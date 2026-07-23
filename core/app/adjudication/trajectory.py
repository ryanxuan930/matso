"""飛彈拋物線軌跡淨空判定（SPEC §7.1 延伸）——純同步純函數（HOW_TO §3、§4.2）。

依武器是否可變軌，接戰可行性分兩種判定：
  * **可變軌武器**（巡弋飛彈/遊蕩彈藥/ATGM…）：地形跟隨/末端機動繞過障礙 → 僅判「射程」。
  * **不可變軌武器**（彈道飛彈/無導引火箭…）：走固定拋物線 → 判「射程 + 拋物線是否被地形
    或地圖障礙（地圖編輯器建立、含高度）阻隔」。

拋物線模型：頂高 apex＝地面射程 × apex_ratio（45° 發射≈0.25）。沿彈道弦線，離發射點比例 f 處
的「弦上高度」＝4·apex·f·(1−f)。
  - 障礙阻隔：地面路徑於比例 f 穿過某障礙，且障礙高度 > 該處弦上高度 → 阻隔（近發射/近彈著處
    弧低，牆/建築易擋；中段弧高，僅高地形擋）。
  - 地形阻隔：取樣點地形高程 > 弦絕對高度（弦端點高程內插 + 弧上高度）→ 阻隔。

紅線：純幾何，不碰 DB/RPC/時鐘/RNG。地形高程由呼叫端（wiring/precheck）查 terrain 後傳入取樣。
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from app.movement.attrition import (
    _dist_point_to_segment_m,
    point_in_ring,
    route_distance_m,
    segments_intersect,
)

# 45° 發射的最大射程拋物線頂高≈射程/4；短程彈道以此近似（可由武器 apex_ratio 覆寫）。
DEFAULT_APEX_RATIO = 0.25


@dataclass(frozen=True, slots=True)
class ArcObstacle:
    """障礙的地面幾何 + 高度（供拋物線淨空；由 MapFeature 攤平）。"""

    feature_id: str
    kind: str
    geometry_type: str  # POINT / LINE / POLYGON
    coords: tuple[tuple[float, float], ...]  # [(lng,lat), …]
    height_m: float
    radius_m: float = 0.0  # POINT 影響半徑


@dataclass(frozen=True, slots=True)
class ArcBlock:
    """阻隔結果：blocked + 原因（TERRAIN / OBSTACLE）+ 說明。"""

    blocked: bool
    reason: str | None = None  # "TERRAIN" / "OBSTACLE" / None
    detail: str = ""


def apex_m(ground_range_m: float, apex_ratio: float = DEFAULT_APEX_RATIO) -> float:
    """拋物線頂高（公尺）＝地面射程 × apex_ratio。"""
    return max(0.0, ground_range_m) * max(0.0, apex_ratio)


def arc_height_at(frac: float, top_m: float) -> float:
    """離發射端比例 frac（0..1）處的弦上高度（公尺）。頂點在 f=0.5。"""
    f = min(1.0, max(0.0, frac))
    return 4.0 * top_m * f * (1.0 - f)


def _crossing_frac(
    s: tuple[float, float], t: tuple[float, float], obs: ArcObstacle
) -> float | None:
    """地面線段 s→t 穿過障礙的最早比例（0..1）；未穿回 None。"""
    if obs.geometry_type == "POLYGON":
        ring = obs.coords
        if point_in_ring(s, ring):
            return 0.0
        if point_in_ring(t, ring):
            return 1.0
        best: float | None = None
        n = len(ring)
        for i in range(n):
            if segments_intersect(s, t, ring[i], ring[(i + 1) % n]):
                # 以障礙頂點在弦上的投影比例近似交會處。
                f = _project_frac(s, t, ring[i])
                best = f if best is None else min(best, f)
        return best
    if obs.geometry_type == "LINE":
        best = None
        for i in range(len(obs.coords) - 1):
            if segments_intersect(s, t, obs.coords[i], obs.coords[i + 1]):
                f = _project_frac(s, t, obs.coords[i])
                best = f if best is None else min(best, f)
        return best
    if (
        obs.geometry_type == "POINT"
        and obs.radius_m > 0.0
        and _dist_point_to_segment_m(obs.coords[0], s, t) <= obs.radius_m
    ):
        return _project_frac(s, t, obs.coords[0])
    return None


def _project_frac(s: tuple[float, float], t: tuple[float, float], p: tuple[float, float]) -> float:
    """p 在線段 s→t 上的投影比例（0..1，夾住）。以平面近似（tactical 尺度足夠）。"""
    dx, dy = t[0] - s[0], t[1] - s[1]
    seg2 = dx * dx + dy * dy
    if seg2 <= 1e-18:
        return 0.0
    f = ((p[0] - s[0]) * dx + (p[1] - s[1]) * dy) / seg2
    return min(1.0, max(0.0, f))


def obstacle_blocks_arc(
    shooter: tuple[float, float],
    target: tuple[float, float],
    obstacles: Iterable[ArcObstacle],
    *,
    apex_ratio: float = DEFAULT_APEX_RATIO,
) -> ArcBlock:
    """拋物線是否被地圖障礙阻隔：障礙於穿越處的高度 > 該處弧上高度 → 阻隔。"""
    ground_range = route_distance_m([shooter, target])
    top = apex_m(ground_range, apex_ratio)
    for obs in obstacles:
        f = _crossing_frac(shooter, target, obs)
        if f is None:
            continue
        arc_h = arc_height_at(f, top)
        if obs.height_m > arc_h:
            return ArcBlock(
                True,
                "OBSTACLE",
                f"{obs.kind} 高 {obs.height_m:.0f}m 於弧線 {arc_h:.0f}m 處阻隔",
            )
    return ArcBlock(False)


def terrain_blocks_arc(
    samples: Sequence[tuple[float, float]],
    shooter_elev_m: float,
    target_elev_m: float,
    ground_range_m: float,
    *,
    apex_ratio: float = DEFAULT_APEX_RATIO,
    margin_m: float = 0.0,
) -> ArcBlock:
    """地形是否阻隔拋物線。samples＝[(frac, 地形高程 m), …]（由呼叫端沿弧線查 terrain 得出）。

    弦絕對高度(f)＝弦端點高程內插 + 弧上高度；地形高程 > 弦絕對高度 + margin → 阻隔。
    """
    top = apex_m(ground_range_m, apex_ratio)
    for frac, terrain_elev in samples:
        chord_elev = shooter_elev_m + (target_elev_m - shooter_elev_m) * frac
        arc_alt = chord_elev + arc_height_at(frac, top)
        if terrain_elev > arc_alt + margin_m:
            return ArcBlock(
                True,
                "TERRAIN",
                f"地形高 {terrain_elev:.0f}m 於弧線 {arc_alt:.0f}m 處阻隔（比例 {frac:.2f}）",
            )
    return ArcBlock(False)
