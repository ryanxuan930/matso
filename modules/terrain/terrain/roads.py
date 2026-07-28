"""道路網 ingestion — #83（SPEC_FULL §4.3；沿既成道路移動明顯快於越野）。

由 osmnx 匯出的 **graphml**（`MATSO_ROAD_GRAPH_PATH`，如 taiwan_drive.graphml）建立
「h3 cell → 道路等級」索引，落 parquet 供 terrain 服務查詢。

**零額外相依**：graphml 是 XML，以標準庫 `xml.etree.ElementTree.iterparse` **串流**解析
（檔案數百 MB，不可整份載入）。不需 osmnx/networkx/pyosmium。

道路是**疊加**於 terrain_class 之上（林中公路仍分類為 FOREST，供未來遮蔽/掩蔽使用）；
移動速度另以道路等級計（見 contracts/mobility_matrix.json 的 `road`）。
"""

from __future__ import annotations

import contextlib
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from itertools import pairwise
from pathlib import Path

import h3
import pyarrow as pa
import pyarrow.parquet as pq

_NS = "{http://graphml.graphdrawing.org/xmlns}"

# 道路等級優先序（高→低）：一個 cell 有多條路時取**最高等級**（決定該格可達的行車速度）。
ROAD_RANK: dict[str, int] = {
    "motorway": 100,
    "motorway_link": 95,
    "trunk": 90,
    "trunk_link": 85,
    "primary": 80,
    "primary_link": 75,
    "secondary": 70,
    "secondary_link": 65,
    "tertiary": 60,
    "tertiary_link": 55,
    "unclassified": 40,
    "residential": 35,
    "living_street": 30,
    "service": 25,
    "track": 20,
}

_PARQUET_SCHEMA = pa.schema(
    [pa.field("h3_index", pa.string()), pa.field("road_class", pa.string())]
)
# 沿邊取樣間距（度）——約 100m，確保長路段不會在 res-8 格（~0.74km²）之間漏標。
_SAMPLE_STEP_DEG = 0.001


def _norm_class(raw: str | None) -> str | None:
    """OSM `highway` 值正規化（osmnx 可能給 list 字串如 "['primary', 'secondary']"）。"""
    if not raw:
        return None
    text = raw.strip()
    if text.startswith("["):  # osmnx 合併邊 → 取其中等級最高者
        best: str | None = None
        for part in text.strip("[]").replace("'", "").split(","):
            cand = part.strip()
            if cand in ROAD_RANK and (best is None or ROAD_RANK[cand] > ROAD_RANK[best]):
                best = cand
        return best
    return text if text in ROAD_RANK else None


def _parse_linestring(wkt: str) -> list[tuple[float, float]]:
    """`LINESTRING (lng lat, …)` → [(lat,lng), …]。格式異常回空清單（略過該邊）。"""
    try:
        body = wkt[wkt.index("(") + 1 : wkt.rindex(")")]
    except ValueError:
        return []
    pts: list[tuple[float, float]] = []
    for pair in body.split(","):
        bits = pair.split()
        if len(bits) >= 2:
            try:
                pts.append((float(bits[1]), float(bits[0])))  # WKT 是 lng lat
            except ValueError:
                continue
    return pts


def _densify(points: list[tuple[float, float]], step_deg: float) -> Iterator[tuple[float, float]]:
    """沿折線加密取樣，避免長直線段跳過中間的 hex。"""
    if not points:
        return
    yield points[0]
    for (lat0, lng0), (lat1, lng1) in pairwise(points):
        span = max(abs(lat1 - lat0), abs(lng1 - lng0))
        steps = max(1, int(span / step_deg))
        for i in range(1, steps + 1):
            f = i / steps
            yield (lat0 + (lat1 - lat0) * f, lng0 + (lng1 - lng0) * f)


def build_road_index(graphml_path: Path, resolution: int = 8) -> dict[str, str]:
    """串流解析 graphml → {h3_index: road_class}（每格取最高等級）。

    兩階段：先收節點座標（edge 只帶 source/target id），再逐邊取樣其幾何。
    """
    nodes: dict[str, tuple[float, float]] = {}
    keys: dict[str, str] = {}
    out: dict[str, str] = {}

    def _better(cell: str, cls: str) -> None:
        cur = out.get(cell)
        if cur is None or ROAD_RANK.get(cls, 0) > ROAD_RANK.get(cur, 0):
            out[cell] = cls

    # 第一趟：key 定義 + 節點座標
    for _ev, el in ET.iterparse(graphml_path, events=("end",)):
        if el.tag == _NS + "key":
            keys[str(el.get("id"))] = str(el.get("attr.name"))
            el.clear()
        elif el.tag == _NS + "node":
            attrs = {keys.get(str(d.get("key"))): d.text for d in el}
            lat, lng = attrs.get("y"), attrs.get("x")
            nid = el.get("id")
            if nid and lat and lng:
                with contextlib.suppress(ValueError):
                    nodes[nid] = (float(lat), float(lng))
            el.clear()

    # 第二趟：邊幾何 → hex
    for _ev, el in ET.iterparse(graphml_path, events=("end",)):
        if el.tag != _NS + "edge":
            continue
        attrs = {keys.get(str(d.get("key"))): d.text for d in el}
        cls = _norm_class(attrs.get("highway"))
        if cls is None:
            el.clear()
            continue
        geom = attrs.get("geometry")
        pts = _parse_linestring(geom) if geom else []
        if not pts:
            src, dst = el.get("source"), el.get("target")
            pts = [p for p in (nodes.get(str(src)), nodes.get(str(dst))) if p is not None]
        for lat, lng in _densify(pts, _SAMPLE_STEP_DEG):
            _better(h3.latlng_to_cell(lat, lng, resolution), cls)
        el.clear()
    return out


def write_road_index(index: dict[str, str], path: Path) -> int:
    """把道路索引寫成 parquet。回筆數。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    items = sorted(index.items())
    table = pa.table(
        {"h3_index": [k for k, _ in items], "road_class": [v for _, v in items]},
        schema=_PARQUET_SCHEMA,
    )
    pq.write_table(table, path)
    return len(items)


def read_road_index(path: Path) -> dict[str, str]:
    """讀回道路索引（檔案不存在→空 dict，代表「無道路資料」而非錯誤）。"""
    if not path.is_file():
        return {}
    data = pq.read_table(path, schema=_PARQUET_SCHEMA).to_pydict()
    return dict(zip(data["h3_index"], data["road_class"], strict=False))
