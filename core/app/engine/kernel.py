"""Simulation Kernel — tick loop（SPEC_FULL §2.3、§3.3）。

每個 tick 的固定順序（決定性，確保 hash chain 可重現）：
  drain orders → 逐一裁決 → movement → sensors → comms → logistics → triggers
  → (若超預算) TICK_OVERRUN → 批次寫 Ledger → 廣播 → 推進 SimClock。

紅線遵循：
- 模擬時間只來自 SimClock；tick 效能量測用注入的 MonotonicClock（真實時間，不參與模擬）。
- 事件不靜默丟棄：超預算時仍完整處理本 tick，並額外記 TICK_OVERRUN（SPEC_FULL §3.3）。
- Kernel 是 Ledger 與（O1.4 起）Redis 熱狀態的唯一寫入者。
"""

from __future__ import annotations

from dataclasses import dataclass

from app.engine.clock import SimClock
from app.engine.subsystems import (
    Adjudicator,
    Broadcaster,
    CommsSystem,
    EventSink,
    LogisticsSystem,
    MonotonicClock,
    MovementSystem,
    OrderSource,
    SensorSystem,
    TriggerChecker,
)
from app.state.ledger import LedgerEvent

_NS_PER_MS = 1_000_000


@dataclass(frozen=True, slots=True)
class TickReport:
    """單一 tick 的執行結果，供 runtime 迴圈決定節奏（降頻）與觀測。"""

    tick: int
    events_written: int
    duration_ns: int
    overran: bool


class Kernel:
    """模擬核心。依賴以 Protocol 注入，O1.3 可全數接 no-op stub 空跑。

    tick_budget_ms 由建構參數注入（SPEC_FULL §18 預設 200ms / 500 單位）。
    """

    def __init__(
        self,
        *,
        session_id: str,
        clock: SimClock,
        order_source: OrderSource,
        adjudicator: Adjudicator,
        movement: MovementSystem,
        sensors: SensorSystem,
        comms: CommsSystem,
        logistics: LogisticsSystem,
        trigger_checker: TriggerChecker,
        broadcaster: Broadcaster,
        event_sink: EventSink,
        wall_clock: MonotonicClock,
        tick_budget_ms: int = 200,
    ) -> None:
        if tick_budget_ms < 1:
            raise ValueError(f"tick_budget_ms 必須 >= 1，收到 {tick_budget_ms}")
        self._session_id = session_id
        self._clock = clock
        self._order_source = order_source
        self._adjudicator = adjudicator
        self._movement = movement
        self._sensors = sensors
        self._comms = comms
        self._logistics = logistics
        self._trigger_checker = trigger_checker
        self._broadcaster = broadcaster
        self._event_sink = event_sink
        self._wall_clock = wall_clock
        self._tick_budget_ms = tick_budget_ms
        self._overrun_count = 0

    @property
    def overrun_count(self) -> int:
        return self._overrun_count

    @property
    def tick_budget_ms(self) -> int:
        return self._tick_budget_ms

    async def run_tick(self) -> TickReport:
        """執行「當前 tick」的完整流程，最後推進時鐘到下一 tick。"""
        now = self._clock.now()
        start_ns = self._wall_clock.now_ns()

        events: list[LedgerEvent] = []
        for order in await self._order_source.drain():
            events.extend(self._adjudicator.resolve(order, now))
        events.extend(await self._movement.step(now))
        events.extend(await self._sensors.sweep(now))
        events.extend(await self._comms.evaluate(now))
        events.extend(await self._logistics.consume(now))
        events.extend(self._trigger_checker.check(now))

        # 效能量測涵蓋整個計算階段；Ledger 寫入與廣播不計入（避免把 I/O 誤判為運算超時）。
        duration_ns = self._wall_clock.now_ns() - start_ns
        overran = duration_ns > self._tick_budget_ms * _NS_PER_MS
        if overran:
            self._overrun_count += 1
            events.append(self._build_overrun_event(now.tick, duration_ns))

        written = self._event_sink.append(self._session_id, events)
        await self._broadcaster.publish(now.tick)
        self._clock.advance()

        return TickReport(
            tick=now.tick,
            events_written=len(written),
            duration_ns=duration_ns,
            overran=overran,
        )

    async def run(self, n_ticks: int) -> list[TickReport]:
        """連續執行 n 個 tick（不做牆鐘節奏控制；節奏/降頻由 runtime 迴圈負責）。"""
        if n_ticks < 0:
            raise ValueError(f"n_ticks 必須 >= 0，收到 {n_ticks}")
        return [await self.run_tick() for _ in range(n_ticks)]

    def _build_overrun_event(self, tick: int, duration_ns: int) -> LedgerEvent:
        # 診斷事件；duration 為真實牆鐘（非決定性），僅在 overrun 時出現，不影響模擬 state。
        return LedgerEvent(
            event_type="TICK_OVERRUN",
            tick=tick,
            ai_decision={
                "duration_ms": duration_ns / _NS_PER_MS,
                "budget_ms": self._tick_budget_ms,
            },
        )
