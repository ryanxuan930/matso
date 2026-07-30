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
from app.ai_loop.worker import FactionWorkerDeps, ground_truth_enemies, run_faction_worker
from app.ai_loop.world_view import contacts_from_intel
from app.config import Settings
from app.factions.session_store import load_session_relations
from app.guardrails import GuardrailGateway
from app.guardrails.modes import resolve_ai_mode
from app.models.enums import AiMode, UserRole
from app.models.tables import SessionParticipant, SystemConfiguration, User
from app.orders.precheck import PhysicsGateway
from app.sim_params import load_sim_params
from app.state.hot_state import HotStateStore, session_tick_key
from app.state.ledger import LedgerWriter

_LOG = logging.getLogger("app.ai_orchestrator")


def autonomy_config_key(session_id: str) -> str:
    """Redis 鍵：本 session 的自主 AI 指派（JSON）。控制端寫、sim 起跑讀。"""
    return f"session:{session_id}:ai_config"


def ai_status_key(session_id: str) -> str:
    """Redis 鍵（hash，field=faction）：本局各陣營 AI 決策心跳狀態。worker 寫、COP 讀（#79）。"""
    return f"session:{session_id}:ai_status"


def _make_status_sink(
    client: Any, session_id: str, faction: str
) -> Callable[[dict[str, Any]], None]:
    """建一則陣營專屬遙測寫入器：把決策狀態寫進 ai_status hash 的 faction field（單寫者無競態）。"""
    key = ai_status_key(session_id)

    def _sink(payload: dict[str, Any]) -> None:
        client.hset(key, faction, json.dumps(payload))

    return _sink


def _tick_key(session_id: str) -> str:
    return session_tick_key(session_id)


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
        # WP-F3：稽核紀錄要對得回是哪一局的決策。
        session_id=session_id,
    )
    guardrail = GuardrailGateway()
    # #98：改讀該局持久化的關係矩陣（原本寫死全 HOSTILE，導致 AI 會攻擊盟軍）。
    # 未宣告的局仍回全 HOSTILE 預設 → 既有行為不變。
    with db_factory() as db:
        relations = load_session_relations(db, session_id)
    # #93：預設心跳改讀全域設定（該局 ai_config 若有指定仍優先）。
    with db_factory() as db:
        _sim = load_sim_params(db)
    heartbeat = float(cfg.get("heartbeat_s") or _sim.ai_heartbeat_s)
    # WP-A1：AI 敵情預設走**真實偵測**（IntelContact 投影，同 GET /intel 語義）。
    # `ai_ground_truth=true` 是刻意保留的退回開關——供「有/無迷霧」對照實驗（SPEC_V2 WP-D1），
    # 開啟即回到改版前的全知行為。預設 false：迷霧對 AI 與對人一致。
    use_ground_truth = bool(cfg.get("ai_ground_truth"))
    enemy_visibility = ground_truth_enemies if use_ground_truth else contacts_from_intel
    if use_ground_truth:
        _LOG.warning("session %s 的 AI 走 ground truth 敵情（對照實驗模式）", session_id)

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
            enemy_visibility=enemy_visibility,  # WP-A1：預設真實偵測（見上）
            # WP-A3：護欄攔截事件落帳（禁射格集由 worker 每週期自 DB 現讀，故不在此傳）。
            event_sink=LedgerWriter(db_factory),
            mission=str(fc.get("mission") or ""),
            objectives=list(fc.get("objectives") or []),
            # session_id 於本迴圈固定（只有 faction 變），直接閉包即可。
            tick_source=lambda: _read_tick(redis_client, session_id),
        )
        tasks.append(
            asyncio.create_task(
                run_faction_worker(
                    deps,
                    should_stop=should_stop,
                    heartbeat_s=heartbeat,
                    max_total_orders=_sim.ai_max_orders,  # #93 可調 runaway 上限
                    status_sink=_make_status_sink(redis_client, session_id, str(faction)),
                )
            )
        )
        _LOG.info(
            "自主 AI worker 起：session=%s faction=%s 心跳=%.0fs", session_id, faction, heartbeat
        )
    return tasks
