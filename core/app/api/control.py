"""White Cell 時間控制端點（O7.4，SPEC_FULL §12 / §3.4）。

POST /api/v1/sessions/{id}/control —— PAUSE / RESUME / ROLLBACK。**權限限 White Cell**。
GET  /api/v1/sessions/{id}/checkpoints —— 可回滾的快照點清單（WP-E1）。

三個動作都真正作用於執行中的 Kernel：PAUSE/RESUME 設清 Redis 暫停旗標（新 #6），
ROLLBACK 排入回滾請求 + 要求 runner 重建（WP-E1，非同步——理由見下）。
"""

from __future__ import annotations

import logging

import redis
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.api.deps import get_current_user, get_settings
from app.auth.schemas import CurrentUser
from app.config import Settings
from app.db import default_session_factory
from app.errors import AuthForbiddenError, RollbackTargetNotFoundError
from app.sim_control import (
    session_concluded_key,
    session_pause_key,
    session_restart_key,
    session_rollback_key,
)
from app.state.checkpoint import CheckpointManager
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
    rollback_requested_tick: int | None = None


class CheckpointView(BaseModel):
    tick: int
    ledger_seq: int
    state_hash: str
    created_at: str


def _require_white_cell(user: CurrentUser) -> None:
    if not is_white_cell(user.role):
        raise AuthForbiddenError("僅 White Cell（統裁）可控制時間")


@router.get("/{session_id}/checkpoints", response_model=list[CheckpointView])
def list_checkpoints(
    session_id: str,
    user: CurrentUser = Depends(get_current_user),
) -> list[CheckpointView]:
    """可回滾的快照點（WP-E1）。限統裁——快照點洩漏對手的推演節奏。"""
    _require_white_cell(user)
    points = CheckpointManager(default_session_factory()).list_points(session_id)
    return [
        CheckpointView(
            tick=p.tick,
            ledger_seq=p.ledger_seq,
            state_hash=p.state_hash,
            created_at=p.created_at.isoformat(),
        )
        for p in points
    ]


def _request_rollback(client: redis.Redis, session_id: str, target_tick: int | None) -> int:
    """驗證回滾目標並排入請求；回傳目標 tick。

    **不在此還原狀態**：`RedisHotState` 有 in-process mirror cache，API 行程直寫 Redis
    的話跑中的 runner 看不到，而且下一個 tick 就會用它自己的舊 mirror 蓋回去。
    改為「暫停 + 記請求 + 要求 runner 重建」，由重建後的 runner 在啟動階段執行還原
    （`state.resume.apply_pending_rollback`）——那時世上只有一個熱狀態寫入者（紅線）。

    收場旗標一併清掉：回滾到分出勝負之前，這局當然就不再是已收場。
    """
    if target_tick is None:
        raise RollbackTargetNotFoundError("ROLLBACK 需指定 target_tick")
    if CheckpointManager(default_session_factory()).load_at_tick(session_id, target_tick) is None:
        raise RollbackTargetNotFoundError(
            f"session {session_id} 無 tick={target_tick} 的 checkpoint 可回滾"
        )
    client.set(session_pause_key(session_id), "1")  # 先凍結，避免 runner 又多跑幾個 tick
    client.set(session_rollback_key(session_id), str(target_tick))
    client.delete(session_concluded_key(session_id))
    client.set(session_restart_key(session_id), "1")  # runner 收工 → 掃描層重建 → 執行回滾
    _LOG.warning("session %s 已排入回滾至 tick=%d（該局暫停中）", session_id, target_tick)
    return target_tick


@router.post("/{session_id}/control", status_code=201, response_model=ControlResponse)
def session_control(
    session_id: str,
    req: ControlRequest,
    user: CurrentUser = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> ControlResponse:
    _require_white_cell(user)
    if req.action not in _ACTIONS:
        raise AuthForbiddenError(f"未知的控制動作：{req.action}")
    payload: dict[str, object] = {"action": req.action, "source": "WHITE_CELL_CONTROL"}
    if req.target_tick is not None:
        payload["target_tick"] = req.target_tick
    rollback_tick: int | None = None
    try:
        client = redis.from_url(settings.redis_url, decode_responses=True)
        if req.action == "PAUSE":
            client.set(session_pause_key(session_id), "1")
        elif req.action == "RESUME":
            client.delete(session_pause_key(session_id))
        elif req.action == "ROLLBACK":
            rollback_tick = _request_rollback(client, session_id, req.target_tick)
        seq = publish_event(client, session_id, "SESSION_CONTROL", payload)
        client.close()
    except redis.RedisError as exc:
        _LOG.warning("session %s: 控制事件發佈失敗：%s", session_id, exc)
        raise
    return ControlResponse(seq=seq, rollback_requested_tick=rollback_tick)
