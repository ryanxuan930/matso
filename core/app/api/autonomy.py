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
import time
from functools import lru_cache
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai_loop.orchestrator import ai_status_key, autonomy_config_key
from app.api.deps import get_current_user, get_db, get_settings
from app.auth.schemas import CurrentUser
from app.cache import make_redis
from app.config import Settings
from app.errors import AuthForbiddenError
from app.models.tables import SessionParticipant
from app.sim_control import session_restart_key
from app.stream.faction_filter import is_omniscient

router = APIRouter(prefix="/api/v1/sessions", tags=["autonomy"])

# AI 狀態逾時判定（#79）：距離最後遙測超過此秒數 → 視為 offline（runner 已停/重啟或 worker 卡死）。
_STALE_FLOOR_S = 300.0


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
    # WP-A1 對照實驗開關：true ＝ AI 改用 ground truth 敵情（全知，迷霧不適用）。
    # 預設 false（AI 與人一樣受迷霧限制）。**未宣告的欄位會被 pydantic 丟掉**，故必須列在這裡，
    # 否則前端/白軍設了也存不進 Redis 的 ai_config。
    ai_ground_truth: bool = False


class AutonomySaved(AutonomyConfig):
    """PUT 的回應＝**存進去之後的那份設定**，加上兩個旗標。

    ⚠ 這裡原本回 `{"factions": list(cfg.factions), ...}`——`list(dict)` 取的是**鍵**，
    於是同一個資源的同一個欄位，PUT 回字串陣列、GET 回物件。存檔後重載會拿到兩種形狀。
    目前沒炸只是因為前端丟棄了 PUT 的回應；那是運氣不是設計。
    繼承 `AutonomyConfig` 讓兩邊的形狀由型別系統保證一致，不靠人記得。
    """

    ok: bool = True
    restarted: bool = True


class AutonomyCleared(BaseModel):
    """DELETE 的回應。沒有設定可回——它剛被刪掉。"""

    ok: bool = True
    restarted: bool = True


@router.put("/{session_id}/autonomy", response_model=AutonomySaved)
def set_autonomy(
    session_id: str,
    cfg: AutonomyConfig,
    user: CurrentUser = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> AutonomySaved:
    _require_admin(user)
    r = _redis(settings.redis_url)
    r.set(autonomy_config_key(session_id), cfg.model_dump_json())
    # 請求 runner 重啟以立即讀取指派（數秒內生效；戰局熱狀態於 Redis 不中斷）。
    r.set(session_restart_key(session_id), "1")
    # **回存進去的那一份**（經 pydantic 正規化後），形狀與 GET 完全相同。
    return AutonomySaved(**cfg.model_dump())


@router.get("/{session_id}/autonomy", response_model=AutonomyConfig)
def get_autonomy(
    session_id: str,
    user: CurrentUser = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> AutonomyConfig:
    _require_admin(user)
    raw = _redis(settings.redis_url).get(autonomy_config_key(session_id))
    if not raw:
        return AutonomyConfig()
    try:
        return AutonomyConfig(**json.loads(raw))
    except (ValueError, TypeError):
        # 壞掉的 Redis 值不該讓白軍主控台整頁掛掉——回預設，讓他重設一次。
        return AutonomyConfig()


@router.delete("/{session_id}/autonomy", response_model=AutonomyCleared)
def clear_autonomy(
    session_id: str,
    user: CurrentUser = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> AutonomyCleared:
    _require_admin(user)
    r = _redis(settings.redis_url)
    r.delete(autonomy_config_key(session_id))
    r.delete(ai_status_key(session_id))  # 清除 AI 狀態遙測（無 AI 即無狀態）
    r.set(session_restart_key(session_id), "1")  # runner 重啟 → 停掉 AI worker
    return AutonomyCleared()


def _faction_status(faction: str, raw: Any, now: float) -> dict[str, Any]:
    """把一個陣營的原始遙測 payload 換算為對外狀態（含下一次決策倒數 / 逾時 offline）。"""
    try:
        p = json.loads(raw) if isinstance(raw, (str, bytes)) else (raw or {})
    except (ValueError, TypeError):
        p = {}
    heartbeat = float(p.get("heartbeat_s") or 45.0)
    stale = max(heartbeat * 3.0, _STALE_FLOOR_S)
    state = str(p.get("state") or "offline")
    out: dict[str, Any] = {
        "faction": faction,
        "state": "offline",
        "seconds_until_next": None,
        "heartbeat_s": heartbeat,
        "thinking_since_s": None,
        "last_submitted": p.get("last_submitted"),
        # 累計送出令數。**與 `last_submitted` 一起帶**：後者只說「上一週期」，
        # 白軍要判斷「這個 AI 到底有沒有在動」看的是累計值。
        "total_submitted": p.get("total_submitted"),
        # 失控保護的**分母**。少了它，前端就算讀到累計數也不知道離上限還有多遠——
        # 而這個守衛觸發時 AI 會直接停止決策，白軍需要事先看得到。
        "max_total_orders": p.get("max_total_orders"),
        "cycles": p.get("cycles"),
    }
    if state == "thinking":
        since = float(p.get("thinking_since") or now)
        elapsed = max(0.0, now - since)
        if elapsed <= stale:  # 思考過久 → 視為卡死 offline
            out["state"] = "thinking"
            out["thinking_since_s"] = round(elapsed, 1)
    elif state == "idle":
        last = float(p.get("last_decision_ts") or 0.0)
        if now - last <= stale:  # 太久沒更新 → runner 已停/重啟 → offline
            out["state"] = "idle"
            out["seconds_until_next"] = round(max(0.0, last + heartbeat - now), 1)
    return out


@router.get("/{session_id}/ai-status")
def get_ai_status(
    session_id: str,
    user: CurrentUser = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """本局各陣營 AI 決策心跳狀態（#79）。faction-scoped：全知見全部，其餘僅見自己參與陣營。"""
    raw_map = _redis(settings.redis_url).hgetall(ai_status_key(session_id)) or {}
    now = time.time()
    # faction 過濾（fog：不得窺知敵方 AI 節奏）：全知 → None（全部）；否則限本人在此局的陣營。
    visible: set[str] | None = None
    if not is_omniscient(user.role):
        rows = db.execute(
            select(SessionParticipant.faction).where(
                SessionParticipant.user_id == user.id,
                SessionParticipant.session_id == session_id,
            )
        ).scalars()
        visible = {str(f) for f in rows}
    factions: list[dict[str, Any]] = []
    for key, raw in raw_map.items():
        faction = key.decode() if isinstance(key, bytes) else str(key)
        if visible is not None and faction not in visible:
            continue
        factions.append(_faction_status(faction, raw, now))
    factions.sort(key=lambda f: f["faction"])
    return {"factions": factions}
