"""移動路徑成本 / 耗損模型（#28）——純同步純函數（HOW_TO §3、§4.2）。

給定一條折線路徑（waypoints，[lng,lat]）與地圖上的阻礙標註（MapFeature），計算：
  * route_distance_m：全程大圓距離。
  * classify_crossings：路徑穿越哪些「不可通行」標註（障礙/建築/不可通行地形）。
  * estimate_route：距離 + 估計 tick 數 + 油耗 + 基礎耗損 + 是否可行 / 是否強穿。
  * forced_extra_attrition：強行穿越不可通行標註時，經注入的 DeterministicRNG 擲出的
    「額外隨機耗損」（戰力點）。**唯一隨機來源＝注入的 rng**，確保 golden replay 可重現。

紅線遵循：不碰牆鐘 / 不用裸 random / 不碰 DB / 不做 RPC。幾何以平面近似（tactical 尺度誤差
可忽略）；距離用 haversine。阻礙判定純拓樸（線段是否穿越環 / 線 / 影響圓），與投影尺度無關。
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

_EARTH_R_M = 6_371_000.0

# 預設「不可通行」標註類別（FEATURE_KINDS 對映）：障礙、建築。
# TERRAIN 類別再看 attributes.impassable（如水域/沼澤）才算阻礙。
IMPASSABLE_KINDS: frozenset[str] = frozenset({"OBSTACLE", "BUILDING"})
_CONDITIONAL_KINDS: frozenset[str] = frozenset({"TERRAIN"})


@dataclass(frozen=True, slots=True)
class Obstacle:
    """一個阻礙標註的幾何摘要（由 MapFeature 攤平；純資料）。"""

    feature_id: str
    kind: str
    geometry_type: str  # POINT / LINE / POLYGON
    coords: tuple[tuple[float, float], ...]  # [(lng,lat), …]；POINT 為單點
    label: str | None = None
    radius_m: float = 0.0  # POINT 影響半徑（influence_radius_m）


@dataclass(frozen=True, slots=True)
class Crossing:
    """路徑穿越一個阻礙的紀錄（供前端逐項顯示 + 執行期扣耗損）。"""

    feature_id: str
    kind: str
    label: str | None
    entry_frac: float  # 0..1，沿全程哪個比例處進入該阻礙（供排序 / 動畫）


@dataclass(frozen=True, slots=True)
class RouteEstimate:
    """一條路徑的成本估計（純確定性；不含隨機強穿耗損）。"""

    distance_m: float
    duration_ticks: int
    fuel_cost: float
    base_attrition: float  # 里程磨耗導致的戰力損失（確定性）
    feasible: bool  # 無強穿＝可行；有阻礙＝需強穿（仍可下令，但代價高）
    forced: bool  # 是否需強穿至少一個阻礙
    crossings: tuple[Crossing, ...] = field(default_factory=tuple)


def is_impassable(kind: str, attributes: dict[str, Any] | None) -> bool:
    """該類別 + 屬性是否視為不可通行（障礙/建築；或標為 impassable 的地形）。"""
    if kind in IMPASSABLE_KINDS:
        return True
    if kind in _CONDITIONAL_KINDS and isinstance(attributes, dict):
        return bool(attributes.get("impassable"))
    return False


def obstacle_from_feature(feat: dict[str, Any]) -> Obstacle | None:
    """由 MapFeature dict 攤平成 Obstacle；非阻礙 / 幾何壞掉 → None。"""
    kind = str(feat.get("kind") or "")
    if not is_impassable(kind, feat.get("attributes")):
        return None
    gtype = str(feat.get("geometry_type") or "").upper()
    geom = feat.get("geometry")
    coords = _coerce_coords(gtype, geom)
    if not coords:
        return None
    radius = feat.get("influence_radius_m")
    return Obstacle(
        feature_id=str(feat.get("id") or ""),
        kind=kind,
        geometry_type=gtype,
        coords=coords,
        label=(str(feat["label"]) if feat.get("label") else None),
        radius_m=float(radius) if isinstance(radius, (int, float)) else 0.0,
    )


def _coerce_coords(gtype: str, geom: Any) -> tuple[tuple[float, float], ...]:
    try:
        if gtype == "POINT":
            return ((float(geom[0]), float(geom[1])),)
        if gtype in ("LINE", "POLYGON"):
            pts = tuple((float(p[0]), float(p[1])) for p in geom if len(p) >= 2)
            return pts if len(pts) >= 2 else ()
    except (TypeError, ValueError, IndexError, KeyError):
        return ()
    return ()


# ---------------- 幾何 ----------------


def haversine_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    """兩點（[lng,lat]）大圓距離（公尺）。"""
    lng1, lat1 = a
    lng2, lat2 = b
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lng2 - lng1)
    h = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * _EARTH_R_M * math.asin(min(1.0, math.sqrt(h)))


def route_distance_m(waypoints: Sequence[tuple[float, float]]) -> float:
    """折線總長（公尺）。少於 2 點 → 0。"""
    if len(waypoints) < 2:
        return 0.0
    return sum(haversine_m(waypoints[i], waypoints[i + 1]) for i in range(len(waypoints) - 1))


def _orient(a: tuple[float, float], b: tuple[float, float], c: tuple[float, float]) -> float:
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _on_seg(a: tuple[float, float], b: tuple[float, float], p: tuple[float, float]) -> bool:
    return min(a[0], b[0]) <= p[0] <= max(a[0], b[0]) and min(a[1], b[1]) <= p[1] <= max(a[1], b[1])


def segments_intersect(
    p1: tuple[float, float],
    p2: tuple[float, float],
    p3: tuple[float, float],
    p4: tuple[float, float],
) -> bool:
    """線段 p1p2 與 p3p4 是否相交（含共線交疊 / 端點觸碰）。標準 orientation 判定。"""
    d1 = _orient(p3, p4, p1)
    d2 = _orient(p3, p4, p2)
    d3 = _orient(p1, p2, p3)
    d4 = _orient(p1, p2, p4)
    if ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0)):
        return True
    return (
        (d1 == 0 and _on_seg(p3, p4, p1))
        or (d2 == 0 and _on_seg(p3, p4, p2))
        or (d3 == 0 and _on_seg(p1, p2, p3))
        or (d4 == 0 and _on_seg(p1, p2, p4))
    )


def point_in_ring(pt: tuple[float, float], ring: Sequence[tuple[float, float]]) -> bool:
    """射線法：點是否在多邊形環內（環可未閉合，自動視首尾相連）。"""
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = ring[i]
        xj, yj = ring[j]
        if ((yi > pt[1]) != (yj > pt[1])) and (
            pt[0] < (xj - xi) * (pt[1] - yi) / ((yj - yi) or 1e-12) + xi
        ):
            inside = not inside
        j = i
    return inside


def _dist_point_to_segment_m(
    p: tuple[float, float], a: tuple[float, float], b: tuple[float, float]
) -> float:
    """點到線段最短距離（公尺，平面近似 + cos-lat 修正）。"""
    lat0 = math.radians((a[1] + b[1]) / 2)
    kx = math.cos(lat0) * math.pi / 180 * _EARTH_R_M  # 每經度公尺
    ky = math.pi / 180 * _EARTH_R_M  # 每緯度公尺
    ax, ay = a[0] * kx, a[1] * ky
    bx, by = b[0] * kx, b[1] * ky
    px, py = p[0] * kx, p[1] * ky
    dx, dy = bx - ax, by - ay
    seg2 = dx * dx + dy * dy
    if seg2 <= 1e-9:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / seg2))
    cx, cy = ax + t * dx, ay + t * dy
    return math.hypot(px - cx, py - cy)


def _segment_hits_obstacle(s0: tuple[float, float], s1: tuple[float, float], obs: Obstacle) -> bool:
    """路徑線段 s0→s1 是否觸及該阻礙（依幾何型別）。"""
    if obs.geometry_type == "POLYGON":
        ring = obs.coords
        if point_in_ring(s0, ring) or point_in_ring(s1, ring):
            return True
        n = len(ring)
        return any(segments_intersect(s0, s1, ring[i], ring[(i + 1) % n]) for i in range(n))
    if obs.geometry_type == "LINE":
        return any(
            segments_intersect(s0, s1, obs.coords[i], obs.coords[i + 1])
            for i in range(len(obs.coords) - 1)
        )
    if obs.geometry_type == "POINT" and obs.radius_m > 0.0:
        return _dist_point_to_segment_m(obs.coords[0], s0, s1) <= obs.radius_m
    return False


def classify_crossings(
    waypoints: Sequence[tuple[float, float]], obstacles: Iterable[Obstacle]
) -> list[Crossing]:
    """路徑穿越哪些阻礙。每個阻礙至多回一筆（取最早進入的線段），依 entry_frac 排序。"""
    if len(waypoints) < 2:
        return []
    total = route_distance_m(waypoints) or 1.0
    seg_starts: list[float] = [0.0]
    for i in range(len(waypoints) - 1):
        seg_starts.append(seg_starts[-1] + haversine_m(waypoints[i], waypoints[i + 1]))
    out: list[Crossing] = []
    for obs in obstacles:
        hit_frac: float | None = None
        for i in range(len(waypoints) - 1):
            if _segment_hits_obstacle(waypoints[i], waypoints[i + 1], obs):
                hit_frac = seg_starts[i] / total
                break
        if hit_frac is not None:
            out.append(Crossing(obs.feature_id, obs.kind, obs.label, round(hit_frac, 4)))
    out.sort(key=lambda c: c.entry_frac)
    return out


# ---------------- 成本 ----------------

# 每公里油耗 / 每公里基礎磨耗（戰力點）。tactical 預設；未來可由單位 profile 覆寫。
_FUEL_PER_KM = 1.0
_ATTRITION_PER_KM = 0.0  # 一般行軍不扣戰力（磨耗留給強穿）；保留參數供未來長途疲勞
_MS_PER_H = 3_600_000.0


def estimate_route(
    waypoints: Sequence[tuple[float, float]],
    obstacles: Iterable[Obstacle],
    *,
    speed_kmh: float,
    tick_rate_ms: float,
    fuel_per_km: float = _FUEL_PER_KM,
    attrition_per_km: float = _ATTRITION_PER_KM,
) -> RouteEstimate:
    """估計一條路徑的距離 / tick 數 / 油耗 / 基礎耗損 / 可行性（確定性，不含強穿隨機耗損）。"""
    dist_m = route_distance_m(waypoints)
    dist_km = dist_m / 1000.0
    per_tick_km = max(1e-9, speed_kmh * tick_rate_ms / _MS_PER_H)
    ticks = math.ceil(dist_km / per_tick_km) if dist_km > 0 else 0
    crossings = classify_crossings(waypoints, obstacles)
    return RouteEstimate(
        distance_m=round(dist_m, 2),
        duration_ticks=ticks,
        fuel_cost=round(dist_km * fuel_per_km, 3),
        base_attrition=round(dist_km * attrition_per_km, 3),
        feasible=len(crossings) == 0,
        forced=len(crossings) > 0,
        crossings=tuple(crossings),
    )


# 強穿單一阻礙的額外耗損比例區間（佔當前戰力）。障礙較輕、建築較重。
_FORCED_PCT_BY_KIND: dict[str, tuple[float, float]] = {
    "OBSTACLE": (0.03, 0.10),
    "BUILDING": (0.06, 0.16),
    "TERRAIN": (0.04, 0.12),
}
_FORCED_PCT_DEFAULT = (0.03, 0.10)


def forced_extra_attrition(
    crossings: Sequence[Crossing],
    current_strength: float,
    rng: Any,
    *,
    max_total_pct: float = 0.40,
) -> float:
    """強穿所有阻礙造成的額外隨機耗損（戰力點）。

    每個阻礙依類別在 [min,max] 比例內擲一次（rng.uniform），乘上「進入時的當前戰力」累加；
    全程封頂 max_total_pct，避免單次移動歸零。**唯一隨機來源＝注入的 rng**（stream="movement"）。
    """
    if current_strength <= 0.0 or not crossings:
        return 0.0
    remaining = current_strength
    total_loss = 0.0
    cap = current_strength * max_total_pct
    for c in crossings:
        lo, hi = _FORCED_PCT_BY_KIND.get(c.kind, _FORCED_PCT_DEFAULT)
        pct = rng.uniform(lo, hi)
        loss = remaining * pct
        total_loss += loss
        remaining -= loss
    return round(min(total_loss, cap), 4)
