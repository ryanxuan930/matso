"""White Cell 時間控制端點（O7.4，SPEC_FULL §12 / §3.4）。

POST /api/v1/sessions/{id}/control —— PAUSE / RESUME / ROLLBACK。**權限限 White Cell**。
本卡發佈 SESSION_CONTROL 事件到 stream（kernel 消費並實際暫停/回滾屬部署層 / O1.5 recover）。
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

_LOG = logging.getLogger("app.control")

router = APIRouter(prefix="/api/v1/sessions", tags=["control"])

_ACTIONS = frozenset({"PAUSE", "RESUME", "ROLLBACK"})


class ControlRequest(BaseModel):
    action: str = Field(description="PAUSE / RESUME / ROLLBACK")
    target_tick: int | None = Field(default=None, description="ROLLBACK 目標 tick")


class ControlResponse(BaseModel):
    seq: int


@router.post("/{session_id}/control", status_code=201, response_model=ControlResponse)
def session_control(
    session_id: str,
    req: ControlRequest,
    user: CurrentUser = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> ControlResponse:
    if not is_white_cell(user.role):
        raise AuthForbiddenError("僅 White Cell（統裁）可控制時間")
    if req.action not in _ACTIONS:
        raise AuthForbiddenError(f"未知的控制動作：{req.action}")
    payload: dict[str, object] = {"action": req.action, "source": "WHITE_CELL_CONTROL"}
    if req.target_tick is not None:
        payload["target_tick"] = req.target_tick
    try:
        client = redis.from_url(settings.redis_url, decode_responses=True)
        seq = publish_event(client, session_id, "SESSION_CONTROL", payload)
        client.close()
    except redis.RedisError as exc:
        _LOG.warning("session %s: 控制事件發佈失敗：%s", session_id, exc)
        raise
    return ControlResponse(seq=seq)
