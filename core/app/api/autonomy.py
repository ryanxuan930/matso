"""自主推演控制 REST — O11.4b（前端 O11.7 用）。

`PUT/GET/DELETE /api/v1/sessions/{session_id}/autonomy` — 設定/檢視/清除本 session 的 AI 陣營
指派（存 Redis `session:{id}:ai_config`）。sim runner 於 session **起跑時**讀取並為每個 AI 陣營起
決策 worker。限統裁/白軍/管理（is_omniscient）。

註：目前於 session runner 起跑時讀取一次；對已在跑的 session 需重啟其 runner（或新建 session）
才生效——動態熱掛載列為後續（O11.8）。
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
    _redis(settings.redis_url).set(autonomy_config_key(session_id), cfg.model_dump_json())
    return {"ok": True, "factions": list(cfg.factions), "heartbeat_s": cfg.heartbeat_s}


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
    _redis(settings.redis_url).delete(autonomy_config_key(session_id))
    return {"ok": True}
