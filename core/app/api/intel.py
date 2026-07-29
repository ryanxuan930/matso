"""Faction-scoped intel 查詢端點（O3.3 + O7.5 RBAC，SPEC §16.1 / §12）。

GET /api/v1/sessions/{session_id}/intel → 呼叫者**自身陣營**的敵情視圖（去識別化）。
White Cell（全知）→ god view（全部）或以 `?as_faction=X` 查某陣營視角。一般角色帶他陣營
as_faction → 403（不信任 client，faction 由認證主體推導）。

WP-C5 敵情粗化（SPEC_FULL §6.2）：觀測陣營整體通聯不良時，位置量化到 h3 res-6、fidelity
上限 DETECTED。粒度由本端點依該陣營單位的熱狀態算出（見 `faction_granularity`），
god view 不套用——統裁看 ground truth。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db, get_settings
from app.api.session_scope import require_participant
from app.auth.schemas import CurrentUser
from app.cache import make_redis
from app.comms import IntelGranularity, LinkState, intel_granularity
from app.config import Settings
from app.errors import AuthForbiddenError
from app.factions import WHITE_CELL, validate_faction_id
from app.intel.schemas import ContactView
from app.intel.service import IntelService
from app.models import TacticalUnit
from app.state.comms_view import load_comms_view
from app.stream.faction_filter import is_omniscient

router = APIRouter(prefix="/api/v1/sessions", tags=["intel"])


def faction_posture(db: Session, settings: Settings, session_id: str, faction: str) -> LinkState:
    """觀測陣營的整體通聯姿態：由**該陣營自己的單位**能否回報決定（敵情融合是指揮所功能）。

    只看該陣營的單位，不含盟軍——盟軍的情報走共享視圖（另一條鏈路），本軍網路斷了不代表
    盟軍的圖也糊了。無活模擬（熱狀態空）→ 全 ONLINE。
    """
    unit_ids = list(
        db.scalars(
            select(TacticalUnit.id).where(
                TacticalUnit.session_id == session_id, TacticalUnit.faction == faction
            )
        ).all()
    )
    view = load_comms_view(make_redis(settings.redis_url), session_id, unit_ids)
    return view.posture(unit_ids)


def faction_granularity(
    db: Session, settings: Settings, session_id: str, faction: str
) -> IntelGranularity:
    """該陣營的敵情粒度（FULL / COARSE / FROZEN）。"""
    return intel_granularity(faction_posture(db, settings, session_id, faction))


@router.get("/{session_id}/intel", response_model=list[ContactView])
def get_intel(
    session_id: str,
    as_faction: str | None = Query(None, description="White Cell 視角：查某陣營 intel"),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
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
            # 白軍指定視角＝問「這一軍看得到什麼」，故粗化照套（與 /units 位置凍結同語義）。
            faction = validate_faction_id(as_faction)
            granularity = faction_granularity(db, settings, session_id, faction)
            return service.visible_contacts(session_id, faction, granularity)
        return service.god_view(session_id, WHITE_CELL)

    # 一般角色：只能查自己陣營；帶他陣營 as_faction → 403（fog of war 越權防護）。
    assert participant is not None  # 非全知 → 必為參與者（上方已 require）
    if as_faction is not None and as_faction != participant.faction:
        raise AuthForbiddenError("僅 White Cell 可查他陣營情報")
    granularity = faction_granularity(db, settings, session_id, participant.faction)
    return service.visible_contacts(session_id, participant.faction, granularity)
