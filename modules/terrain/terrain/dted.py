"""DTED 載入與點高程查詢（SPEC_FULL §4.1、§4.3 GetElevation）。

設計（O2.1）：
- **windowed 1×1 讀取**而非整檔載入 RAM——真檔在外接硬碟、開發機記憶體有限；
  rasterio 內部 block cache + OS page cache 使熱點查詢達 p99 < 5ms（真檔以
  `realdata` benchmark 驗證，不達標再引入 overview/自建快取）。
- nodata（海域，TW_ALL 約 65%）→ 視為海面：elevation 0.0m、water=True（§4.1）。
- bbox 之外 → OutOfBoundsError（呼叫方錯誤，不靜默當海面）。
- 路徑由 TerrainSettings 注入（env `MATSO_DTED_PATH`，支援外接硬碟）。

執行緒/行程模型：DtedMap 非 thread-safe（rasterio dataset handle）；terrain 服務
（O2.5）單行程持有一份實例即可。
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType

import numpy as np
import rasterio
from rasterio.windows import Window

from terrain.config import TerrainSettings
from terrain.errors import DtedFileNotFoundError, OutOfBoundsError

SEA_LEVEL_M = 0.0
_METERS_PER_DEG_LAT = 111_320.0


@dataclass(frozen=True, slots=True)
class ElevationResult:
    """單點高程查詢結果（對應 contracts/terrain.proto GetElevationResponse）。"""

    elevation_m: float
    water: bool


@dataclass(frozen=True, slots=True)
class WindowSample:
    """一個 bbox 窗口的高程取樣（供 hex cell 聚合、坡度計算，O2.2）。

    values：float64 陣列，nodata（海面）已轉為 np.nan。
    x_res_m / y_res_m：像素在該窗口中心緯度的近似公尺尺寸（坡度換算用）。
    valid_fraction：非 nodata 像素占比（水域判定用）。
    """

    values: np.ndarray
    x_res_m: float
    y_res_m: float
    valid_fraction: float


class DtedMap:
    """單一 DTED GeoTIFF 的查詢介面（WGS84 / EPSG:4326）。

    用 context manager 或顯式 close() 管理 dataset handle：
        with DtedMap.open(path) as dted:
            dted.get_elevation(23.5, 121.0)
    """

    def __init__(self, dataset: rasterio.DatasetReader, path: Path) -> None:
        self._ds = dataset
        self._path = path
        self._nodata: float | None = dataset.nodata
        self._bounds = dataset.bounds

    # ---------------- 生命週期 ----------------

    @classmethod
    def open(cls, path: Path | str) -> DtedMap:
        resolved = Path(path)
        if not resolved.is_file():
            raise DtedFileNotFoundError(
                f"DTED 檔案不存在：{resolved}\n"
                f"若真檔放在外接硬碟：確認硬碟已掛載，並以環境變數指定路徑，例如\n"
                f'  export MATSO_DTED_PATH="/Volumes/<你的硬碟>/TW_ALL.tiff"\n'
                f"開發/測試不需要真檔——測試會用合成夾具（modules/terrain/tests/make_fixture.py）。"
            )
        try:
            dataset = rasterio.open(resolved)
        except rasterio.errors.RasterioIOError as exc:
            raise DtedFileNotFoundError(f"DTED 檔案無法讀取：{resolved}（{exc}）") from exc
        return cls(dataset, resolved)

    @classmethod
    def open_default(cls, settings: TerrainSettings | None = None) -> DtedMap:
        """依設定（env MATSO_DTED_PATH）開啟。檔案不存在則拋 DtedFileNotFoundError。"""
        return cls.open((settings or TerrainSettings()).dted_path)

    @classmethod
    def try_open_default(cls, settings: TerrainSettings | None = None) -> DtedMap | None:
        """依設定開啟；DTED 不可用（外接硬碟未掛載）時回 None 而非拋例外。

        供 terrain 服務 fallback 啟動：無 DTED 時可改用 hex 快取（O2.2）或標記 DEGRADED，
        而非讓整個服務崩潰（使用者需求：未連外接硬碟時系統仍能運作）。
        """
        cfg = settings or TerrainSettings()
        if not cfg.dted_available():
            return None
        return cls.open(cfg.dted_path)

    def close(self) -> None:
        self._ds.close()

    def __enter__(self) -> DtedMap:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    # ---------------- 屬性 ----------------

    @property
    def path(self) -> Path:
        return self._path

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        """(min_lng, min_lat, max_lng, max_lat)，WGS84。"""
        b = self._bounds
        return (b.left, b.bottom, b.right, b.top)

    # ---------------- 查詢 ----------------

    def contains(self, lat: float, lng: float) -> bool:
        b = self._bounds
        return bool(b.left <= lng <= b.right and b.bottom <= lat <= b.top)

    def get_elevation(self, lat: float, lng: float) -> ElevationResult:
        """單點高程。nodata → 海面（0m, water=True）；bbox 外 → OutOfBoundsError。

        注意參數順序為 (lat, lng)——與 contracts/terrain.proto 的 LatLng 一致；
        rasterio 內部座標為 (x=lng, y=lat)，於此處一次轉換。
        """
        if not (math.isfinite(lat) and math.isfinite(lng)):
            raise OutOfBoundsError(f"非法座標：lat={lat}, lng={lng}")
        if not self.contains(lat, lng):
            raise OutOfBoundsError(
                f"({lat}, {lng}) 在 DTED 覆蓋範圍外（bounds={self.bounds}，檔案={self._path}）"
            )
        row, col = self._ds.index(lng, lat)
        # index() 對右/下邊界可能回傳 shape 外一格——夾回有效範圍
        row = min(max(row, 0), self._ds.height - 1)
        col = min(max(col, 0), self._ds.width - 1)
        value = float(self._ds.read(1, window=Window(col, row, 1, 1))[0, 0])
        if self._is_nodata(value):
            return ElevationResult(elevation_m=SEA_LEVEL_M, water=True)
        return ElevationResult(elevation_m=value, water=False)

    def _is_nodata(self, value: float) -> bool:
        if math.isnan(value):  # NaN 一律視為 nodata（含 nodata 標記本身是 NaN 的檔案）
            return True
        nodata = self._nodata
        return nodata is not None and not math.isnan(nodata) and value == nodata

    def sample_bbox(
        self, min_lng: float, min_lat: float, max_lng: float, max_lat: float
    ) -> WindowSample:
        """讀取涵蓋 bbox 的窗口（一次 I/O），nodata→nan。供 hex cell 聚合（O2.2）。

        bbox 部分超出 DTED 範圍時，僅讀取交集（窗口夾回影像內）；完全在界外 → OutOfBoundsError。
        """
        b = self._bounds
        if max_lng < b.left or min_lng > b.right or max_lat < b.bottom or min_lat > b.top:
            raise OutOfBoundsError(
                f"bbox ({min_lng},{min_lat},{max_lng},{max_lat}) 與 DTED 範圍無交集"
                f"（{self.bounds}）"
            )
        window = self._ds.window(min_lng, min_lat, max_lng, max_lat)
        full = Window(0, 0, self._ds.width, self._ds.height)
        window = window.round_offsets().round_lengths().intersection(full)
        if window.width < 1 or window.height < 1:
            window = Window(
                min(max(int(window.col_off), 0), self._ds.width - 1),
                min(max(int(window.row_off), 0), self._ds.height - 1),
                1,
                1,
            )
        raw = self._ds.read(1, window=window).astype(np.float64)
        values = self._mask_nodata(raw)
        valid = np.count_nonzero(~np.isnan(values))
        valid_fraction = valid / values.size if values.size else 0.0

        center_lat = (min_lat + max_lat) / 2.0
        x_res_deg, y_res_deg = self._ds.res
        x_res_m = x_res_deg * _METERS_PER_DEG_LAT * math.cos(math.radians(center_lat))
        y_res_m = y_res_deg * _METERS_PER_DEG_LAT
        return WindowSample(
            values=values,
            x_res_m=abs(x_res_m),
            y_res_m=abs(y_res_m),
            valid_fraction=valid_fraction,
        )

    def _mask_nodata(self, arr: np.ndarray) -> np.ndarray:
        out = arr.copy()
        out[np.isnan(out)] = np.nan
        nodata = self._nodata
        if nodata is not None and not math.isnan(nodata):
            out[arr == nodata] = np.nan
        return out

    def line_sampler(
        self, min_lng: float, min_lat: float, max_lng: float, max_lat: float
    ) -> Callable[[float, float], float]:
        """讀取一次涵蓋 bbox 的窗口，回傳純記憶體高程取樣函式（LOS/Viewshed 用；nodata→0 海面）。

        LOS 沿線可能上百點——整條線 bbox 一次讀入記憶體，避免逐點 rasterio I/O（p99 關鍵）。
        取樣為最近像素；水域（nodata）視為海面 0m（視線掠過海面）。bbox 界外部分夾回影像。
        """
        pad = 3  # 像素邊界緩衝（大圓微彎 / 邊界像素）
        x_res_deg, y_res_deg = self._ds.res
        window = self._ds.window(
            min_lng - pad * x_res_deg,
            min_lat - pad * y_res_deg,
            max_lng + pad * x_res_deg,
            max_lat + pad * y_res_deg,
        )
        full = Window(0, 0, self._ds.width, self._ds.height)
        window = window.round_offsets().round_lengths().intersection(full)
        if window.width < 1 or window.height < 1:
            window = Window(
                min(max(int(window.col_off), 0), self._ds.width - 1),
                min(max(int(window.row_off), 0), self._ds.height - 1),
                1,
                1,
            )
        row_off, col_off = int(window.row_off), int(window.col_off)
        arr = self._ds.read(1, window=window)
        height, width = arr.shape
        nodata = self._nodata
        nodata_valid = nodata is not None and not math.isnan(nodata)

        def sample(lat: float, lng: float) -> float:
            row, col = self._ds.index(lng, lat)
            lr = min(max(row - row_off, 0), height - 1)
            lc = min(max(col - col_off, 0), width - 1)
            value = float(arr[lr, lc])
            if math.isnan(value) or (nodata_valid and value == nodata):
                return SEA_LEVEL_M
            return value

        return sample
