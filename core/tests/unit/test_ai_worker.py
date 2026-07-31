"""O11.4 陣營 AI 決策 worker：敵情可見性、決策週期端到端（context→護欄→落單）、心跳迴圈。"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from _order_fakes import FakeGateway, seed_world
from sqlalchemy.orm import Session, sessionmaker

from app.ai_loop.worker import (
    FactionWorkerDeps,
    ground_truth_enemies,
    load_unit_meta,
    run_decision_cycle,
    run_faction_worker,
)
from app.factions.relations import FactionRelations, Relation
from app.guardrails import GuardrailGateway
from app.models.enums import AiMode, OrderStatus
from app.models.tables import Order, TacticalUnit
from app.state.hot_state import InMemoryHotState

_REASONING = (
    "1. 判斷態勢：當面之敵位於我方推進軸線，需先行處置以確保任務達成與部隊安全。\n"
    "2. 確立意圖：以現有部隊向目標區推進並保持接觸，伺機殲敵。\n"
    "3. 配置命令：令前沿單位向指定位置機動，其餘維持警戒。"
)


def _decision(orders: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "reasoning_chain": _REASONING,
        "confidence": 0.7,
        "cited_documents": [],
        "intent": "向目標推進",
        "orders": orders,
        "ihl_self_check": {"civilian_risk_assessed": True},
    }


class _StubDecider:
    """回固定 decision 文字並記錄看到的 context（不打真 LLM）。"""

    def __init__(self, orders: list[dict[str, Any]] | None) -> None:
        self._orders = orders
        self.seen_context: dict[str, Any] | None = None

    def decide(self, context: dict[str, Any], *, feedback: str | None = None) -> dict[str, Any]:
        self.seen_context = context
        if self._orders is None:
            return {}  # 迫使 G1 擋下 → fallback
        return _decision(self._orders)


def _relations() -> FactionRelations:
    return FactionRelations([("BLUE", "RED", Relation.HOSTILE)])


def _hot(world) -> InMemoryHotState:  # type: ignore[no-untyped-def]
    hot = InMemoryHotState()
    hot.update_unit(
        world.blue_unit_id, {"lat": 23.75, "lng": 121.25, "strength": 100.0, "health": 100.0}
    )
    hot.update_unit(
        world.red_unit_id, {"lat": 23.76, "lng": 121.26, "strength": 100.0, "health": 100.0}
    )
    return hot


# ---- 敵情可見性 ----


def test_ground_truth_enemies_excludes_own(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    with session_factory() as db:
        enemies = ground_truth_enemies(db, world.session_id, "BLUE", _relations())
    factions = {e["faction"] for e in enemies}
    assert factions == {"RED"}  # 只見敵、不見己


def test_ground_truth_enemies_excludes_dead(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    with session_factory() as db:
        red = db.get(TacticalUnit, world.red_unit_id)
        assert red is not None
        red.current_strength = 0.0  # 殲滅
        db.commit()
        enemies = ground_truth_enemies(db, world.session_id, "BLUE", _relations())
    assert enemies == []  # 已殲滅不列入敵情


def test_load_unit_meta(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    with session_factory() as db:
        meta = load_unit_meta(db, world.session_id)
    assert meta[world.blue_unit_id].faction == "BLUE"
    assert meta[world.red_unit_id].faction == "RED"


# ---- 決策週期端到端 ----


def _cycle(world, db, decider, orders_issuer=None, enemy_visibility=None):  # type: ignore[no-untyped-def]
    """跑一輪決策。

    ⚠ `enemy_visibility` **要明傳**。它的預設值已改為 fail-closed（迷霧），
    生產端（orchestrator）也一律明傳——測試若靠預設值，測的就不是任何真實組態。
    要驗全知那一側就自己把 `ground_truth_enemies` 傳進來，讓意圖寫在測試裡。
    """
    kw = {} if enemy_visibility is None else {"enemy_visibility": enemy_visibility}
    return run_decision_cycle(
        session_id=world.session_id,
        faction="BLUE",
        hot=_hot(world),
        db=db,
        decider=decider,
        guardrail=GuardrailGateway(),
        phys_gateway=FakeGateway(reachable=True, visible=True),
        relations=_relations(),
        mode=AiMode.AI_BARE,
        issuer_id=orders_issuer or world.blue_issuer_id,
        **kw,
    )


def test_cycle_submits_move_and_sees_enemy(session_factory: sessionmaker[Session]) -> None:
    """對照實驗那一側：明確要求 ground truth 敵情時，context 裡就該有 RED。

    **明傳 `ground_truth_enemies` 是刻意的**——這條驗的是「開了全知開關會怎樣」，
    而不是「預設會怎樣」。預設已改為 fail-closed（迷霧），靠預設值寫的測試
    會在別人調整預設時默默改變自己在測什麼。
    """
    from app.ai_loop.worker import ground_truth_enemies

    world = seed_world(session_factory)
    decider = _StubDecider(
        [{"unit_id": world.blue_unit_id, "order_type": "MOVE", "target_h3": "8a2a1072b59ffff"}]
    )
    with session_factory() as db:
        outcome = _cycle(world, db, decider, enemy_visibility=ground_truth_enemies)
        assert outcome.turn.accepted is True
        assert len(outcome.bridge.submitted) == 1
        order = db.get(Order, outcome.bridge.submitted[0])
        assert order is not None and order.status == OrderStatus.VALIDATED
    # 決策時 context 已含（ground-truth）敵情 RED
    assert decider.seen_context is not None
    assert any(e["faction"] == "RED" for e in decider.seen_context["known_enemies"])
    assert {u["unit_id"] for u in decider.seen_context["own_units"]} == {world.blue_unit_id}


def test_cycle_fallback_on_garbage(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    with session_factory() as db:
        outcome = _cycle(world, db, _StubDecider(None))
    assert outcome.turn.accepted is False
    assert outcome.turn.fallback_used is True
    assert outcome.bridge.submitted == []


def test_cycle_rejects_commanding_enemy_unit(session_factory: sessionmaker[Session]) -> None:
    # LLM 幻想命令敵方單位 → G3 物理可過，但 submit 的權限檢查（issuer=藍、單位=紅）擋下。
    world = seed_world(session_factory)
    decider = _StubDecider(
        [{"unit_id": world.red_unit_id, "order_type": "MOVE", "target_h3": "8a2a1072b59ffff"}]
    )
    with session_factory() as db:
        outcome = _cycle(world, db, decider)
    assert outcome.bridge.submitted == []
    assert len(outcome.bridge.rejected) == 1  # 權限拒（不可對他方單位下令）


# ---- 心跳迴圈 ----


def test_worker_runs_one_cycle_then_stops(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    decider = _StubDecider(
        [{"unit_id": world.blue_unit_id, "order_type": "MOVE", "target_h3": "8a2a1072b59ffff"}]
    )
    deps = FactionWorkerDeps(
        session_id=world.session_id,
        faction="BLUE",
        issuer_id=world.blue_issuer_id,
        hot=_hot(world),
        db_factory=session_factory,
        decider=decider,
        guardrail=GuardrailGateway(),
        phys_gateway=FakeGateway(reachable=True, visible=True),
        relations=_relations(),
        mode=AiMode.AI_BARE,
    )
    cycles: list[Any] = []
    stopped = {"v": False}

    def should_stop() -> bool:
        return stopped["v"]

    def on_cycle(outcome: Any) -> None:
        cycles.append(outcome)
        stopped["v"] = True  # 跑完一輪即收工

    asyncio.run(
        run_faction_worker(deps, should_stop=should_stop, heartbeat_s=0.01, on_cycle=on_cycle)
    )
    assert len(cycles) == 1
    assert len(cycles[0].bridge.submitted) == 1


def test_worker_emits_thinking_then_idle_status(
    session_factory: sessionmaker[Session],
) -> None:
    """#79：每週期先發 thinking、完成後發 idle（含心跳與落單數）供 COP 倒數。"""
    world = seed_world(session_factory)
    decider = _StubDecider(
        [{"unit_id": world.blue_unit_id, "order_type": "MOVE", "target_h3": "8a2a1072b59ffff"}]
    )
    deps = FactionWorkerDeps(
        session_id=world.session_id,
        faction="BLUE",
        issuer_id=world.blue_issuer_id,
        hot=_hot(world),
        db_factory=session_factory,
        decider=decider,
        guardrail=GuardrailGateway(),
        phys_gateway=FakeGateway(reachable=True, visible=True),
        relations=_relations(),
        mode=AiMode.AI_BARE,
    )
    statuses: list[dict[str, Any]] = []
    stopped = {"v": False}
    clock = {"t": 1000.0}

    def should_stop() -> bool:
        return stopped["v"]

    def on_cycle(_outcome: Any) -> None:
        stopped["v"] = True  # 跑完一輪即收工

    def fake_now() -> float:
        clock["t"] += 1.0
        return clock["t"]

    asyncio.run(
        run_faction_worker(
            deps,
            should_stop=should_stop,
            heartbeat_s=5.0,
            on_cycle=on_cycle,
            status_sink=statuses.append,
            now=fake_now,
        )
    )
    assert [s["state"] for s in statuses[:2]] == ["thinking", "idle"]
    idle = statuses[1]
    assert idle["heartbeat_s"] == 5.0
    assert idle["last_submitted"] == 1
    assert "last_decision_ts" in idle


def test_decision_is_json_parseable_shape() -> None:
    # 保護：_decision 產出的結構可序列化（模擬真 LLM 回傳文字前的 dict）。
    json.dumps(_decision([]), ensure_ascii=False)
