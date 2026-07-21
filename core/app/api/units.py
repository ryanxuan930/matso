"""Units REST 端點（O4.5，SPEC §16.1）——faction-scoped 單位列表（下令 UX 需真單位）。

GET /api/v1/sessions/{id}/units —— 一般角色見己方單位；全知（統裁/白軍/管理）見全部。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.api.session_scope import require_participant
from app.auth.schemas import CurrentUser
from app.models import TacticalUnit
from app.stream.faction_filter import is_omniscient

router = APIRouter(prefix="/api/v1/sessions", tags=["units"])


class UnitView(BaseModel):
    id: str
    designation: str
    unit_level: str
    faction: str
    lat: float | None
    lng: float | None
    health: float
    comms: str


def _view(u: TacticalUnit) -> UnitView:
    return UnitView(
        id=u.id,
        designation=u.designation,
        unit_level=u.unit_level.value,
        faction=u.faction.value,
        lat=u.current_lat,
        lng=u.current_lng,
        health=u.health_status,
        comms=u.comms_status.value,
    )


@router.get("/{session_id}/units", response_model=list[UnitView])
def list_units(
    session_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[UnitView]:
    participant = require_participant(db, user, session_id)
    # faction 過濾下推到 SQL（CODE_REVIEW C12）：非全知者不把敵方單位載入行程記憶體。
    stmt = select(TacticalUnit).where(TacticalUnit.session_id == session_id)
    if not is_omniscient(participant.role):
        stmt = stmt.where(TacticalUnit.faction == participant.faction)
    units = db.execute(stmt).scalars().all()
    return [_view(u) for u in units]
