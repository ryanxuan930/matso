"""Golden replay 想定註冊表。

現階段（O1.6）子系統多為 no-op；為讓 golden 對邏輯敏感，加入一個確定性示範子系統
RngWalkMovement（整數格點亂步，用 DeterministicRNG）。真實子系統（O3.x）就緒後，
可在此加入「讀 Ledger order 序列」的想定——harness 的 build_kernel 工廠可注入 scripted OrderSource。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from harness import ReplayScenario

from app.engine.clock import SimClock, SimTime
from app.engine.kernel import Kernel
from app.engine.rng import DeterministicRNG
from app.engine.subsystems import (
    NoOpAdjudicator,
    NoOpBroadcaster,
    NoOpCommsSystem,
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


class _NullSink:
    def append(self, session_id: str, events: Sequence[LedgerEvent]) -> list[str]:
        return []


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
        event_sink=_NullSink(),
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


SCENARIOS: dict[str, ReplayScenario] = {
    "empty_100": ReplayScenario("empty_100", 100, _build_empty),
    "rng_walk_100": ReplayScenario("rng_walk_100", 100, build_rng_walk_kernel),
}
