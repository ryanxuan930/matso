"""觸發 DSL + MSEL 引擎 + 勝利判定（O7.2，SPEC §11.3）。"""

from __future__ import annotations

import pytest

from app.scenario.triggers import (
    MselEngine,
    MselEntry,
    TriggerContext,
    TriggerError,
    check_victory,
    evaluate_condition,
)


def _ctx(tick: int, **over: object) -> TriggerContext:
    return TriggerContext(tick=tick, **over)  # type: ignore[arg-type]


def test_time_condition() -> None:
    assert evaluate_condition({"type": "time", "at_tick": 30}, _ctx(30))
    assert evaluate_condition({"type": "time", "at_tick": 30}, _ctx(31))
    assert not evaluate_condition({"type": "time", "at_tick": 30}, _ctx(29))


def test_faction_eliminated_and_strength_below() -> None:
    ctx = _ctx(1, faction_strength={"RED": 0.0, "BLUE": 40.0})
    assert evaluate_condition({"type": "faction_eliminated", "faction": "RED"}, ctx)
    assert not evaluate_condition({"type": "faction_eliminated", "faction": "BLUE"}, ctx)
    assert evaluate_condition({"type": "strength_below", "faction": "BLUE", "value": 50}, ctx)
    assert not evaluate_condition({"type": "strength_below", "faction": "BLUE", "value": 30}, ctx)


def test_unit_in_region() -> None:
    ctx = _ctx(1, unit_positions=[("RED", 23.5, 121.0), ("BLUE", 20.0, 100.0)])
    cond = {"type": "unit_in_region", "faction": "RED", "bbox": [120.5, 23.0, 121.5, 24.0]}
    assert evaluate_condition(cond, ctx)
    cond_blue = {**cond, "faction": "BLUE"}
    assert not evaluate_condition(cond_blue, ctx)  # BLUE 不在此區


def test_combinators() -> None:
    ctx = _ctx(30, faction_strength={"RED": 10.0})
    both = {
        "type": "all",
        "of": [
            {"type": "time", "at_tick": 30},
            {"type": "strength_below", "faction": "RED", "value": 50},
        ],
    }
    assert evaluate_condition(both, ctx)
    either = {
        "type": "any",
        "of": [
            {"type": "time", "at_tick": 999},
            {"type": "faction_eliminated", "faction": "RED"},  # RED=10 → False
        ],
    }
    assert not evaluate_condition(either, ctx)


def test_unknown_condition_raises() -> None:
    with pytest.raises(TriggerError):
        evaluate_condition({"type": "nope"}, _ctx(1))


def test_check_victory() -> None:
    vcs = [
        {"faction": "BLUE", "condition": {"type": "faction_eliminated", "faction": "RED"}},
        {"faction": "RED", "condition": {"type": "faction_eliminated", "faction": "BLUE"}},
    ]
    ctx = _ctx(1, faction_strength={"RED": 0.0, "BLUE": 100.0})
    assert check_victory(vcs, ctx) == ["BLUE"]


# ---- MSEL 引擎 ----


def _engine(entries: list[MselEntry], strengths: dict[int, dict[str, float]]) -> MselEngine:
    return MselEngine(entries, lambda tick: _ctx(tick, faction_strength=strengths.get(tick, {})))


def test_msel_time_fires_once() -> None:
    entry = MselEntry(
        id="reinf",
        trigger={"type": "time", "at_tick": 30},
        inject={"event_type": "REINFORCEMENT", "faction": "BLUE", "payload": {"n": 1}},
    )
    eng = _engine([entry], {})

    class T:
        def __init__(self, tick: int) -> None:
            self.tick = tick

    assert eng.check(T(29)) == []  # 未到
    fired = eng.check(T(30))
    assert len(fired) == 1
    ev = fired[0]
    assert ev.event_type == "REINFORCEMENT" and ev.tick == 30
    assert ev.ai_decision == {"msel_id": "reinf", "source": "MSEL", "faction": "BLUE", "n": 1}
    assert eng.check(T(31)) == []  # once → 不再觸


def test_msel_condition_edge_triggered() -> None:
    entry = MselEntry(
        id="collapse",
        trigger={"type": "strength_below", "faction": "RED", "value": 50},
        inject={"event_type": "FORCE_COLLAPSE"},
    )
    eng = _engine([entry], {1: {"RED": 80.0}, 2: {"RED": 40.0}, 3: {"RED": 30.0}})
    assert eng.check(1) == []  # 80 ≥ 50
    assert len(eng.check(2)) == 1  # 40 < 50 → 觸發
    assert eng.check(3) == []  # once → 不重觸（即使仍成立）
