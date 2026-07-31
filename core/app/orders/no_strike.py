"""禁射區（No-Strike / Restricted-Fire）解析 — WP-A3（SPEC_V2 §6 WP-A3）。

兩個資料來源合流成同一份格集：
1. 想定/白軍宣告的 `WargameSession.noStrikeZones`（宣告式幾何）。
2. 地圖標註 `MapFeature.attributes.zone_class`（白軍在 COP 上圈的區）。

`zones_to_cells` 是**純函數**（不碰 DB），DB 讀取集中在 `load_no_strike_zones`。
與 `precheck._load_arc_obstacles` 同一紀律：MapFeature 可被後端讀來參與物理/規則判定。

**座標順序陷阱**：本 repo 幾何一律 GeoJSON 的 `[lng, lat]`，而 `h3.LatLngPoly` 吃 `(lat, lng)`。
兩者相反，轉換點集中在 `_ring_to_cells` 一處，其餘地方不得自行轉。
"""

from __future__ import annotations

import contextlib
import enum
import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import h3
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import MapFeature, WargameSession

# 與偵測/交戰/移動同級（全 repo 的戰術解析度）。
NO_STRIKE_H3_RES = 8
# 圓形取樣邊數：48 邊在 res-8 下的邊長誤差遠小於一格，足夠且便宜（同前端 genCircle 的 steps）。
_CIRCLE_STEPS = 48
_EARTH_R_M = 6378137.0


class ZoneClass(enum.StrEnum):
    """禁射級別。

    NO_STRIKE：硬阻擋——AI 令被剔除且整批不接受；人類令被 precheck 拒絕（不可 override）。
    RESTRICTED_FIRE：不硬擋——AI 令保留但升白軍確認；人類須明確 override，且 override 記 Ledger。
    """

    NO_STRIKE = "NO_STRIKE"
    RESTRICTED_FIRE = "RESTRICTED_FIRE"


@dataclass(frozen=True, slots=True)
class NoStrikeCells:
    """一局的禁射格集（依級別分開——兩者的處置不同，合在一起就分不出該擋還是該升級）。"""

    no_strike: frozenset[str] = frozenset()
    restricted: frozenset[str] = frozenset()
    # 格 → 區名。**拒絕訊息說得出是哪一區，檢討會才問得下去**：
    # 「被禁射區擋下」與「被『虎尾鎮立醫院』擋下」對受訓者是兩種不同的回饋，
    # 前者只知道規則存在，後者才知道自己差點打到什麼。
    # 重疊時保留第一個宣告的名字（從嚴不從名——級別判定與名字無關）。
    names: Mapping[str, str] = field(default_factory=dict)

    @property
    def any_cells(self) -> bool:
        return bool(self.no_strike or self.restricted)

    def name_at(self, lat: float, lng: float) -> str:
        """該座標落在哪一個宣告區。查不到 → 空字串（呼叫端自行退回泛稱）。"""
        return self.names.get(h3.latlng_to_cell(lat, lng, NO_STRIKE_H3_RES), "")

    def classify(self, cell: str) -> ZoneClass | None:
        """該格屬於哪一級。NO_STRIKE 優先（重疊時從嚴）。"""
        if cell in self.no_strike:
            return ZoneClass.NO_STRIKE
        if cell in self.restricted:
            return ZoneClass.RESTRICTED_FIRE
        return None

    def classify_latlng(self, lat: float, lng: float) -> ZoneClass | None:
        return self.classify(h3.latlng_to_cell(lat, lng, NO_STRIKE_H3_RES))


def _ring_to_cells(ring: list[list[float]]) -> set[str]:
    """開放環（GeoJSON [lng,lat]）→ 覆蓋格集。

    `h3.LatLngPoly` 要 (lat, lng) 且環需閉合；另補上各頂點所在格——極小的區域可能一格都框不進
    （polygon_to_cells 只收「格心落在多邊形內」者），漏掉會讓小型禁射區形同虛設。
    """
    pts = [(float(p[1]), float(p[0])) for p in ring if len(p) >= 2]
    if len(pts) < 3:
        return set()
    if pts[0] != pts[-1]:
        pts.append(pts[0])
    cells = {h3.latlng_to_cell(lat, lng, NO_STRIKE_H3_RES) for lat, lng in pts}
    # 退化/自交多邊形：至少保住頂點格，不讓整個區失效（禁射區寧可少保護也不能整個消失）。
    with contextlib.suppress(ValueError, TypeError):
        cells |= set(h3.polygon_to_cells(h3.LatLngPoly(pts), NO_STRIKE_H3_RES))
    return cells


def _circle_ring(center: list[float], radius_m: float) -> list[list[float]]:
    """圓心 + 半徑 → 近似圓環（[lng,lat]）。緯度方向的度數換算隨緯度壓縮。"""
    lng, lat = float(center[0]), float(center[1])
    d_lat = (radius_m / _EARTH_R_M) * (180.0 / math.pi)
    d_lng = d_lat / max(math.cos(math.radians(lat)), 1e-6)
    return [
        [
            lng + d_lng * math.cos(2 * math.pi * i / _CIRCLE_STEPS),
            lat + d_lat * math.sin(2 * math.pi * i / _CIRCLE_STEPS),
        ]
        for i in range(_CIRCLE_STEPS)
    ]


def _geometry_to_cells(geometry: Any) -> set[str]:
    """單一 zone 幾何 → 格集。認得 polygon（ring）與 circle（center+radius_m）。"""
    if not isinstance(geometry, dict):
        return set()
    gtype = str(geometry.get("type", "")).lower()
    if gtype == "circle":
        center, radius = geometry.get("center"), geometry.get("radius_m")
        if isinstance(center, list) and len(center) >= 2 and isinstance(radius, int | float):
            return _ring_to_cells(_circle_ring(center, float(radius)))
        return set()
    if gtype == "polygon":
        ring = geometry.get("ring")
        return _ring_to_cells(ring) if isinstance(ring, list) else set()
    return set()


def zones_to_cells(zones: Any) -> NoStrikeCells:
    """宣告式 zone 清單 → 依級別分組的格集（純函數）。

    寬容解析：壞掉的單一 zone 略過而非整批失效——禁射區是安全機制，
    「一筆打錯就整個保護消失」比「少保護一區」危險得多。
    """
    if not isinstance(zones, list):
        return NoStrikeCells()
    hard: set[str] = set()
    soft: set[str] = set()
    names: dict[str, str] = {}
    for z in zones:
        if not isinstance(z, dict):
            continue
        cells = _geometry_to_cells(z.get("geometry"))
        if not cells:
            continue
        label = str(z.get("name") or z.get("label") or "").strip()
        if label:
            # 先宣告者優先——重疊時不覆寫，才不會讓名字隨 zone 清單的順序跳動。
            for cell in cells:
                names.setdefault(cell, label)
        if str(z.get("zone_class", "")).upper() == ZoneClass.RESTRICTED_FIRE.value:
            soft |= cells
        else:  # 未知/缺值一律從嚴視為 NO_STRIKE（安全預設）
            hard |= cells
    return NoStrikeCells(no_strike=frozenset(hard), restricted=frozenset(soft - hard), names=names)


def _feature_zones(db: Session, session_id: str) -> list[dict[str, Any]]:
    """白軍在 COP 圈的禁射區：`MapFeature.attributes.zone_class` 有值的面。

    刻意**不限 owner_faction**：禁射區是全局的人道/交戰規則約束，不是某軍的私有標註——
    某軍把醫院圈起來，敵軍的 AI 也該受同一條規則約束。
    """
    rows = db.scalars(
        select(MapFeature).where(
            MapFeature.session_id == session_id, MapFeature.geometry_type == "POLYGON"
        )
    ).all()
    out: list[dict[str, Any]] = []
    for f in rows:
        attrs = f.attributes if isinstance(f.attributes, dict) else {}
        zone_class = attrs.get("zone_class")
        if not isinstance(zone_class, str) or not zone_class:
            continue
        out.append(
            {
                "name": f.label or f.id,
                "zone_class": zone_class,
                "geometry": {"type": "polygon", "ring": f.geometry},
            }
        )
    return out


def load_no_strike_cells(db: Session, session_id: str) -> NoStrikeCells:
    """該局的禁射格集：想定/白軍宣告 + 地圖標註，兩來源合流。

    無宣告 → 空集合（既有局零行為變更）。每次呼叫重算（白軍可局中增修，快取會讓變更不生效；
    zone 數量是個位數、polygon_to_cells 成本遠低於同路徑上的 terrain gRPC）。
    """
    row = db.get(WargameSession, session_id)
    declared = row.no_strike_zones if row is not None else None
    zones: list[Any] = list(declared) if isinstance(declared, list) else []
    zones.extend(_feature_zones(db, session_id))
    return zones_to_cells(zones)
