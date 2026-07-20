"""Golden replay 想定註冊表。

現階段（O1.6）子系統多為 no-op；為讓 golden 對邏輯敏感，加入一個確定性示範子系統
RngWalkMovement（整數格點亂步，用 DeterministicRNG）。真實子系統（O3.x）就緒後，
可在此加入「讀 Ledger order 序列」的想定——harness 的 build_kernel 工廠可注入 scripted OrderSource。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from harness import ReplayScenario

from app.engine.clock import SimClock, SimTime
from app.engine.kernel import Kernel
from app.engine.rng import DeterministicRNG
from app.engine.subsystems import (
    NoOpAdjudicator,
    NoOpBroadcaster,
    NoOpCommsSystem,
    NoOpEventSink,
    NoOpLogisticsSystem,
    NoOpMovementSystem,
    NoOpOrderSource,
    NoOpSensorSystem,
    NoOpTriggerChecker,
    NullMonotonicClock,
)
from app.state.hot_state import InMemoryHotState
from app.state.ledger import LedgerEvent

MASTER_SEED = 20260718
N_UNITS = 5
# 整數格點四方向；PCG64 整數串跨平台穩定 → hash 不受浮點格式化影響
_MOVES: tuple[tuple[int, int], ...] = ((1, 0), (-1, 0), (0, 1), (0, -1))


class RngWalkMovement:
    """確定性示範：每 tick 用 DeterministicRNG 讓每個單位在整數格點上走一步。

    僅供 golden replay 驗證（相同 seed→相同最終 stateHash）；真實 movement 為 O3.4。
    """

    def __init__(
        self,
        hot_state: InMemoryHotState,
        unit_ids: Sequence[str],
        master_seed: int,
        stream_id: str = "movement",
    ) -> None:
        self._hot = hot_state
        self._ids = list(unit_ids)
        self._rng = DeterministicRNG(master_seed, stream_id)

    async def step(self, now: SimTime) -> list[LedgerEvent]:
        for uid in self._ids:
            cur: dict[str, Any] = self._hot.get_unit(uid) or {"x": 0, "y": 0}
            dx, dy = self._rng.choice(_MOVES)
            self._hot.update_unit(uid, {"x": cur["x"] + dx, "y": cur["y"] + dy})
        return []


def _base_kernel(hot: InMemoryHotState, movement: Any | None = None) -> Kernel:
    return Kernel(
        session_id="replay",
        clock=SimClock(),
        order_source=NoOpOrderSource(),
        adjudicator=NoOpAdjudicator(),
        movement=movement or NoOpMovementSystem(),
        sensors=NoOpSensorSystem(),
        comms=NoOpCommsSystem(),
        logistics=NoOpLogisticsSystem(),
        trigger_checker=NoOpTriggerChecker(),
        broadcaster=NoOpBroadcaster(),
        event_sink=NoOpEventSink(),
        hot_state=hot,
        wall_clock=NullMonotonicClock(),
    )


def _build_empty() -> Kernel:
    return _base_kernel(InMemoryHotState())


def build_rng_walk_kernel(stream_id: str = "movement") -> Kernel:
    hot = InMemoryHotState()
    ids = [f"u{i}" for i in range(N_UNITS)]
    for uid in ids:
        hot.put_unit(uid, {"x": 0, "y": 0})
    return _base_kernel(hot, movement=RngWalkMovement(hot, ids, MASTER_SEED, stream_id))


# --------------------------------------------------------------------------
# Order 指令序列重播（O3.1 / O1.7 R10）：SPEC §3.2「replay 讀 Ledger 指令序列重新執行 →
# 最終 stateHash 一致」的 Phase-1 接入。以固定 ScriptedOrder 序列（代表 Ledger 中記錄的 order
# 序列）驅動 Kernel：LedgerOrderSource 依 tick drain、OrderApplyingAdjudicator 確定性套用。
# 完整「讀 DB Ledger 重播」屬 O8.1；此處證明同一指令序列 → 同一最終狀態的決定性保證。
# --------------------------------------------------------------------------

_ORDER_UNITS = [f"u{i}" for i in range(N_UNITS)]
_ORDER_TICKS = 60


@dataclass(frozen=True, slots=True)
class ScriptedOrder:
    tick: int
    unit_id: str
    dx: int
    dy: int


class LedgerOrderSource:
    """依當前 tick drain 固定指令序列（順序確定）。讀 SimClock 得知 tick。"""

    def __init__(self, clock: SimClock, orders: Sequence[ScriptedOrder]) -> None:
        self._clock = clock
        by_tick: dict[int, list[ScriptedOrder]] = {}
        for order in orders:
            by_tick.setdefault(order.tick, []).append(order)
        self._by_tick = by_tick

    async def drain(self) -> list[ScriptedOrder]:
        return list(self._by_tick.get(self._clock.now().tick, []))


class OrderApplyingAdjudicator:
    """把 MOVE 指令確定性套到 hot_state（位移）並產 ORDER_EXECUTED 事件。純同步。"""

    def __init__(self, hot: InMemoryHotState) -> None:
        self._hot = hot

    def resolve(self, order: ScriptedOrder, now: SimTime) -> list[LedgerEvent]:
        cur: dict[str, Any] = self._hot.get_unit(order.unit_id) or {"x": 0, "y": 0}
        self._hot.update_unit(order.unit_id, {"x": cur["x"] + order.dx, "y": cur["y"] + order.dy})
        return [
            LedgerEvent(
                event_type="ORDER_EXECUTED",
                tick=now.tick,
                initiator_id=order.unit_id,
                ai_decision={"dx": order.dx, "dy": order.dy},
            )
        ]


def _scripted_orders() -> list[ScriptedOrder]:
    """固定（無 RNG、無牆鐘）指令序列：每 tick 每單位一條確定性位移。"""
    orders: list[ScriptedOrder] = []
    for tick in range(_ORDER_TICKS):
        for i, uid in enumerate(_ORDER_UNITS):
            dx = 1 if (tick + i) % 2 == 0 else 0
            dy = 1 if (tick + i) % 3 == 0 else -1
            orders.append(ScriptedOrder(tick=tick, unit_id=uid, dx=dx, dy=dy))
    return orders


def build_order_replay_kernel() -> Kernel:
    hot = InMemoryHotState()
    for uid in _ORDER_UNITS:
        hot.put_unit(uid, {"x": 0, "y": 0})
    clock = SimClock()
    return Kernel(
        session_id="replay",
        clock=clock,
        order_source=LedgerOrderSource(clock, _scripted_orders()),
        adjudicator=OrderApplyingAdjudicator(hot),
        movement=NoOpMovementSystem(),
        sensors=NoOpSensorSystem(),
        comms=NoOpCommsSystem(),
        logistics=NoOpLogisticsSystem(),
        trigger_checker=NoOpTriggerChecker(),
        broadcaster=NoOpBroadcaster(),
        event_sink=NoOpEventSink(),
        hot_state=hot,
        wall_clock=NullMonotonicClock(),
    )


SCENARIOS: dict[str, ReplayScenario] = {
    "empty_100": ReplayScenario("empty_100", 100, _build_empty),
    "rng_walk_100": ReplayScenario("rng_walk_100", 100, build_rng_walk_kernel),
    "order_replay_60": ReplayScenario("order_replay_60", _ORDER_TICKS, build_order_replay_kernel),
}
