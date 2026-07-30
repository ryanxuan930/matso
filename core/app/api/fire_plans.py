"""火力計畫 REST 端點（WP-C10.3）。

GET    /api/v1/sessions/{id}/fire-plans                              本陣營的計畫
POST   /api/v1/sessions/{id}/fire-plans                              建立計畫（含預劃目標）
DELETE /api/v1/sessions/{id}/fire-plans/{plan_id}                    取消/刪除計畫
POST   /api/v1/sessions/{id}/fire-plans/{plan_id}/targets/{tid}/fire on-call 呼叫

**火力計畫是陣營私有情報**——列表過濾一律在後端做（紅線 3），前端拿到的就是它有權看的。

on-call 端點刻意**不加席位檢查**：`fires.service.fire_target` 走的是同一個
`OrderService.submit`，席位權限在那裡已經判過（`SEAT_ORDER_TYPES[FSO_FIRES]` 含
FIRE_MISSION）。在端點再判一次會比整個 codebase 的其他閘門都嚴——`seat_role` 為
NULL 的既有參與者會被鎖死，而那個 NULL 語義是 B5.1 刻意保留的。前端隱不隱藏按鈕是 UX。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db, get_gateway
from app.api.session_scope import require_participant
from app.auth.schemas import CurrentUser
from app.errors import AuthForbiddenError, SessionNotFoundError
from app.factions.session_store import load_session_relations
from app.fires.service import (
    FirePlanError,
    NewTarget,
    cancel_plan,
    create_plan,
    fire_target,
    list_plans,
    targets_of,
)
from app.models import User, WargameSession
from app.models.enums import FirePlanTargetStatus, FireSchedule
from app.models.tables import FirePlan, FirePlanTarget
from app.orders.precheck import PhysicsGateway
from app.orders.service import OrderService
from app.state.ledger import LedgerWriter
from app.stream.faction_filter import is_omniscient

router = APIRouter(prefix="/api/v1/sessions", tags=["fire-plans"])


class FirePlanTargetView(BaseModel):
    id: str
    seq: int
    label: str | None = None
    target_lat: float
    target_lng: float
    rounds: int
    shooter_unit_id: str
    schedule: FireSchedule
    at_tick: int | None = None
    fire_request_id: str | None = None
    status: FirePlanTargetStatus
    order_id: str | None = None
    fired_at_tick: int | None = None
    failure_reason: str | None = None


class FirePlanView(BaseModel):
    id: str
    name: str
    faction: str
    status: str
    created_by: str | None = None
    created_at_tick: int
    targets: list[FirePlanTargetView] = []


class NewTargetRequest(BaseModel):
    label: str | None = None
    target_lat: float = Field(ge=-90.0, le=90.0)
    target_lng: float = Field(ge=-180.0, le=180.0)
    rounds: int = Field(default=4, ge=1, le=200)
    shooter_unit_id: str = Field(min_length=1)
    schedule: FireSchedule = FireSchedule.ON_CALL
    at_tick: int | None = Field(default=None, ge=0)
    fire_request_id: str | None = None


class CreateFirePlanRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    targets: list[NewTargetRequest] = Field(min_length=1)


def _to_target_view(t: FirePlanTarget) -> FirePlanTargetView:
    return FirePlanTargetView(
        id=t.id,
        seq=t.seq,
        label=t.label,
        target_lat=t.target_lat,
        target_lng=t.target_lng,
        rounds=t.rounds,
        shooter_unit_id=t.shooter_unit_id,
        schedule=t.schedule,
        at_tick=t.at_tick,
        fire_request_id=t.fire_request_id,
        status=t.status,
        order_id=t.order_id,
        fired_at_tick=t.fired_at_tick,
        failure_reason=t.failure_reason,
    )


def _to_view(db: Session, plan: FirePlan) -> FirePlanView:
    author = None
    if plan.created_by_participant_id:
        from app.models import SessionParticipant

        part = db.get(SessionParticipant, plan.created_by_participant_id)
        if part is not None:
            user = db.get(User, part.user_id)
            author = user.username if user is not None else None
    return FirePlanView(
        id=plan.id,
        name=plan.name,
        faction=plan.faction,
        status=str(plan.status.value),
        created_by=author,
        created_at_tick=plan.created_at_tick,
        targets=[_to_target_view(t) for t in targets_of(db, plan.id)],
    )


def _require_session(db: Session, session_id: str) -> None:
    if db.get(WargameSession, session_id) is None:
        raise SessionNotFoundError(f"session 不存在：{session_id}")


def _owned_plan(db: Session, session_id: str, plan_id: str, faction: str, omniscient: bool):  # type: ignore[no-untyped-def]
    plan = db.get(FirePlan, plan_id)
    if plan is None or plan.session_id != session_id:
        raise FirePlanError("火力計畫不存在")
    if not omniscient and plan.faction != faction:
        raise AuthForbiddenError("此火力計畫不屬於本陣營")
    return plan


@router.get("/{session_id}/fire-plans", response_model=list[FirePlanView])
def list_fire_plans(
    session_id: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> list[FirePlanView]:
    _require_session(db, session_id)
    omniscient = is_omniscient(user.role)
    faction = ""
    if not omniscient:
        faction = require_participant(db, user, session_id).faction
    return [_to_view(db, p) for p in list_plans(db, session_id, faction, omniscient)]


@router.post(
    "/{session_id}/fire-plans", response_model=FirePlanView, status_code=status.HTTP_201_CREATED
)
def create_fire_plan(
    session_id: str,
    body: CreateFirePlanRequest,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> FirePlanView:
    _require_session(db, session_id)
    part = require_participant(db, user, session_id)
    plan = create_plan(
        db,
        session_id,
        part.faction,
        body.name,
        [
            NewTarget(
                target_lat=t.target_lat,
                target_lng=t.target_lng,
                shooter_unit_id=t.shooter_unit_id,
                label=t.label,
                rounds=t.rounds,
                schedule=t.schedule,
                at_tick=t.at_tick,
                fire_request_id=t.fire_request_id,
            )
            for t in body.targets
        ],
        participant_id=part.id,
        tick=_tick(session_id),
    )
    return _to_view(db, plan)


@router.delete("/{session_id}/fire-plans/{plan_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_fire_plan(
    session_id: str,
    plan_id: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> None:
    _require_session(db, session_id)
    omniscient = is_omniscient(user.role)
    faction = "" if omniscient else require_participant(db, user, session_id).faction
    plan = _owned_plan(db, session_id, plan_id, faction, omniscient)
    cancel_plan(db, plan)
    db.delete(plan)
    db.commit()


@router.post(
    "/{session_id}/fire-plans/{plan_id}/targets/{target_id}/fire",
    response_model=FirePlanTargetView,
)
def fire_plan_target(
    session_id: str,
    plan_id: str,
    target_id: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    gateway: PhysicsGateway = Depends(get_gateway),
) -> FirePlanTargetView:
    """on-call：對預劃目標下火力任務。**下令者是按鈕的人**，不是計畫作者。

    自動排程走的是計畫作者（見 `fires.service.issuer_for`）——兩者刻意不同：
    人手按下去的當責者是按的人，排程執行的當責者是排它的人。
    """
    _require_session(db, session_id)
    part = require_participant(db, user, session_id)
    plan = _owned_plan(db, session_id, plan_id, part.faction, False)
    target = db.get(FirePlanTarget, target_id)
    if target is None or target.plan_id != plan.id:
        raise FirePlanError("預劃目標不存在於此計畫")
    tick = _tick(session_id)
    updated = fire_target(
        db,
        plan,
        target,
        issuer_id=part.id,
        order_service_factory=lambda s: _order_service(s, gateway, session_id),
        tick=tick,
    )
    return _to_target_view(updated)


def _order_service(db: Session, gateway: PhysicsGateway, session_id: str) -> OrderService:
    from app.db import default_session_factory

    return OrderService(
        db,
        gateway,
        tick_source=lambda: _tick(session_id),
        relations=load_session_relations(db, session_id),  # 見 `api/deps.py` 的警語
        event_sink=LedgerWriter(default_session_factory()),
    )


def _tick(session_id: str) -> int:
    from app.api.deps import _live_tick

    return _live_tick(session_id)
