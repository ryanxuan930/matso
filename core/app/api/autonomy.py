"""自主推演控制 REST — O11.4b（前端 O11.7 用）。

`PUT/GET/DELETE /api/v1/sessions/{session_id}/autonomy` — 設定/檢視/清除本 session 的 AI 陣營
指派（存 Redis `session:{id}:ai_config`）。sim runner 於 session **起跑時**讀取並為每個 AI 陣營起
決策 worker。限統裁/白軍/管理（is_omniscient）。

指派只於 runner 起跑時讀取，故 PUT/DELETE 額外設「重啟旗標」（`session_restart_key`）：執行中的
runner 輪詢到即結束當前迴圈，由掃描層數秒內重建 → 重讀指派 → 立即啟動/停止 AI（熱狀態於 Redis，
不中斷戰局）。
"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.ai_loop.orchestrator import autonomy_config_key
from app.api.deps import get_current_user, get_settings
from app.auth.schemas import CurrentUser
from app.cache import make_redis
from app.config import Settings
from app.errors import AuthForbiddenError
from app.sim_control import session_restart_key
from app.stream.faction_filter import is_omniscient

router = APIRouter(prefix="/api/v1/sessions", tags=["autonomy"])


def _require_admin(user: CurrentUser) -> None:
    if not is_omniscient(user.role):
        raise AuthForbiddenError("僅統裁/白軍/管理可設定自主推演")


@lru_cache(maxsize=1)
def _redis(url: str) -> Any:
    return make_redis(url)


class FactionAI(BaseModel):
    mission: str = ""
    objectives: list[dict[str, Any]] = []


class AutonomyConfig(BaseModel):
    factions: dict[str, FactionAI] = {}
    heartbeat_s: float = 45.0


@router.put("/{session_id}/autonomy")
def set_autonomy(
    session_id: str,
    cfg: AutonomyConfig,
    user: CurrentUser = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    _require_admin(user)
    r = _redis(settings.redis_url)
    r.set(autonomy_config_key(session_id), cfg.model_dump_json())
    # 請求 runner 重啟以立即讀取指派（數秒內生效；戰局熱狀態於 Redis 不中斷）。
    r.set(session_restart_key(session_id), "1")
    return {
        "ok": True,
        "factions": list(cfg.factions),
        "heartbeat_s": cfg.heartbeat_s,
        "restarted": True,
    }


@router.get("/{session_id}/autonomy")
def get_autonomy(
    session_id: str,
    user: CurrentUser = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    _require_admin(user)
    raw = _redis(settings.redis_url).get(autonomy_config_key(session_id))
    if not raw:
        return {"factions": {}, "heartbeat_s": 45.0}
    try:
        return dict(json.loads(raw))
    except (ValueError, TypeError):
        return {"factions": {}, "heartbeat_s": 45.0}


@router.delete("/{session_id}/autonomy")
def clear_autonomy(
    session_id: str,
    user: CurrentUser = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    _require_admin(user)
    r = _redis(settings.redis_url)
    r.delete(autonomy_config_key(session_id))
    r.set(session_restart_key(session_id), "1")  # runner 重啟 → 停掉 AI worker
    return {"ok": True, "restarted": True}
