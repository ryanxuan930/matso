"""移動路徑預覽（#28）——下令前試算路徑距離 / tick 數 / 油耗 / 可行性 / 強穿阻礙。

POST /api/v1/sessions/{id}/movement/preview

輸入：unit_id + 目的地（to_h3 或 to_lat/to_lng）或自訂 waypoints（[[lng,lat],…]）。
輸出：完整路徑座標串、距離、估計 tick、油耗、基礎耗損、是否可行（不穿阻礙）、
      是否需強穿、逐項穿越的阻礙（feature_id/kind/label/進入比例）。

紅線：阻礙可見性沿用 fog of war（後端過濾，只看本軍 + 共同標註）；純幾何試算，
不改任何狀態、不擲骰（強穿的隨機加成耗損在執行期由 DeterministicRNG 產生）。
"""

from __future__ import annotations

import h3
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.api.session_scope import require_participant
from app.auth.schemas import CurrentUser
from app.errors import OrderValidationError, SessionNotFoundError
from app.factions import WHITE_CELL
from app.models import MapFeature, TacticalUnit, WargameSession
from app.movement.attrition import estimate_route, obstacle_from_feature
from app.movement.params import MOVE_SPEED_KMH, MOVE_TICK_RATE_MS
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
    est_attrition: float  # 基礎（確定性）耗損；強穿隨機加成不在此
    feasible: bool
    forced: bool
    crossings: list[CrossingView]


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

    # 路徑：自訂 waypoints 優先（首點若非起點則補上起點）；否則起點→目的地直線。
    waypoints: list[tuple[float, float]] = [start]
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

    est = estimate_route(
        waypoints, obstacles, speed_kmh=MOVE_SPEED_KMH, tick_rate_ms=MOVE_TICK_RATE_MS
    )
    return MovementPreviewView(
        path=[[lng, lat] for lng, lat in waypoints],
        distance_m=est.distance_m,
        duration_ticks=est.duration_ticks,
        fuel_cost=est.fuel_cost,
        est_attrition=est.base_attrition,
        feasible=est.feasible,
        forced=est.forced,
        crossings=[
            CrossingView(
                feature_id=c.feature_id, kind=c.kind, label=c.label, entry_frac=c.entry_frac
            )
            for c in est.crossings
        ],
    )


def _close(a: tuple[float, float], b: tuple[float, float], eps: float = 1e-7) -> bool:
    return abs(a[0] - b[0]) < eps and abs(a[1] - b[1]) < eps
