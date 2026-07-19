"""H3 六角網格：由 DTED 預計算 cell 屬性，落 parquet 快取；查詢由快取讀取（SPEC §4.2）。

架構（含使用者的外接硬碟 fallback 需求）：
- **預計算**（HexGridBuilder + precompute CLI）：讀 DTED（需外接硬碟一次）→ 每 cell 窗口聚合
  → parquet 快取。
- **查詢**（HexGridCache.get_cell_batch）：只讀 parquet 快取——快取放本地即可讓日常查詢
  完全不依賴外接硬碟。

cell 屬性對應 contracts/terrain.proto CellInfo：
  h3_index, center(lat,lng), elevation_mean, elevation_max, slope_deg, terrain_class,
  water, mobility_cost。

res 8 為戰術預設（~0.74 km²/cell）；scenario 可宣告 res 7–9。
"""

from __future__ import annotations

import enum
import math
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

import h3
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from terrain.dted import DtedMap, WindowSample

# terrain_class 門檻（Phase 1 只用坡度+高程；URBAN/FOREST 需土地利用資料，Phase 2）
_MOUNTAIN_ELEV_M = 1000.0
_MOUNTAIN_SLOPE_DEG = 25.0
_BARREN_SLOPE_DEG = 12.0
_WETLAND_MAX_ELEV_M = 5.0
_WATER_NODATA_FRACTION = 0.5  # cell 內海面(nodata)像素占比 ≥ 此值 → water


class TerrainClass(enum.StrEnum):
    URBAN = "URBAN"
    FOREST = "FOREST"
    GRASSLAND = "GRASSLAND"
    WETLAND = "WETLAND"
    BARREN = "BARREN"
    WATER = "WATER"
    MOUNTAIN = "MOUNTAIN"


@dataclass(frozen=True, slots=True)
class CellAttributes:
    h3_index: str
    center_lat: float
    center_lng: float
    elevation_mean: float
    elevation_max: float
    slope_deg: float
    terrain_class: TerrainClass
    water: bool
    mobility_cost: float


def classify_terrain(elevation_mean: float, slope_deg: float, water: bool) -> TerrainClass:
    """Phase 1 地形分類（坡度+高程規則）。

    誠實限制：URBAN/FOREST 無法只憑高程/坡度分辨，需 taiwan.osm.pbf 土地利用（Phase 2）——
    Phase 1 這類地表會落在 GRASSLAND/BARREN。
    """
    if water:
        return TerrainClass.WATER
    if elevation_mean >= _MOUNTAIN_ELEV_M or slope_deg >= _MOUNTAIN_SLOPE_DEG:
        return TerrainClass.MOUNTAIN
    if slope_deg >= _BARREN_SLOPE_DEG:
        return TerrainClass.BARREN
    if elevation_mean <= _WETLAND_MAX_ELEV_M:
        return TerrainClass.WETLAND
    return TerrainClass.GRASSLAND


def base_mobility_cost(terrain_class: TerrainClass, slope_deg: float) -> float:
    """cell 層的基礎通行阻力（profile 無關；O2.4 A* 再結合 mobility_matrix 的 profile×class）。

    以坡度為主：平地 1.0，坡度越陡越貴；WATER 給高懲罰佔位（實際可否通行由 profile 決定）。
    """
    if terrain_class is TerrainClass.WATER:
        return 999.0
    return 1.0 + slope_deg / 15.0


def _aggregate(sample: WindowSample) -> tuple[float, float, float, bool]:
    """由窗口取樣算 (elevation_mean, elevation_max, slope_deg, water)。"""
    water = sample.valid_fraction < _WATER_NODATA_FRACTION  # 海面(nodata)占多數 → 水域
    values = sample.values
    valid = values[~np.isnan(values)]
    if water or valid.size == 0:
        return (0.0, 0.0, 0.0, True)
    elevation_mean = float(np.mean(valid))
    elevation_max = float(np.max(valid))
    slope_deg = _slope_deg(values, sample.x_res_m, sample.y_res_m)
    return (elevation_mean, elevation_max, slope_deg, False)


def _slope_deg(values: np.ndarray, x_res_m: float, y_res_m: float) -> float:
    """由窗口高程梯度算平均坡度（度）。nodata(nan) 以最近有效值近似（np.gradient 需連續）。"""
    if values.shape[0] < 2 or values.shape[1] < 2:
        return 0.0
    filled = _fill_nan(values)
    dz_dy, dz_dx = np.gradient(filled)
    gx = dz_dx / x_res_m if x_res_m > 0 else np.zeros_like(dz_dx)
    gy = dz_dy / y_res_m if y_res_m > 0 else np.zeros_like(dz_dy)
    slope_rad = np.arctan(np.hypot(gx, gy))
    # 只在原本有效的像素上取平均，避免填補區污染
    mask = ~np.isnan(values)
    if not np.any(mask):
        return 0.0
    return float(np.degrees(np.mean(slope_rad[mask])))


def _fill_nan(values: np.ndarray) -> np.ndarray:
    if not np.any(np.isnan(values)):
        return values
    filled = values.copy()
    mean = np.nanmean(values)
    filled[np.isnan(filled)] = 0.0 if math.isnan(mean) else mean
    return filled


class HexGridBuilder:
    """由 DTED 建構 cell 屬性。查詢端不需要它（見 HexGridCache）。"""

    def __init__(self, dted: DtedMap) -> None:
        self._dted = dted

    def build_cell(self, h3_index: str) -> CellAttributes:
        lat, lng = h3.cell_to_latlng(h3_index)
        boundary = h3.cell_to_boundary(h3_index)  # [(lat,lng), ...]
        lats = [p[0] for p in boundary]
        lngs = [p[1] for p in boundary]
        sample = self._dted.sample_bbox(min(lngs), min(lats), max(lngs), max(lats))
        elevation_mean, elevation_max, slope_deg, water = _aggregate(sample)
        terrain_class = classify_terrain(elevation_mean, slope_deg, water)
        return CellAttributes(
            h3_index=h3_index,
            center_lat=lat,
            center_lng=lng,
            elevation_mean=elevation_mean,
            elevation_max=elevation_max,
            slope_deg=slope_deg,
            terrain_class=terrain_class,
            water=water,
            mobility_cost=base_mobility_cost(terrain_class, slope_deg),
        )

    def build_region(
        self, bbox: tuple[float, float, float, float], resolution: int
    ) -> Iterator[CellAttributes]:
        """bbox=(min_lng,min_lat,max_lng,max_lat)，涵蓋範圍內所有 H3 cell 逐一建構。"""
        if not 7 <= resolution <= 9:
            raise ValueError(f"resolution 必須在 7–9（SPEC §4.2），收到 {resolution}")
        min_lng, min_lat, max_lng, max_lat = bbox
        poly = h3.LatLngPoly(
            [
                (min_lat, min_lng),
                (min_lat, max_lng),
                (max_lat, max_lng),
                (max_lat, min_lng),
            ]
        )
        for h3_index in h3.polygon_to_cells(poly, resolution):
            yield self.build_cell(h3_index)


_PARQUET_SCHEMA = pa.schema(
    [
        ("h3_index", pa.string()),
        ("center_lat", pa.float64()),
        ("center_lng", pa.float64()),
        ("elevation_mean", pa.float32()),
        ("elevation_max", pa.float32()),
        ("slope_deg", pa.float32()),
        ("terrain_class", pa.string()),
        ("water", pa.bool_()),
        ("mobility_cost", pa.float32()),
    ]
)


def write_parquet(cells: Iterable[CellAttributes], path: Path) -> int:
    """把 cell 屬性寫入 parquet 快取，回傳寫入筆數。"""
    rows = list(cells)
    path.parent.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pydict(
        {
            "h3_index": [c.h3_index for c in rows],
            "center_lat": [c.center_lat for c in rows],
            "center_lng": [c.center_lng for c in rows],
            "elevation_mean": [c.elevation_mean for c in rows],
            "elevation_max": [c.elevation_max for c in rows],
            "slope_deg": [c.slope_deg for c in rows],
            "terrain_class": [str(c.terrain_class) for c in rows],
            "water": [c.water for c in rows],
            "mobility_cost": [c.mobility_cost for c in rows],
        },
        schema=_PARQUET_SCHEMA,
    )
    pq.write_table(table, path)
    return len(rows)


class HexGridCache:
    """parquet 快取的查詢端。**不需 DTED / 外接硬碟**——載入後純記憶體查詢。"""

    def __init__(self, cells: dict[str, CellAttributes]) -> None:
        self._cells = cells

    @classmethod
    def open(cls, path: Path) -> HexGridCache:
        table = pq.read_table(path, schema=_PARQUET_SCHEMA)
        data = table.to_pydict()
        cells: dict[str, CellAttributes] = {}
        for i, h3_index in enumerate(data["h3_index"]):
            cells[h3_index] = CellAttributes(
                h3_index=h3_index,
                center_lat=data["center_lat"][i],
                center_lng=data["center_lng"][i],
                elevation_mean=data["elevation_mean"][i],
                elevation_max=data["elevation_max"][i],
                slope_deg=data["slope_deg"][i],
                terrain_class=TerrainClass(data["terrain_class"][i]),
                water=data["water"][i],
                mobility_cost=data["mobility_cost"][i],
            )
        return cls(cells)

    def __len__(self) -> int:
        return len(self._cells)

    def get_cell(self, h3_index: str) -> CellAttributes | None:
        return self._cells.get(h3_index)

    def get_cell_batch(self, h3_indexes: Iterable[str]) -> dict[str, CellAttributes]:
        """批次查詢；缺漏的 h3_index 不出現在回傳 dict（呼叫方自行判斷）。"""
        return {h: self._cells[h] for h in h3_indexes if h in self._cells}
