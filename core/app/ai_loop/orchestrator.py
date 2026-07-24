"""自主推演編排 — O11.4b（SPEC_AUTONOMY §3.1）。

`sim_runtime` 於 session 起跑時呼叫 `start_ai_workers`：讀「本 session 的自主 AI 指派」
（Redis `session:{id}:ai_config`，由前端/控制端設定；**缺 → 不啟動，既有 session 不受影響**）
＋ #54 系統 AI 設定（模式 / Ollama 位址），為每個 AI 陣營建 issuer participant + 起一條決策 worker。

安全：AI issuer 以 role=COMMANDER 建 SessionParticipant（**非** override 角色）→ 仍受 faction
檢查，只能命令本陣營單位（LLM 幻想命令他方 → submit 權限擋）。
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai_loop.decider import make_llm_faction_decider
from app.ai_loop.opfor import OpforDecider
from app.ai_loop.worker import FactionWorkerDeps, run_faction_worker
from app.config import Settings
from app.factions.relations import FactionRelations
from app.guardrails import GuardrailGateway
from app.guardrails.modes import resolve_ai_mode
from app.models.enums import AiMode, UserRole
from app.models.tables import SessionParticipant, SystemConfiguration, User
from app.orders.precheck import PhysicsGateway
from app.state.hot_state import HotStateStore

_LOG = logging.getLogger("app.ai_orchestrator")


def autonomy_config_key(session_id: str) -> str:
    """Redis 鍵：本 session 的自主 AI 指派（JSON）。控制端寫、sim 起跑讀。"""
    return f"session:{session_id}:ai_config"


def _tick_key(session_id: str) -> str:
    return f"session:{session_id}:tick"


def read_system_ai(db: Session) -> dict[str, Any]:
    """讀 #54 系統設定的 AI 區塊（mode / llm_base_url / llm_model / llm_api_key）。"""
    cfg = db.execute(select(SystemConfiguration).limit(1)).scalars().first()
    ic = (cfg.integration_config or {}) if cfg is not None else {}
    ai = ic.get("ai") if isinstance(ic, dict) else None
    return ai if isinstance(ai, dict) else {}


def ensure_ai_participant(db: Session, session_id: str, faction: str) -> str:
    """get-or-create 本陣營的 AI 指揮官 participant（issuer）。

    role=COMMANDER（非 override）→ 仍受 faction 檢查。以 `ai-{faction}` 不可登入帳號
    （password_hash 非合法 Argon2 → 無法登入）掛載，供 AAR/稽核追溯此令由 AI 下。回 participant id。
    """
    username = f"ai-{faction}"
    user = db.scalar(select(User).where(User.username == username))
    if user is None:
        user = User(username=username, password_hash="!ai-no-login", role=UserRole.COMMANDER)
        db.add(user)
        db.flush()
    part = db.scalar(
        select(SessionParticipant).where(
            SessionParticipant.user_id == user.id,
            SessionParticipant.session_id == session_id,
        )
    )
    if part is None:
        part = SessionParticipant(
            user_id=user.id,
            session_id=session_id,
            faction=faction,
            role=UserRole.COMMANDER,
            unit_scope=[],
        )
        db.add(part)
    db.commit()
    return part.id


def _read_tick(client: Any, session_id: str) -> int:
    try:
        raw = client.get(_tick_key(session_id))
        return int(raw) if raw is not None else 0
    except (ValueError, TypeError):
        return 0


def start_ai_workers(
    *,
    session_id: str,
    hot: HotStateStore,
    redis_client: Any,
    db_factory: Callable[[], Session],
    gateway: PhysicsGateway,
    should_stop: Callable[[], bool],
    settings: Settings | None = None,
    decider_factory: Callable[..., OpforDecider] = make_llm_faction_decider,
) -> list[asyncio.Task[None]]:
    """讀自主指派 + #54 AI 設定，為每個 AI 陣營起一條 decision worker。

    缺指派 / AI_OFF / 無 base_url → 回 []（不啟動）。`decider_factory` 可注入（測試用 stub）。
    """
    raw = redis_client.get(autonomy_config_key(session_id))
    if not raw:
        return []  # 未指派自主 AI → 不啟動（既有 session 不受影響）
    try:
        cfg = json.loads(raw)
    except (ValueError, TypeError):
        _LOG.warning("session %s ai_config 非法 JSON，忽略", session_id)
        return []
    factions_cfg = cfg.get("factions") if isinstance(cfg, dict) else None
    if not isinstance(factions_cfg, dict) or not factions_cfg:
        return []

    settings = settings or Settings()
    with db_factory() as db:
        ai = read_system_ai(db)
    mode = resolve_ai_mode(ai.get("mode"), settings.ai_mode)
    base_url = str(ai.get("llm_base_url") or "")
    if mode == AiMode.AI_OFF or not base_url:
        _LOG.warning(
            "自主 AI 未啟動（session %s）：mode=%s base_url_set=%s",
            session_id,
            mode.value,
            bool(base_url),
        )
        return []

    decider = decider_factory(
        base_url=base_url,
        model=str(ai.get("llm_model") or ""),
        api_key=str(ai.get("llm_api_key") or ""),
        mode=mode,
    )
    guardrail = GuardrailGateway()
    # 首版預設全 HOSTILE（多陣營敵我）；alliance/NEUTRAL 之後由 scenario relations 注入。
    relations = FactionRelations()
    heartbeat = float(cfg.get("heartbeat_s") or 45.0)

    tasks: list[asyncio.Task[None]] = []
    for faction, fc_raw in factions_cfg.items():
        fc = fc_raw if isinstance(fc_raw, dict) else {}
        with db_factory() as db:
            issuer_id = ensure_ai_participant(db, session_id, str(faction))
        deps = FactionWorkerDeps(
            session_id=session_id,
            faction=str(faction),
            issuer_id=issuer_id,
            hot=hot,
            db_factory=db_factory,
            decider=decider,
            guardrail=guardrail,
            phys_gateway=gateway,
            relations=relations,
            mode=mode,
            mission=str(fc.get("mission") or ""),
            objectives=list(fc.get("objectives") or []),
            # session_id 於本迴圈固定（只有 faction 變），直接閉包即可。
            tick_source=lambda: _read_tick(redis_client, session_id),
        )
        tasks.append(
            asyncio.create_task(
                run_faction_worker(deps, should_stop=should_stop, heartbeat_s=heartbeat)
            )
        )
        _LOG.info(
            "自主 AI worker 起：session=%s faction=%s 心跳=%.0fs", session_id, faction, heartbeat
        )
    return tasks
