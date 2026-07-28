"""土地利用分類 ingestion — #89（SPEC_FULL §4.2；terrain_class 由真實土地利用而非坡度猜測）。

背景：`hexgrid.classify_terrain` 只用坡度+高程，**產不出 URBAN/FOREST**（原始碼註明「需土地利用
資料，Phase 2」）。實測即因此把台北市區判成 `WETLAND`（低海拔又平坦）。本模組由 OSM PBF 的
landuse/natural/building 圖層建立「h3 cell → terrain_class」索引，疊加修正之。

**相依**：`osmium`（pyosmium）——僅**離線預計算**需要；服務執行期只讀 parquet，不載入 osmium。

**兩種取樣**：
- 面狀土地利用（forest/residential/farmland…）→ `h3shape_to_cells` 填滿多邊形。
- 建物 → 單棟遠小於 res-8 格（~0.74km²），填不出格；改以**centroid 計數**，
  一格建物數 ≥ `_URBAN_BUILDING_MIN` 即判 URBAN（市區密度代理）。
"""

from __future__ import annotations

import logging
from collections import defaultdict
from pathlib import Path

import h3
import pyarrow as pa
import pyarrow.parquet as pq

_LOG = logging.getLogger("terrain.landuse")

# 一格內建物數達此值 → 視為市區（res 8 ≈ 0.74 km²）。
_URBAN_BUILDING_MIN = 12

# OSM tag → terrain_class。鍵為 (tag_key, tag_value)。
_TAG_CLASS: dict[tuple[str, str], str] = {}
for _v in (
    "residential",
    "industrial",
    "commercial",
    "retail",
    "military",
    "garages",
    "education",
    "institutional",
    "construction",
    "brownfield",
):
    _TAG_CLASS[("landuse", _v)] = "URBAN"
for _v in ("forest", "orchard", "plant_nursery", "vineyard"):
    _TAG_CLASS[("landuse", _v)] = "FOREST"
for _v in ("wood", "scrub"):
    _TAG_CLASS[("natural", _v)] = "FOREST"
for _v in ("wetland", "marsh", "mangrove"):
    _TAG_CLASS[("natural", _v)] = "WETLAND"
for _v in ("basin", "salt_pond", "aquaculture"):
    _TAG_CLASS[("landuse", _v)] = "WETLAND"
for _v in ("water", "bay", "strait"):
    _TAG_CLASS[("natural", _v)] = "WATER"
_TAG_CLASS[("landuse", "reservoir")] = "WATER"
for _v in (
    "farmland",
    "meadow",
    "grass",
    "farmyard",
    "allotments",
    "greenfield",
    "village_green",
    "recreation_ground",
    "cemetery",
):
    _TAG_CLASS[("landuse", _v)] = "GRASSLAND"
for _v in ("grassland", "heath", "fell"):
    _TAG_CLASS[("natural", _v)] = "GRASSLAND"
for _v in ("bare_rock", "scree", "sand", "beach", "shingle", "cliff"):
    _TAG_CLASS[("natural", _v)] = "BARREN"
for _v in ("quarry", "landfill"):
    _TAG_CLASS[("landuse", _v)] = "BARREN"

# 同格多種土地利用時的優先序（高→低）。WATER 最高：誤把水域當陸地會讓單位「開進湖裡」，
# 寧可保守（有橋則由 #83 道路疊加另行加速，不受此限）。
_CLASS_RANK: dict[str, int] = {
    "WATER": 60,
    "URBAN": 50,
    "WETLAND": 40,
    "FOREST": 30,
    "BARREN": 20,
    "GRASSLAND": 10,
}

_PARQUET_SCHEMA = pa.schema(
    [pa.field("h3_index", pa.string()), pa.field("terrain_class", pa.string())]
)


def _class_of(tags: object) -> str | None:
    """由 OSM tags 取土地利用類別（取優先序最高者）。無對應 → None。"""
    best: str | None = None
    for key in ("landuse", "natural"):
        val = tags.get(key) if hasattr(tags, "get") else None
        if not val:
            continue
        cls = _TAG_CLASS.get((key, str(val)))
        if cls and (best is None or _CLASS_RANK[cls] > _CLASS_RANK[best]):
            best = cls
    return best


def _ring_latlng(ring: object) -> list[tuple[float, float]]:
    """osmium outer ring → [(lat,lng), …]（去除重複收尾點）。"""
    pts: list[tuple[float, float]] = []
    for node in ring:  # type: ignore[attr-defined]
        try:
            pts.append((float(node.lat), float(node.lon)))
        except Exception:
            continue
    if len(pts) > 1 and pts[0] == pts[-1]:
        pts.pop()
    return pts


def build_landuse_index(pbf_path: Path, resolution: int = 8) -> dict[str, str]:
    """串流 OSM PBF → {h3_index: terrain_class}。需 `osmium`（僅離線預計算）。"""
    import osmium

    best: dict[str, str] = {}
    buildings: dict[str, int] = defaultdict(int)

    def _better(cell: str, cls: str) -> None:
        cur = best.get(cell)
        if cur is None or _CLASS_RANK[cls] > _CLASS_RANK.get(cur, 0):
            best[cell] = cls

    fp = (
        osmium.FileProcessor(str(pbf_path))
        .with_areas()
        .with_filter(osmium.filter.KeyFilter("landuse", "natural", "building"))
    )
    areas = 0
    for obj in fp:
        if not isinstance(obj, osmium.osm.Area):
            continue
        tags = obj.tags
        cls = _class_of(tags)
        if cls is None:
            # 無 landuse/natural → 若為建物，記其 centroid 供市區密度判定。
            if "building" in tags:
                try:
                    ring = next(iter(obj.outer_rings()))
                    pts = _ring_latlng(ring)
                except Exception:
                    pts = []
                if pts:
                    lat = sum(p[0] for p in pts) / len(pts)
                    lng = sum(p[1] for p in pts) / len(pts)
                    buildings[h3.latlng_to_cell(lat, lng, resolution)] += 1
            continue
        areas += 1
        try:
            for ring in obj.outer_rings():
                pts = _ring_latlng(ring)
                if len(pts) < 3:
                    continue
                cells = h3.h3shape_to_cells(h3.LatLngPoly(pts), resolution)
                if not cells:  # 多邊形小於一格 → 取其中心格
                    lat = sum(p[0] for p in pts) / len(pts)
                    lng = sum(p[1] for p in pts) / len(pts)
                    cells = [h3.latlng_to_cell(lat, lng, resolution)]
                for c in cells:
                    _better(c, cls)
        except Exception:  # 幾何異常（自相交等）→ 略過該面，不中斷整批
            continue

    # 建物密度 → URBAN（覆蓋非 URBAN 的既有判定）。
    urban = 0
    for cell, count in buildings.items():
        if count >= _URBAN_BUILDING_MIN:
            _better(cell, "URBAN")
            urban += 1
    _LOG.info("土地利用：%d 面、%d 格；建物密度判定 URBAN %d 格", areas, len(best), urban)
    return best


def write_landuse_index(index: dict[str, str], path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    items = sorted(index.items())
    table = pa.table(
        {"h3_index": [k for k, _ in items], "terrain_class": [v for _, v in items]},
        schema=_PARQUET_SCHEMA,
    )
    pq.write_table(table, path)
    return len(items)


def read_landuse_index(path: Path) -> dict[str, str]:
    """讀回土地利用索引（檔案不存在→空 dict＝無資料，terrain_class 維持坡度推導）。"""
    if not path.is_file():
        return {}
    data = pq.read_table(path, schema=_PARQUET_SCHEMA).to_pydict()
    return dict(zip(data["h3_index"], data["terrain_class"], strict=False))
