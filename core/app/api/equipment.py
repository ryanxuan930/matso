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

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Response, status
from jsonschema import Draft202012Validator
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

# weaponeering.schema.json：base_stats 依 category 對應 $def 驗證（資料驅動裁決的權威）。
_WEAPONEERING_SCHEMA = json.loads(
    (Path(__file__).resolve().parents[3] / "contracts" / "weaponeering.schema.json").read_text(
        encoding="utf-8"
    )
)


def _validate_base_stats(category: str, base_stats: dict[str, Any]) -> None:
    """依 category 對 weaponeering.schema.json 的對應 $def 驗證 base_stats。"""
    defs = _WEAPONEERING_SCHEMA.get("$defs", {})
    if category.lower() not in defs:
        raise OrderValidationError(
            f"未知裝備類別：{category}", error_code="EQUIPMENT_CATEGORY_UNKNOWN"
        )
    # 以「$ref 目標 def + 內嵌 $defs」為驗證 schema，讓 allOf $ref（如 missile/artillery→kinetic）
    # 能於同一文件內解析（否則裸 subschema 的 #/$defs/kinetic 無處可尋）。
    schema = {"$ref": f"#/$defs/{category.lower()}", "$defs": defs}
    validator = Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(base_stats), key=lambda e: list(e.path))
    if errors:
        detail = "; ".join(
            f"{'/'.join(map(str, e.path)) or '(root)'}: {e.message}" for e in errors[:5]
        )
        raise OrderValidationError(
            f"裝備屬性不符 schema：{detail}", error_code="EQUIPMENT_INVALID_STATS"
        )


def _require_admin(user: CurrentUser) -> None:
    """全域武器庫（範本目錄）僅統裁/白軍/管理可增改（影響所有 session）。"""
    if not is_omniscient(user.role):
        raise AuthForbiddenError("僅統裁/管理可編輯武器庫")


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
    quantity: int = 1  # #30 建制數量（班內同型武器件數；驅動 squad 齊射火力）


class AddEquipmentRequest(BaseModel):
    template_id: str
    quantity: int = 1  # #30 配發件數


class EquipmentTemplateEdit(BaseModel):
    name: str
    category: str
    base_stats: dict[str, Any]


class EquipmentStateEdit(BaseModel):
    current_state: dict[str, Any]
    quantity: int | None = None  # #30 調整建制數量（None＝不動）


def _view(inst: EquipmentInstance, tmpl: EquipmentTemplate) -> EquipmentInstanceView:
    return EquipmentInstanceView(
        id=inst.id,
        template_id=tmpl.id,
        name=tmpl.name,
        category=tmpl.category,
        current_state=dict(inst.current_state or {}),
        base_stats=dict(tmpl.base_stats or {}),
        quantity=int(inst.quantity or 1),
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


def _template_view(t: EquipmentTemplate) -> EquipmentTemplateView:
    return EquipmentTemplateView(
        id=t.id, name=t.name, category=t.category, base_stats=dict(t.base_stats or {})
    )


@router.post(
    "/equipment-templates",
    response_model=EquipmentTemplateView,
    status_code=status.HTTP_201_CREATED,
)
def create_equipment_template(
    body: EquipmentTemplateEdit,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> EquipmentTemplateView:
    _require_admin(user)
    category = body.category.upper()
    _validate_base_stats(category, body.base_stats)
    tmpl = EquipmentTemplate(name=body.name, category=category, base_stats=dict(body.base_stats))
    db.add(tmpl)
    db.commit()
    return _template_view(tmpl)


@router.put("/equipment-templates/{tid}", response_model=EquipmentTemplateView)
def update_equipment_template(
    tid: str,
    body: EquipmentTemplateEdit,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> EquipmentTemplateView:
    _require_admin(user)
    category = body.category.upper()
    _validate_base_stats(category, body.base_stats)
    tmpl = db.get(EquipmentTemplate, tid)
    if tmpl is None:
        raise OrderValidationError(
            f"裝備範本不存在：{tid}", error_code="EQUIPMENT_TEMPLATE_NOT_FOUND"
        )
    tmpl.name = body.name
    tmpl.category = category
    tmpl.base_stats = dict(body.base_stats)
    db.commit()
    return _template_view(tmpl)


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
    qty = max(1, int(req.quantity))
    inst = EquipmentInstance(
        template_id=tmpl.id, owner_id=unit.id, current_state=state, quantity=qty
    )
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
    if edit.quantity is not None:
        inst.quantity = max(1, int(edit.quantity))
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
