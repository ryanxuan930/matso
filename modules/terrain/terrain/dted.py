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
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType

import rasterio
from rasterio.windows import Window

from terrain.config import TerrainSettings
from terrain.errors import DtedFileNotFoundError, OutOfBoundsError

SEA_LEVEL_M = 0.0


@dataclass(frozen=True, slots=True)
class ElevationResult:
    """單點高程查詢結果（對應 contracts/terrain.proto GetElevationResponse）。"""

    elevation_m: float
    water: bool


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
        """依設定（env MATSO_DTED_PATH）開啟。"""
        return cls.open((settings or TerrainSettings()).dted_path)

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
