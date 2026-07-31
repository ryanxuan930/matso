"""白軍的 MSEL 動態取捨端點（WP-B2c）。

GET  /api/v1/sessions/{id}/msel                    待命注入清單
POST /api/v1/sessions/{id}/msel/{entry_id}/fire    扣板機（`manual` 唯一會成立的方式）
POST /api/v1/sessions/{id}/msel/{entry_id}/skip    不發這個狀況（記著，不是刪掉）

**限白軍/統裁**：MSEL 是整場演習的腳本，任何一方看得到就等於知道接下來會發生什麼。

寫入走命令佇列而非直接改狀態——`MselRuntime` 活在 sim runner 行程裡，
API 行程碰不到它（而且熱狀態有 in-process mirror，外部直寫會被忽略）。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db, get_settings
from app.auth.schemas import CurrentUser
from app.cache import make_redis
from app.config import Settings
from app.errors import AuthForbiddenError, SessionNotFoundError
from app.models import WargameSession
from app.state.live_msel import FIRE, SKIP, push_msel_cmd, read_pending
from app.stream.faction_filter import is_white_cell

router = APIRouter(prefix="/api/v1/sessions", tags=["msel"])


def _require_white_cell(db: Session, session_id: str, user: CurrentUser) -> None:
    if db.get(WargameSession, session_id) is None:
        raise SessionNotFoundError(f"session 不存在：{session_id}")
    if not is_white_cell(user.role):
        raise AuthForbiddenError("MSEL 腳本限白軍/統裁存取")


@router.get("/{session_id}/msel")
def list_pending(
    session_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, list[dict[str, Any]]]:
    """待命注入清單。該局沒在跑（runner 沒發布）→ 空清單，不是錯誤。"""
    _require_white_cell(db, session_id, user)
    return {"pending": read_pending(make_redis(settings.redis_url), session_id)}


@router.post("/{session_id}/msel/{entry_id}/fire", status_code=status.HTTP_202_ACCEPTED)
def fire_entry(
    session_id: str,
    entry_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, str]:
    """扣板機。**202 而非 200**——命令排入佇列，由 runner 於下一 tick 套用。"""
    _require_white_cell(db, session_id, user)
    push_msel_cmd(make_redis(settings.redis_url), session_id, FIRE, entry_id)
    return {"status": "queued", "entry_id": entry_id}


@router.post("/{session_id}/msel/{entry_id}/skip", status_code=status.HTTP_202_ACCEPTED)
def skip_entry(
    session_id: str,
    entry_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, str]:
    _require_white_cell(db, session_id, user)
    push_msel_cmd(make_redis(settings.redis_url), session_id, SKIP, entry_id)
    return {"status": "queued", "entry_id": entry_id}
