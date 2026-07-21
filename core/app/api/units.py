"""Units REST 端點（O4.5，SPEC §16.1）——faction-scoped 單位列表（下令 UX 需真單位）。

GET /api/v1/sessions/{id}/units —— 一般角色見己方單位；全知（統裁/白軍/管理）見全部。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db, get_settings
from app.api.session_scope import require_participant
from app.auth.schemas import CurrentUser
from app.config import Settings
from app.errors import AuthForbiddenError
from app.factions import validate_faction_id
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
        faction=u.faction,
        lat=u.current_lat,
        lng=u.current_lng,
        health=u.health_status,
        comms=u.comms_status.value,
    )


@router.get("/{session_id}/units", response_model=list[UnitView])
def list_units(
    session_id: str,
    as_faction: str | None = Query(None, description="White Cell 視角切換（O7.4）"),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> list[UnitView]:
    participant = require_participant(db, user, session_id)
    omniscient = is_omniscient(participant.role)
    stmt = select(TacticalUnit).where(TacticalUnit.session_id == session_id)

    if as_faction is not None:
        # 視角切換（White Cell 控制台，O7.4）：僅全知可指定；非全知者禁止（防越權窺視）。
        if not omniscient:
            raise AuthForbiddenError("僅 White Cell 可切換視角")
        stmt = stmt.where(TacticalUnit.faction == validate_faction_id(as_faction))
    elif not omniscient and not settings.stub_gateway:
        # 一般角色：faction 過濾下推 SQL（C12）；STUB_GATEWAY E2E affordance 放行全單位。
        stmt = stmt.where(TacticalUnit.faction == participant.faction)
    # else：全知且未指定視角 → 全部（god view）

    units = db.execute(stmt).scalars().all()
    return [_view(u) for u in units]
