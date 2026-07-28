"""編裝（ORBAT）編輯 REST（#6）——White Cell 編輯單位參數 + 設定各軍自編權限。

PATCH /api/v1/sessions/{id}/units/{uid}         編輯單位（designation / health / attributes）
GET   /api/v1/sessions/{id}/orbat-permissions   取自編權限（限白軍）
PUT   /api/v1/sessions/{id}/orbat-permissions   設自編權限（限白軍）

權限：White Cell（全知）恆可編任一單位；一般指揮官僅在「其陣營 ∈ 自編清單」且單位為本軍時可編。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.api.session_scope import require_participant
from app.auth.schemas import CurrentUser
from app.errors import AuthForbiddenError, SessionNotFoundError
from app.factions import validate_faction_id
from app.models import TacticalUnit, WargameSession
from app.stream.faction_filter import is_omniscient

router = APIRouter(prefix="/api/v1/sessions", tags=["orbat"])


class UnitEdit(BaseModel):
    designation: str | None = None
    health_status: float | None = Field(None, ge=0, le=100)
    attributes: dict[str, Any] | None = None


class UnitEditView(BaseModel):
    id: str
    designation: str
    faction: str
    health: float
    attributes: dict[str, Any]


class OrbatPermissions(BaseModel):
    factions: list[str] = Field(default_factory=list)


def _session_or_404(db: Session, session_id: str) -> WargameSession:
    session = db.get(WargameSession, session_id)
    if session is None:
        raise SessionNotFoundError(f"session 不存在：{session_id}")
    return session


@router.patch("/{session_id}/units/{unit_id}", response_model=UnitEditView)
def edit_unit(
    session_id: str,
    unit_id: str,
    edit: UnitEdit,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> UnitEditView:
    session = _session_or_404(db, session_id)
    unit = db.get(TacticalUnit, unit_id)
    if unit is None or unit.session_id != session_id:
        raise SessionNotFoundError("單位不存在於此 session")
    # 權限：白軍全開；一般角色需「本軍 + 該局開放自編」。
    if not is_omniscient(user.role):
        participant = require_participant(db, user, session_id)
        allowed = set(session.orbat_edit_factions or [])
        if participant.faction not in allowed or unit.faction != participant.faction:
            raise AuthForbiddenError("無編裝編輯權限（需白軍，或本軍且該局開放自編）")
    if edit.designation is not None:
        unit.designation = edit.designation
    if edit.health_status is not None:
        unit.health_status = edit.health_status
    if edit.attributes is not None:
        unit.attributes = {**(unit.attributes or {}), **edit.attributes}
    db.commit()
    return UnitEditView(
        id=unit.id,
        designation=unit.designation,
        faction=unit.faction,
        health=unit.health_status,
        attributes=unit.attributes or {},
    )


@router.get("/{session_id}/orbat-permissions", response_model=OrbatPermissions)
def get_orbat_permissions(
    session_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> OrbatPermissions:
    if not is_omniscient(user.role):
        raise AuthForbiddenError("僅白軍可檢視自編權限")
    session = _session_or_404(db, session_id)
    return OrbatPermissions(factions=list(session.orbat_edit_factions or []))


@router.put("/{session_id}/orbat-permissions", response_model=OrbatPermissions)
def set_orbat_permissions(
    session_id: str,
    perms: OrbatPermissions,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> OrbatPermissions:
    if not is_omniscient(user.role):
        raise AuthForbiddenError("僅白軍可設定自編權限")
    session = _session_or_404(db, session_id)
    factions = [validate_faction_id(f) for f in perms.factions]
    session.orbat_edit_factions = factions
    db.commit()
    return OrbatPermissions(factions=factions)
