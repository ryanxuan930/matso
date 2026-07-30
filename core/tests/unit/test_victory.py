"""O11.5 勝負判定：context 組建、預設最後存活條件、監視器收場。"""

from __future__ import annotations

import asyncio
from typing import Any

from _order_fakes import seed_world
from sqlalchemy.orm import Session, sessionmaker

from app.ai_loop.context import UnitMeta
from app.ai_loop.victory import (
    build_trigger_context,
    last_standing_conditions,
    resolve_victory_conditions,
    run_victory_monitor,
)
from app.scenario.triggers import check_victory
from app.state.hot_state import InMemoryHotState


def _meta() -> dict[str, UnitMeta]:
    return {
        "b1": UnitMeta(faction="BLUE", designation="B1", echelon="PLATOON"),
        "r1": UnitMeta(faction="RED", designation="R1", echelon="PLATOON"),
    }


def test_build_context_sums_strength_and_positions() -> None:
    hot = {
        "b1": {"strength": 100.0, "lat": 24.0, "lng": 121.0},
        "r1": {"strength": 40.0, "lat": 24.1, "lng": 121.1},
    }
    ctx = build_trigger_context(hot, _meta(), tick=5)
    assert ctx.faction_strength == {"BLUE": 100.0, "RED": 40.0}
    assert ("BLUE", 24.0, 121.0) in ctx.unit_positions
    assert ctx.tick == 5


def test_build_context_absent_unit_counts_as_zero() -> None:
    # r1 不在熱狀態（未 seed）→ RED 戰力 0（供 faction_eliminated 正確判定）。
    ctx = build_trigger_context({"b1": {"strength": 100.0}}, _meta(), tick=0)
    assert ctx.faction_strength == {"BLUE": 100.0, "RED": 0.0}


def test_last_standing_conditions() -> None:
    conds = last_standing_conditions(["BLUE", "RED"])
    assert len(conds) == 2
    blue = next(c for c in conds if c["faction"] == "BLUE")
    assert blue["condition"]["of"][0] == {"type": "faction_eliminated", "faction": "RED"}


def test_resolve_prefers_explicit() -> None:
    explicit = [{"faction": "BLUE", "condition": {"type": "time", "at_tick": 10}}]
    assert resolve_victory_conditions(explicit, ["BLUE", "RED"]) is explicit
    assert resolve_victory_conditions(None, ["BLUE", "RED"])  # 非空預設


def test_check_victory_last_standing() -> None:
    # RED 全滅 → BLUE 勝（最後存活）。
    conds = last_standing_conditions(["BLUE", "RED"])
    ctx = build_trigger_context({"b1": {"strength": 100.0}}, _meta(), tick=3)
    assert check_victory(conds, ctx) == ["BLUE"]


def test_no_winner_while_both_alive() -> None:
    conds = last_standing_conditions(["BLUE", "RED"])
    ctx = build_trigger_context(
        {"b1": {"strength": 100.0}, "r1": {"strength": 50.0}}, _meta(), tick=3
    )
    assert check_victory(conds, ctx) == []


def test_victory_monitor_concludes(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)  # 藍/紅各一單位（DB）
    hot = InMemoryHotState()
    hot.update_unit(world.blue_unit_id, {"strength": 100.0, "lat": 23.75, "lng": 121.25})
    # 紅方不在熱狀態 → 戰力 0 → 已殲滅 → 藍勝
    conds = last_standing_conditions(["BLUE", "RED"])
    concluded: list[Any] = []

    async def _run() -> None:
        await run_victory_monitor(
            session_id=world.session_id,
            hot=hot,
            db_factory=session_factory,
            victory_conditions=conds,
            on_conclude=lambda winners, tick: concluded.append((winners, tick)),
            should_stop=lambda: False,
            tick_source=lambda: 42,
            poll_s=0.01,
        )

    asyncio.run(_run())
    assert concluded == [(["BLUE"], 42)]
