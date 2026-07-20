"""移動執行（O3.4）——admit + 逐 tick 推進、抵達、地形中斷、油料耗盡、速度、確定性。

用假 OrderStore / PathPlanner + InMemoryHotState，不需 DB/terrain。
"""

from __future__ import annotations

import h3
import pytest

from app.engine.clock import SimTime
from app.movement.system import MoveCommand, MovementSystem
from app.state.hot_state import InMemoryHotState

_START = h3.latlng_to_cell(23.75, 121.25, 8)
_END = h3.latlng_to_cell(23.78, 121.28, 8)
_PATH = h3.grid_path_cells(_START, _END)  # 相鄰 h3 鏈（含起訖）


class FakeOrderStore:
    def __init__(self, commands: list[MoveCommand]) -> None:
        self._pending = list(commands)
        self.executing: list[str] = []
        self.completed: dict[str, int] = {}

    def pending_moves(self, session_id: str) -> list[MoveCommand]:
        drained, self._pending = self._pending, []  # 拉一次後即非 VALIDATED
        return drained

    def mark_executing(self, order_id: str) -> None:
        self.executing.append(order_id)

    def mark_completed(self, order_id: str, tick: int) -> None:
        self.completed[order_id] = tick


class FakePlanner:
    def __init__(self, path: list[str]) -> None:
        self._path = path

    def plan(self, from_h3: str, to_h3: str, mobility_profile: str) -> list[str]:
        return list(self._path)


def _cmd(unit: str = "u1", order: str = "o1") -> MoveCommand:
    return MoveCommand(
        order_id=order, unit_id=unit, from_h3=_START, to_h3=_END, mobility_profile="FOOT"
    )


def _time(tick: int) -> SimTime:
    return SimTime(tick=tick, sim_time_ms=tick * 1000)


async def _run(system: MovementSystem, ticks: int) -> list[str]:
    types: list[str] = []
    for t in range(ticks):
        for ev in await system.step(_time(t)):
            types.append(ev.event_type)
    return types


async def test_move_reaches_path_end() -> None:
    hot = InMemoryHotState()
    hot.put_unit("u1", {"h3": _START})
    store = FakeOrderStore([_cmd()])
    system = MovementSystem("s", hot, store, FakePlanner(_PATH), speed_hexes=1)

    events = await _run(system, len(_PATH) + 2)
    assert "MOVE_STARTED" in events
    assert "MOVE_COMPLETED" in events
    # N ticks 後位置 = 路徑終點
    assert hot.get_unit("u1")["h3"] == _PATH[-1]
    lat, _lng = h3.cell_to_latlng(_PATH[-1])
    assert hot.get_unit("u1")["lat"] == pytest.approx(lat)
    assert store.completed["o1"] >= 0  # order 標記完成


async def test_terrain_interruption_stops_at_break() -> None:
    hot = InMemoryHotState()
    hot.put_unit("u1", {"h3": _START})
    store = FakeOrderStore([_cmd()])
    blocked = _PATH[2]
    system = MovementSystem(
        "s", hot, store, FakePlanner(_PATH), speed_hexes=1, passable=lambda h: h != blocked
    )

    events = await _run(system, len(_PATH) + 2)
    assert "MOVE_INTERRUPTED" in events
    assert "MOVE_COMPLETED" not in events
    # 停在斷點前一格（path[1]），未進入被阻擋的 path[2]
    assert hot.get_unit("u1")["h3"] == _PATH[1]
    assert "o1" in store.completed  # 中斷也結束 order（事件入帳）


async def test_fuel_exhaustion_halts() -> None:
    hot = InMemoryHotState()
    hot.put_unit("u1", {"h3": _START, "fuel": 2.0})
    store = FakeOrderStore([_cmd()])
    system = MovementSystem("s", hot, store, FakePlanner(_PATH), speed_hexes=1, fuel_per_hex=1.0)

    events = await _run(system, len(_PATH) + 2)
    assert "MOVE_HALTED_FUEL" in events
    assert hot.get_unit("u1")["h3"] == _PATH[2]  # 走 2 格後油盡
    assert hot.get_unit("u1")["fuel"] == pytest.approx(0.0)


async def test_speed_advances_multiple_hexes_per_tick() -> None:
    hot = InMemoryHotState()
    hot.put_unit("u1", {"h3": _START})
    store = FakeOrderStore([_cmd()])
    system = MovementSystem("s", hot, store, FakePlanner(_PATH), speed_hexes=3)

    await system.step(_time(0))  # 同 tick admit + 前進 3 格
    assert hot.get_unit("u1")["h3"] == _PATH[3]
    await system.step(_time(1))  # 再前進 3 格
    assert hot.get_unit("u1")["h3"] == _PATH[6]


async def test_trivial_path_completes_immediately() -> None:
    hot = InMemoryHotState()
    hot.put_unit("u1", {"h3": _START})
    store = FakeOrderStore([_cmd()])
    system = MovementSystem("s", hot, store, FakePlanner([_START]))  # 已在終點

    events = await _run(system, 1)
    assert events == ["MOVE_COMPLETED"]
    assert "o1" in store.completed


async def test_no_path_unreachable_completes() -> None:
    hot = InMemoryHotState()
    hot.put_unit("u1", {"h3": _START})
    store = FakeOrderStore([_cmd()])
    system = MovementSystem("s", hot, store, FakePlanner([]))  # 不可達 → 空路徑

    events = await _run(system, 1)
    assert events == ["MOVE_COMPLETED"]


async def test_multiple_units_deterministic_order() -> None:
    hot = InMemoryHotState()
    for u in ("u1", "u2"):
        hot.put_unit(u, {"h3": _START})
    store = FakeOrderStore([_cmd("u2", "o2"), _cmd("u1", "o1")])
    system = MovementSystem("s", hot, store, FakePlanner(_PATH))

    ev0 = [e async for e in _events(system, _time(0))]
    # admit 依 store 順序；advance 依 unit_id 排序 → 事件穩定
    assert [e.initiator_id for e in ev0] == ["u2", "u1"]  # MOVE_STARTED（admit 序）


async def _events(system: MovementSystem, now: SimTime):  # type: ignore[no-untyped-def]
    for ev in await system.step(now):
        yield ev
