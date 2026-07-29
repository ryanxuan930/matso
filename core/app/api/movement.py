"""移動路徑預覽（#28）——下令前試算路徑距離 / tick 數 / 油耗 / 可行性 / 強穿阻礙。

POST /api/v1/sessions/{id}/movement/preview

輸入：unit_id + 目的地（to_h3 或 to_lat/to_lng）或自訂 waypoints（[[lng,lat],…]）。
輸出：完整路徑座標串、距離、估計 tick、油耗、基礎耗損、是否可行（不穿阻礙）、
      是否需強穿、逐項穿越的阻礙（feature_id/kind/label/進入比例）。

紅線：阻礙可見性沿用 fog of war（後端過濾，只看本軍 + 共同標註）；純幾何試算，
不改任何狀態、不擲骰（強穿的隨機加成耗損在執行期由 DeterministicRNG 產生）。
"""

from __future__ import annotations

from itertools import pairwise

import h3
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db, get_movement_path_fn
from app.api.session_scope import require_participant
from app.auth.schemas import CurrentUser
from app.errors import OrderValidationError, SessionNotFoundError
from app.factions import WHITE_CELL
from app.models import MapFeature, TacticalUnit, WargameSession
from app.movement.attrition import estimate_route, haversine_m, obstacle_from_feature
from app.movement.fuel import load_unit_fuel
from app.movement.mobility import resolve_unit_mobility
from app.movement.mobility_matrix import MobilityRules
from app.movement.params import (
    TEMPO_ATTRITION_FACTOR,
)
from app.movement.router import plan_route
from app.movement.session_mobility import load_session_mobility_rules
from app.movement.terrain_sampler import build_terrain_cell_sampler
from app.sim_params import load_sim_params
from app.stream.faction_filter import is_omniscient

router = APIRouter(prefix="/api/v1/sessions", tags=["movement"])

_MAX_WAYPOINTS = 64


class MovementPreviewRequest(BaseModel):
    """路徑預覽請求：以 waypoints 為主；否則用單一目的地（起點取單位當前座標）。"""

    unit_id: str
    waypoints: list[list[float]] | None = None  # [[lng,lat], …]（含或不含起點皆可）
    to_h3: str | None = None
    to_lat: float | None = None
    to_lng: float | None = None
    tempo: str = "NORMAL"  # #80：NORMAL / FORCED_MARCH（強行軍更快但更耗）


class CrossingView(BaseModel):
    feature_id: str
    kind: str
    label: str | None
    entry_frac: float


class MovementPreviewView(BaseModel):
    path: list[list[float]]  # [[lng,lat], …] 實際試算路徑（含起點）
    distance_m: float
    duration_ticks: int
    fuel_cost: float
    est_attrition: float  # 行軍（確定性）耗損；強穿隨機加成不在此
    feasible: bool
    forced: bool
    crossings: list[CrossingView]
    mobility_profile: str = "FOOT"  # #80：由編裝導出的機動 profile
    speed_kmh: float = 0.0  # #80/#81：有效速度（含 tempo、路徑平均地形調變），供 COP 顯示
    terrain_impassable: bool = False  # #81：路徑是否穿越對此 profile 不可通行的地形
    terrain_routed: bool = False  # #82：路徑是否為地形 A* 繞路（False＝直線，含不可達退回）
    fuel_remaining: float = 0.0  # #84：單位目前剩餘油量（0＝徒步/無油料模型）
    fuel_sufficient: bool = True  # #84：現有油量是否足以走完全程（否則中途會停駛）


def _dest_lnglat(body: MovementPreviewRequest) -> tuple[float, float] | None:
    if isinstance(body.to_lat, (int, float)) and isinstance(body.to_lng, (int, float)):
        return float(body.to_lng), float(body.to_lat)
    if body.to_h3:
        try:
            lat, lng = h3.cell_to_latlng(body.to_h3)
            return float(lng), float(lat)
        except (ValueError, TypeError) as exc:
            raise OrderValidationError("to_h3 非法", error_code="MOVE_PREVIEW_BAD_DEST") from exc
    return None


@router.post("/{session_id}/movement/preview", response_model=MovementPreviewView)
def preview_movement(
    session_id: str,
    body: MovementPreviewRequest,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
    path_fn: object | None = Depends(get_movement_path_fn),
) -> MovementPreviewView:
    session = db.get(WargameSession, session_id)
    if session is None:
        raise SessionNotFoundError(f"session 不存在：{session_id}")

    unit = db.get(TacticalUnit, body.unit_id)
    if unit is None or unit.session_id != session_id:
        raise OrderValidationError("查無此單位", error_code="MOVE_PREVIEW_NO_UNIT")
    if unit.current_lat is None or unit.current_lng is None:
        raise OrderValidationError("單位尚無座標", error_code="MOVE_PREVIEW_NO_POS")
    start = (float(unit.current_lng), float(unit.current_lat))

    # 路徑：自訂 waypoints 優先（首點若非起點則補上起點）；否則 #82 地形路徑（不可達→直線）。
    waypoints: list[tuple[float, float]] = [start]
    routed = False
    if body.waypoints:
        pts = [(float(p[0]), float(p[1])) for p in body.waypoints[:_MAX_WAYPOINTS] if len(p) >= 2]
        if pts and _close(pts[0], start):
            pts = pts[1:]
        waypoints.extend(pts)
    else:
        dest = _dest_lnglat(body)
        if dest is None:
            raise OrderValidationError(
                "需提供 to_h3 / to_lat+to_lng 或 waypoints", error_code="MOVE_PREVIEW_NO_DEST"
            )
        # #82：預覽與執行共用同一規劃器 → 預覽路徑＝實際行進路徑（含任意點位精確起訖）。
        if path_fn is not None:
            route = plan_route(
                path_fn,  # type: ignore[arg-type]
                start_lat=start[1],
                start_lng=start[0],
                dest_lat=dest[1],
                dest_lng=dest[0],
                profile=resolve_unit_mobility(db, unit.id).profile,
            )
            waypoints.extend(route.waypoints)
            routed = route.routed
        else:
            waypoints.append(dest)

    if len(waypoints) < 2:
        raise OrderValidationError("路徑至少需起訖兩點", error_code="MOVE_PREVIEW_SHORT")

    # 阻礙標註（fog of war：本軍 + 共同）。
    stmt = select(MapFeature).where(MapFeature.session_id == session_id)
    if not is_omniscient(user.role):
        participant = require_participant(db, user, session_id)
        stmt = stmt.where(MapFeature.owner_faction.in_([WHITE_CELL, participant.faction]))
    obstacles = []
    for f in db.execute(stmt).scalars().all():
        obs = obstacle_from_feature(
            {
                "id": f.id,
                "kind": f.kind,
                "geometry_type": f.geometry_type,
                "geometry": f.geometry,
                "label": f.label,
                "influence_radius_m": f.influence_radius_m,
                "attributes": f.attributes,
            }
        )
        if obs is not None:
            obstacles.append(obs)

    # #80：per-unit 機動速度 + 行軍磨耗率（由編裝導出）。
    tempo = body.tempo if body.tempo in ("NORMAL", "FORCED_MARCH") else "NORMAL"
    # #93：預覽與執行必須讀同一份推演參數，否則「估計」與「實跑」再度分歧
    # （SPEC_MOVEMENT 當初就是為了消滅這種不一致）。
    sim_params = load_sim_params(db)
    mob = resolve_unit_mobility(
        db, unit.id, foot_xc_kmh=sim_params.foot_xc_kmh, foot_road_kmh=sim_params.foot_road_kmh
    )
    speed_kmh = mob.speed_kmh(tempo=tempo)
    attrition_per_km = sim_params.attrition_for(mob.profile) * TEMPO_ATTRITION_FACTOR.get(
        tempo, 1.0
    )
    # #81：以路徑平均地形成本調變預覽速度，並標示是否穿越不可通行地形（terrain 不可用→不調）。
    terrain_impassable = False
    sampler = build_terrain_cell_sampler()
    if sampler is not None:
        cells = _route_cells(waypoints)
        # WP-B6：預覽與執行**讀同一份**機動規則（含該局的想定覆寫）——只讓執行端可覆寫，
        # 就會重演 SPEC_MOVEMENT 當初要消滅的「預覽與實跑不一致」。
        rules = load_session_mobility_rules(db, session_id)
        avg_cost, terrain_impassable = _route_terrain_cost(sampler, cells, mob.profile, rules)
        if avg_cost > 0:
            speed_kmh /= avg_cost
    # #84：油耗以**單位實際編裝**計（取代原本 1.0/km 佔位值）；並回報是否夠油走完全程。
    unit_fuel = load_unit_fuel(db, unit.id)
    est = estimate_route(
        waypoints,
        obstacles,
        speed_kmh=speed_kmh,
        tick_rate_ms=sim_params.tick_rate_ms,  # #93 與執行端同一份
        attrition_per_km=attrition_per_km,
        fuel_per_km=unit_fuel.burn_per_km,
    )
    fuel_sufficient = (not unit_fuel.needs_fuel) or est.fuel_cost <= unit_fuel.remaining
    return MovementPreviewView(
        path=[[lng, lat] for lng, lat in waypoints],
        distance_m=est.distance_m,
        duration_ticks=est.duration_ticks,
        fuel_cost=est.fuel_cost,
        est_attrition=est.base_attrition,
        feasible=est.feasible and not terrain_impassable,
        forced=est.forced,
        crossings=[
            CrossingView(
                feature_id=c.feature_id, kind=c.kind, label=c.label, entry_frac=c.entry_frac
            )
            for c in est.crossings
        ],
        mobility_profile=mob.profile,
        speed_kmh=round(speed_kmh, 1),
        terrain_impassable=terrain_impassable,
        terrain_routed=routed,
        fuel_remaining=round(unit_fuel.remaining, 1),
        fuel_sufficient=fuel_sufficient,
    )


def _close(a: tuple[float, float], b: tuple[float, float], eps: float = 1e-7) -> bool:
    return abs(a[0] - b[0]) < eps and abs(a[1] - b[1]) < eps


_ROUTE_SAMPLE_KM = 0.3  # 沿路徑取樣地形的間距（約 res-8 hex 尺度）


def _route_cells(waypoints: list[tuple[float, float]]) -> list[str]:
    """沿路徑（[(lng,lat)…]）取樣經過的 hex（res 8，去重保序）。"""
    seen: list[str] = []
    known: set[str] = set()
    for (lng0, lat0), (lng1, lat1) in pairwise(waypoints):
        dist_km = haversine_m((lng0, lat0), (lng1, lat1)) / 1000.0
        n = max(1, int(dist_km / _ROUTE_SAMPLE_KM))
        for i in range(n + 1):
            f = i / n
            cell = h3.latlng_to_cell(lat0 + (lat1 - lat0) * f, lng0 + (lng1 - lng0) * f, 8)
            if cell not in known:
                known.add(cell)
                seen.append(cell)
    return seen


def _route_terrain_cost(  # type: ignore[no-untyped-def]
    sampler, cells: list[str], profile: str, rules: MobilityRules
) -> tuple[float, bool]:
    """回 (路徑平均地形成本, 是否含不可通行)。取樣失敗 → (1.0, False)（不調預覽）。"""
    try:
        info = sampler(cells)
    except Exception:
        return 1.0, False
    costs: list[float] = []
    impassable = False
    for c in cells:
        ct = info.get(c)
        if ct is None:
            continue
        # 取樣器的 terrain_class 在有路的格是 "FOREST|primary" 形——**必須先切掉路段**，
        # 否則查不到該 class 而落到「未知→1.0」分支（執行端 engine/movement 早已這麼做，
        # 預覽端過去漏了，兩邊對有路路段的地形成本因此不同）。
        sc = rules.step_cost(profile, ct[0].split("|", 1)[0], ct[1])
        if sc is None:
            impassable = True
        elif sc > 0:
            costs.append(sc)
    return (sum(costs) / len(costs) if costs else 1.0), impassable
