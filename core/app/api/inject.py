"""White Cell ad-hoc 事件注入端點（O7.2，SPEC_FULL §11.3 / §12）。

POST /api/v1/sessions/{id}/inject —— 手動注入任意 MSEL/臨時事件到 Ledger + WS stream。
**權限限 White Cell**（EXERCISE_DIRECTOR / WHITE_CELL_STAFF）；其餘角色 → 403 AUTH_FORBIDDEN。
"""

from __future__ import annotations

import logging

import redis
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.api.deps import get_current_user, get_settings
from app.auth.schemas import CurrentUser
from app.config import Settings
from app.errors import AuthForbiddenError
from app.stream.faction_filter import is_white_cell
from app.stream.publish import publish_event

_LOG = logging.getLogger("app.inject")

router = APIRouter(prefix="/api/v1/sessions", tags=["inject"])

# 可注入的角色（統裁）——ADMIN 不含（管理≠統裁；SPEC §12 inject 限 White Cell）。


class InjectRequest(BaseModel):
    event_type: str = Field(min_length=1)
    payload: dict[str, object] = Field(default_factory=dict)
    faction: str | None = None  # 受眾（None＝廣播全體）


class InjectResponse(BaseModel):
    seq: int


@router.post("/{session_id}/inject", status_code=201, response_model=InjectResponse)
def inject_event(
    session_id: str,
    req: InjectRequest,
    user: CurrentUser = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> InjectResponse:
    if not is_white_cell(user.role):
        raise AuthForbiddenError("僅 White Cell（統裁）可注入事件")
    seq = -1
    try:
        client = redis.from_url(settings.redis_url, decode_responses=True)
        seq = publish_event(
            client,
            session_id,
            req.event_type,
            {**req.payload, "source": "WHITE_CELL_INJECT"},
            faction=req.faction,
        )
        client.close()
    except redis.RedisError as exc:  # 不吞：注入失敗要讓統裁知道
        _LOG.warning("session %s: 事件注入發佈失敗：%s", session_id, exc)
        raise
    return InjectResponse(seq=seq)
