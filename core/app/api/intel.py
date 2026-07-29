"""Faction-scoped intel 查詢端點（O3.3 + O7.5 RBAC，SPEC §16.1 / §12）。

GET /api/v1/sessions/{session_id}/intel → 呼叫者**自身陣營**的敵情視圖（去識別化）。
White Cell（全知）→ god view（全部）或以 `?as_faction=X` 查某陣營視角。一般角色帶他陣營
as_faction → 403（不信任 client，faction 由認證主體推導）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.api.session_scope import require_participant
from app.auth.schemas import CurrentUser
from app.errors import AuthForbiddenError
from app.factions import WHITE_CELL, validate_faction_id
from app.intel.schemas import ContactView
from app.intel.service import IntelService
from app.stream.faction_filter import is_omniscient

router = APIRouter(prefix="/api/v1/sessions", tags=["intel"])


@router.get("/{session_id}/intel", response_model=list[ContactView])
def get_intel(
    session_id: str,
    as_faction: str | None = Query(None, description="White Cell 視角：查某陣營 intel"),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[ContactView]:
    # 全知（統裁/白軍/管理）由**使用者全域角色**判定，且不要求是本局參與者——與 /units、
    # /map-features、WS 的 resolve_ws_identity 一致（WP-E3）。過去這裡無條件先
    # require_participant，導致「未加入該局的白軍觀察員」在 units 看得到、intel 卻 403；
    # /state 快照要與各端點逐項一致，就必須先讓各端點彼此一致。
    omniscient = is_omniscient(user.role)
    participant = None if omniscient else require_participant(db, user, session_id)
    service = IntelService(db)

    if omniscient:
        if as_faction is not None:
            return service.visible_contacts(session_id, validate_faction_id(as_faction))
        return service.god_view(session_id, WHITE_CELL)

    # 一般角色：只能查自己陣營；帶他陣營 as_faction → 403（fog of war 越權防護）。
    assert participant is not None  # 非全知 → 必為參與者（上方已 require）
    if as_faction is not None and as_faction != participant.faction:
        raise AuthForbiddenError("僅 White Cell 可查他陣營情報")
    return service.visible_contacts(session_id, participant.faction)
