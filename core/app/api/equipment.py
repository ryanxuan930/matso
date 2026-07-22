"""裝備（編裝／武器裝載）編輯 REST（stage ①）——列裝備範本 + 為單位增/刪/改裝備實例。

GET    /api/v1/equipment-templates                             裝備範本目錄（配發用）
GET    /api/v1/sessions/{id}/units/{uid}/equipment            單位編裝清單（fog of war）
POST   /api/v1/sessions/{id}/units/{uid}/equipment            配發一件裝備
PATCH  /api/v1/sessions/{id}/units/{uid}/equipment/{eid}      覆寫實例即時狀態（如彈藥）
DELETE /api/v1/sessions/{id}/units/{uid}/equipment/{eid}      移除一件裝備

權限（同 orbat #6）：白軍（全知）恆可編任一單位；一般指揮官僅「本軍 + 該局開放自編」。
讀取（列清單）走 fog of war：全知見任一，否則僅己方單位（他方→403，不洩漏 loadout）。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.api.session_scope import require_participant
from app.auth.schemas import CurrentUser
from app.errors import AuthForbiddenError, OrderValidationError, SessionNotFoundError
from app.models import EquipmentInstance, EquipmentTemplate, TacticalUnit, WargameSession
from app.stream.faction_filter import is_omniscient

router = APIRouter(prefix="/api/v1", tags=["equipment"])


class EquipmentTemplateView(BaseModel):
    id: str
    name: str
    category: str
    base_stats: dict[str, Any]


class EquipmentInstanceView(BaseModel):
    id: str
    template_id: str
    name: str
    category: str
    current_state: dict[str, Any]
    base_stats: dict[str, Any]


class AddEquipmentRequest(BaseModel):
    template_id: str


class EquipmentStateEdit(BaseModel):
    current_state: dict[str, Any]


def _view(inst: EquipmentInstance, tmpl: EquipmentTemplate) -> EquipmentInstanceView:
    return EquipmentInstanceView(
        id=inst.id,
        template_id=tmpl.id,
        name=tmpl.name,
        category=tmpl.category,
        current_state=dict(inst.current_state or {}),
        base_stats=dict(tmpl.base_stats or {}),
    )


def _session_or_404(db: Session, session_id: str) -> WargameSession:
    session = db.get(WargameSession, session_id)
    if session is None:
        raise SessionNotFoundError(f"session 不存在：{session_id}")
    return session


def _unit_or_403(db: Session, session_id: str, unit_id: str) -> TacticalUnit:
    unit = db.get(TacticalUnit, unit_id)
    if unit is None or unit.session_id != session_id:
        raise AuthForbiddenError("查無此單位")  # 不區分「不存在」與「他方」以防列舉
    return unit


def _require_read(db: Session, user: CurrentUser, session_id: str, unit: TacticalUnit) -> None:
    """fog of war：全知見任一，否則須為參與者且為己方單位。"""
    if is_omniscient(user.role):
        return
    participant = require_participant(db, user, session_id)
    if unit.faction != participant.faction:
        raise AuthForbiddenError("無權查看他方單位裝備")


def _require_edit(
    db: Session, user: CurrentUser, session: WargameSession, unit: TacticalUnit
) -> None:
    """編裝編輯權限：白軍全開；一般角色需「本軍 + 該局開放自編」。"""
    if is_omniscient(user.role):
        return
    participant = require_participant(db, user, session.id)
    allowed = set(session.orbat_edit_factions or [])
    if participant.faction not in allowed or unit.faction != participant.faction:
        raise AuthForbiddenError("無編裝編輯權限（需白軍，或本軍且該局開放自編）")


def _instance_or_404(db: Session, unit: TacticalUnit, eid: str) -> EquipmentInstance:
    inst = db.get(EquipmentInstance, eid)
    if inst is None or inst.owner_id != unit.id:
        raise AuthForbiddenError("查無此裝備")
    return inst


@router.get("/equipment-templates", response_model=list[EquipmentTemplateView])
def list_equipment_templates(
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[EquipmentTemplateView]:
    tmpls = db.execute(select(EquipmentTemplate)).scalars().all()
    return [
        EquipmentTemplateView(
            id=t.id, name=t.name, category=t.category, base_stats=dict(t.base_stats or {})
        )
        for t in tmpls
    ]


@router.get(
    "/sessions/{session_id}/units/{unit_id}/equipment",
    response_model=list[EquipmentInstanceView],
)
def list_unit_equipment(
    session_id: str,
    unit_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[EquipmentInstanceView]:
    unit = _unit_or_403(db, session_id, unit_id)
    _require_read(db, user, session_id, unit)
    instances = (
        db.execute(select(EquipmentInstance).where(EquipmentInstance.owner_id == unit.id))
        .scalars()
        .all()
    )
    out: list[EquipmentInstanceView] = []
    for inst in instances:
        tmpl = db.get(EquipmentTemplate, inst.template_id)
        if tmpl is not None:
            out.append(_view(inst, tmpl))
    return out


@router.post(
    "/sessions/{session_id}/units/{unit_id}/equipment",
    response_model=EquipmentInstanceView,
    status_code=status.HTTP_201_CREATED,
)
def add_unit_equipment(
    session_id: str,
    unit_id: str,
    req: AddEquipmentRequest,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> EquipmentInstanceView:
    session = _session_or_404(db, session_id)
    unit = _unit_or_403(db, session_id, unit_id)
    _require_edit(db, user, session, unit)
    tmpl = db.get(EquipmentTemplate, req.template_id)
    if tmpl is None:
        raise OrderValidationError(
            f"裝備範本不存在：{req.template_id}", error_code="EQUIPMENT_TEMPLATE_NOT_FOUND"
        )
    # KINETIC 武器配發時給初始彈藥，其餘裝備空狀態（後續可 PATCH）。
    state: dict[str, Any] = {"ammo": 100} if tmpl.category == "KINETIC" else {}
    inst = EquipmentInstance(template_id=tmpl.id, owner_id=unit.id, current_state=state)
    db.add(inst)
    db.commit()
    return _view(inst, tmpl)


@router.patch(
    "/sessions/{session_id}/units/{unit_id}/equipment/{eid}",
    response_model=EquipmentInstanceView,
)
def edit_unit_equipment(
    session_id: str,
    unit_id: str,
    eid: str,
    edit: EquipmentStateEdit,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> EquipmentInstanceView:
    session = _session_or_404(db, session_id)
    unit = _unit_or_403(db, session_id, unit_id)
    _require_edit(db, user, session, unit)
    inst = _instance_or_404(db, unit, eid)
    inst.current_state = {**(inst.current_state or {}), **edit.current_state}
    db.commit()
    tmpl = db.get(EquipmentTemplate, inst.template_id)
    if tmpl is None:
        raise AuthForbiddenError("查無此裝備範本")
    return _view(inst, tmpl)


@router.delete(
    "/sessions/{session_id}/units/{unit_id}/equipment/{eid}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def remove_unit_equipment(
    session_id: str,
    unit_id: str,
    eid: str,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    session = _session_or_404(db, session_id)
    unit = _unit_or_403(db, session_id, unit_id)
    _require_edit(db, user, session, unit)
    inst = _instance_or_404(db, unit, eid)
    db.delete(inst)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
