"""每陣營 AI 決策 worker — O11.4（SPEC_AUTONOMY §3.1）。

把 O11.1–O11.3 串成一條決策迴路：**取 COP 快照 → 建 faction context → run_faction_turn（LLM
decider + 護欄 G1–G6 + PrecheckFeasibility）→ submit_faction_orders 落 VALIDATED**。

時序解耦（紅線）：LLM 一次 ~15s、tick 1s，故決策 worker 為**獨立 async 任務**（非 pre_tick）；
LLM 在 `asyncio.to_thread` 內跑，不阻塞 Kernel tick 迴圈。worker 只讀熱狀態快照、只產令（經 Order
pipeline），**不寫熱狀態**（single-writer 仍是 Kernel）。

敵情可見性（fog，SPEC_AUTONOMY §1.4）：首版用 ground-truth HOSTILE（活 sim 感測 NoOp）；真偵測
上線後把 `enemy_visibility` 換成 IntelService 即可，worker 邏輯不變。
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai_loop.context import UnitMeta, build_faction_context
from app.ai_loop.opfor import AiTurnResult, OpforDecider, run_faction_turn
from app.ai_loop.orders_bridge import (
    BridgeResult,
    PrecheckFeasibility,
    UnitTargetLocator,
    submit_faction_orders,
)
from app.ai_loop.world_view import allied_units, recent_events
from app.factions.relations import FactionRelations
from app.guardrails import GuardrailGateway, intervention_events
from app.guardrails.schemas import CitationVerifier
from app.models.enums import AiMode
from app.models.tables import TacticalUnit
from app.movement.fuel import load_unit_fuel
from app.movement.mobility import resolve_session_mobility
from app.orders.no_strike import load_no_strike_cells
from app.orders.precheck import PhysicsGateway
from app.state.hot_state import HotStateStore

_LOG = logging.getLogger("app.ai_worker")

_DEFAULT_HEARTBEAT_S = 45.0  # 決策心跳（牆鐘秒）：LLM ~15s，留餘裕；場景可覆寫（D2）。
_STOP_POLL_S = 1.0  # 心跳等待期間輪詢 stop 的粒度（讓收工快速反應）。
_MIN_HEARTBEAT_S = 5.0  # 心跳下限（O11.8）：防誤設過小導致緊迴圈狂打 LLM。
_MAX_TOTAL_ORDERS = 500  # 單 worker 累計落單上限（O11.8 runaway 守衛）：超過即停（異常保護）。


class EnemyVisibility(Protocol):
    """霧化敵情來源。回傳**已依陣營過濾**的敵情清單（呼叫端保證只含本陣營可見者）。"""

    def __call__(
        self, db: Session, session_id: str, faction: str, relations: FactionRelations
    ) -> list[dict[str, Any]]: ...  # pragma: no cover


@dataclass(frozen=True, slots=True)
class DecisionOutcome:
    """一次決策週期結果（供觀測/測試）。"""

    turn: AiTurnResult
    bridge: BridgeResult


def load_unit_meta(db: Session, session_id: str) -> dict[str, UnitMeta]:
    """DB → uid→UnitMeta（faction/designation/type/機動；熱狀態不存這些）。"""
    units = db.scalars(select(TacticalUnit).where(TacticalUnit.session_id == session_id)).all()
    mob = resolve_session_mobility(db, [u.id for u in units])  # #80 批次導出機動（單次查詢）
    # #84：油料剩餘行程（僅自走載具有值）——讓 AI 知道「這台還能開多遠」。
    ranges: dict[str, float | None] = {}
    for u in units:
        f = load_unit_fuel(db, u.id)
        ranges[u.id] = round(f.range_km(), 1) if f.needs_fuel else None
    return {
        u.id: UnitMeta(
            faction=u.faction,
            designation=u.designation,
            unit_type=u.unit_level.value,
            is_fixed=u.is_fixed,
            mobility_profile=mob[u.id].profile,
            speed_kmh=mob[u.id].xc_kmh,
            range_km=ranges.get(u.id),
        )
        for u in units
    }


def ground_truth_enemies(
    db: Session, session_id: str, faction: str, relations: FactionRelations
) -> list[dict[str, Any]]:
    """首版敵情可見性：所有**存活的敵對陣營**單位（ground truth；感測 NoOp 期間的權宜）。

    真偵測（IntelService）上線後以其取代——worker 只依賴 EnemyVisibility 協定，不需改動。
    """
    units = db.scalars(select(TacticalUnit).where(TacticalUnit.session_id == session_id)).all()
    out: list[dict[str, Any]] = []
    for u in units:
        if u.faction == faction or not relations.is_hostile(faction, u.faction):
            continue
        if u.current_strength is not None and float(u.current_strength) <= 0:
            continue  # 已殲滅不列入敵情
        enemy: dict[str, Any] = {
            "unit_id": u.id,
            "faction": u.faction,
            "designation": u.designation,
            "unit_type": u.unit_level.value,
        }
        if u.current_lat is not None and u.current_lng is not None:
            enemy["lat"] = float(u.current_lat)
            enemy["lng"] = float(u.current_lng)
        out.append(enemy)
    return out


def run_decision_cycle(
    *,
    session_id: str,
    faction: str,
    hot: HotStateStore,
    db: Session,
    decider: OpforDecider,
    guardrail: GuardrailGateway,
    phys_gateway: PhysicsGateway,
    relations: FactionRelations,
    mode: AiMode,
    issuer_id: str,
    mission: str = "",
    objectives: list[dict[str, Any]] | None = None,
    tick: int = 0,
    no_strike_hexes: frozenset[str] = frozenset(),
    restricted_fire_hexes: frozenset[str] = frozenset(),
    enemy_visibility: EnemyVisibility = ground_truth_enemies,
    citation_verifier: CitationVerifier | None = None,
    event_sink: Any = None,
) -> DecisionOutcome:
    """一個陣營的一次決策週期（同步；async worker 於 to_thread 內呼叫）。

    快照 → context → run_faction_turn（護欄 + feasibility）→ 落單。回 DecisionOutcome。
    """
    snapshot = hot.get_all()
    unit_meta = load_unit_meta(db, session_id)
    enemies = enemy_visibility(db, session_id, faction, relations)
    # WP-A1：盟軍走共享視圖（非偵測）、近期事件走 Ledger 受眾過濾。
    # faction_for 由已載入的 unit_meta 導出——查無單位回 ""，`event_audience` 會忽略空字串。
    allies = allied_units(db, session_id, faction, relations)
    events = recent_events(
        db,
        session_id,
        faction,
        faction_for=lambda uid: unit_meta[uid].faction if uid in unit_meta else "",
    )
    context = build_faction_context(
        faction=faction,
        tick=tick,
        hot_snapshot=snapshot,
        unit_meta=unit_meta,
        known_enemies=enemies,
        relations=relations,
        allied_units=allies,
        objectives=objectives,
        recent_events=events,
        mission=mission,
    )
    feasibility = PrecheckFeasibility(db, session_id, phys_gateway, relations)
    # WP-A3：禁射格集每週期現讀（白軍可局中增修禁射區，快取會讓變更不生效）。
    # deps 帶進來的值僅作為 fallback（測試/合成想定可直接指定格）。
    zones = load_no_strike_cells(db, session_id)
    turn = run_faction_turn(
        decider,
        guardrail,
        mode=mode,
        context=context,
        no_strike_hexes=zones.no_strike or no_strike_hexes,
        restricted_fire_hexes=zones.restricted or restricted_fire_hexes,
        target_locator=UnitTargetLocator(db, session_id, hot),
        feasibility=feasibility,
        citation_verifier=citation_verifier,
    )
    # WP-A3：護欄攔截落帳。`intervention_events` 自 O6.2 就存在，但**從無 production 呼叫端**
    # → 活執行期從未寫過一筆 GUARDRAIL_INTERVENTION，AAR 的「護欄攔截 N 次」恆為 0、
    # 重播書籤也永遠標不出來。這裡補上（寫入端比照 sim_runtime 的 SESSION_CONCLUDED：
    # 非 Kernel 路徑亦可 append，LedgerWriter 自身處理 seq/tip 衝突）。
    if event_sink is not None:
        events_out = intervention_events(turn.findings, tick, initiator_id=None)
        if events_out:
            event_sink.append(session_id, events_out)

    bridge = BridgeResult()
    if turn.accepted and turn.orders:
        bridge = submit_faction_orders(
            db,
            session_id,
            turn.orders,
            issuer_id=issuer_id,
            gateway=phys_gateway,
            relations=relations,
            tick_source=lambda: tick,
        )
    return DecisionOutcome(turn=turn, bridge=bridge)


@dataclass
class FactionWorkerDeps:
    """一條陣營 worker 的注入依賴（sim_runtime 於裝配時提供）。"""

    session_id: str
    faction: str
    issuer_id: str
    hot: HotStateStore
    db_factory: Callable[[], Session]
    decider: OpforDecider
    guardrail: GuardrailGateway
    phys_gateway: PhysicsGateway
    relations: FactionRelations
    mode: AiMode
    mission: str = ""
    objectives: list[dict[str, Any]] = field(default_factory=list)
    no_strike_hexes: frozenset[str] = frozenset()
    restricted_fire_hexes: frozenset[str] = frozenset()
    tick_source: Callable[[], int] = lambda: 0
    enemy_visibility: EnemyVisibility = ground_truth_enemies
    citation_verifier: CitationVerifier | None = None
    # WP-A3：護欄攔截事件的落帳出口（LedgerWriter）。None＝不落帳（測試/合成想定）。
    event_sink: Any = None


def _cycle_with_db(deps: FactionWorkerDeps) -> DecisionOutcome:
    """開一條短生命期 DB session 跑一次決策週期（在 to_thread 內）。"""
    db = deps.db_factory()
    try:
        outcome = run_decision_cycle(
            session_id=deps.session_id,
            faction=deps.faction,
            hot=deps.hot,
            db=db,
            decider=deps.decider,
            guardrail=deps.guardrail,
            phys_gateway=deps.phys_gateway,
            relations=deps.relations,
            mode=deps.mode,
            issuer_id=deps.issuer_id,
            mission=deps.mission,
            objectives=deps.objectives,
            tick=deps.tick_source(),
            no_strike_hexes=deps.no_strike_hexes,
            restricted_fire_hexes=deps.restricted_fire_hexes,
            enemy_visibility=deps.enemy_visibility,
            event_sink=deps.event_sink,
            citation_verifier=deps.citation_verifier,
        )
        return outcome
    finally:
        db.close()


async def _sleep_or_stop(seconds: float, should_stop: Callable[[], bool]) -> None:
    """睡 `seconds`，但每 _STOP_POLL_S 檢查 should_stop → 收工時快速跳出。"""
    waited = 0.0
    while waited < seconds and not should_stop():
        await asyncio.sleep(min(_STOP_POLL_S, seconds - waited))
        waited += _STOP_POLL_S


def _emit_status(sink: Callable[[dict[str, Any]], None] | None, payload: dict[str, Any]) -> None:
    """把一則決策狀態遙測交給 sink（失敗絕不影響決策迴路——遙測是觀測，非邏輯）。"""
    if sink is None:
        return
    try:
        sink(payload)
    except Exception:
        # 遙測寫入失敗不得中斷 worker（觀測非邏輯）。
        _LOG.debug("AI 狀態遙測寫入失敗，忽略")


async def run_faction_worker(
    deps: FactionWorkerDeps,
    *,
    should_stop: Callable[[], bool],
    heartbeat_s: float = _DEFAULT_HEARTBEAT_S,
    max_total_orders: int = _MAX_TOTAL_ORDERS,  # #93 可調 runaway 上限
    on_cycle: Callable[[DecisionOutcome], None] | None = None,
    status_sink: Callable[[dict[str, Any]], None] | None = None,
    now: Callable[[], float] = time.time,
) -> None:
    """陣營 AI 決策迴路：固定心跳，每週期取快照→LLM→護欄→落單。非 pre_tick（不阻塞 tick）。

    韌性（O11.8）：心跳夾下限（防緊迴圈）；LLM 逾時/失敗 → 記錄後續跑（fallback HOLD，不停）；
    累計落單超上限 → runaway 守衛停止（異常保護）。

    觀測（#79）：`status_sink` 收「思考中／閒置（含下一次決策牆鐘）」遙測供 COP 顯示；
    `now` 為牆鐘來源（worker 本即牆鐘心跳、非決定性 kernel，時間戳屬遙測不涉 SimClock 紅線）。
    """
    heartbeat_s = max(_MIN_HEARTBEAT_S, heartbeat_s)
    _LOG.info(
        "AI worker 啟動：session=%s faction=%s 心跳=%.0fs",
        deps.session_id,
        deps.faction,
        heartbeat_s,
    )
    cycles = 0
    total_submitted = 0
    while not should_stop():
        _emit_status(
            status_sink,
            {
                "state": "thinking",
                "thinking_since": now(),
                "heartbeat_s": heartbeat_s,
                "cycles": cycles,
            },
        )
        try:
            outcome = await asyncio.to_thread(_cycle_with_db, deps)
            b = outcome.bridge
            cycles += 1
            total_submitted += len(b.submitted)
            _emit_status(
                status_sink,
                {
                    "state": "idle",
                    "last_decision_ts": now(),
                    "heartbeat_s": heartbeat_s,
                    "cycles": cycles,
                    "last_submitted": len(b.submitted),
                    "fallback": outcome.turn.fallback_used,
                },
            )
            _LOG.info(
                "AI %s/%s #%d：accepted=%s fallback=%s 落單=%d(累計%d) 拒=%d 略=%d 超量=%d",
                deps.session_id,
                deps.faction,
                cycles,
                outcome.turn.accepted,
                outcome.turn.fallback_used,
                len(b.submitted),
                total_submitted,
                len(b.rejected),
                len(b.skipped),
                b.capped,
            )
            if on_cycle is not None:
                on_cycle(outcome)
            if total_submitted > max_total_orders:
                _LOG.warning(
                    "AI worker runaway 守衛觸發：session=%s faction=%s 累計落單 %d 超上限 %d，停止",
                    deps.session_id,
                    deps.faction,
                    total_submitted,
                    max_total_orders,
                )
                return
        except asyncio.CancelledError:
            raise
        except Exception:
            _LOG.exception(
                "AI worker 週期失敗：session=%s faction=%s", deps.session_id, deps.faction
            )
            # 失敗週期也回 idle（帶下一次心跳）——否則狀態會卡在「思考中」直到下輪。
            _emit_status(
                status_sink,
                {
                    "state": "idle",
                    "last_decision_ts": now(),
                    "heartbeat_s": heartbeat_s,
                    "cycles": cycles,
                    "last_submitted": 0,
                    "fallback": True,
                },
            )
        await _sleep_or_stop(heartbeat_s, should_stop)
    _LOG.info("AI worker 收工：session=%s faction=%s", deps.session_id, deps.faction)
