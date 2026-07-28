"""以觀測者為中心的陣營關係查詢（#91，SPEC_FULL §12.1）。

GET /api/v1/sessions/{id}/relations → 「我對其他各陣營」的關係，供前端決定 2525 affiliation。

**刻意不回完整矩陣**：第三方之間是否結盟，不是觀測者必然知道的事；只回以觀測者為中心的一列，
既滿足畫友/敵符號的需要，又不順手洩漏他方的政治關係。視角切換（as_faction）與 units/intel 同紀律：
僅全知可指定，一般角色帶他陣營→403。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.api.session_scope import require_participant
from app.auth.schemas import CurrentUser
from app.errors import AuthForbiddenError
from app.factions import WHITE_CELL, validate_faction_id
from app.factions.session_store import load_session_relations
from app.models.tables import TacticalUnit
from app.stream.faction_filter import is_omniscient

router = APIRouter(prefix="/api/v1/sessions", tags=["relations"])


class FactionRelationsView(BaseModel):
    observer: str | None
    relations: dict[str, str]
    factions: list[str]


@router.get("/{session_id}/relations", response_model=FactionRelationsView)
def get_faction_relations(
    session_id: str,
    as_faction: str | None = Query(None, description="White Cell 視角：以該陣營為觀測者"),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> FactionRelationsView:
    omniscient = is_omniscient(user.role)
    participant = None if omniscient else require_participant(db, user, session_id)

    if as_faction is not None:
        if not omniscient:
            raise AuthForbiddenError("僅 White Cell 可切換視角")
        observer: str | None = validate_faction_id(as_faction)
    else:
        # 全知未指定 → 全局視角（無單一觀測者）；一般角色 → 自身陣營。
        observer = None if omniscient else (participant.faction if participant else None)

    factions = sorted(
        f
        for f in db.scalars(
            select(TacticalUnit.faction).where(TacticalUnit.session_id == session_id).distinct()
        ).all()
        if f != WHITE_CELL
    )
    if observer is None:
        return FactionRelationsView(observer=None, relations={}, factions=factions)

    rel = load_session_relations(db, session_id)
    return FactionRelationsView(
        observer=observer,
        relations={f: rel.relation(observer, f).value for f in factions},
        factions=factions,
    )
