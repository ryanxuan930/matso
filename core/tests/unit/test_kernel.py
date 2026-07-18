"""Kernel tick loop 單元測試（fake 子系統 + 可控牆鐘）。"""

from __future__ import annotations

from collections.abc import Iterator, Sequence

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.engine.clock import SimClock, SimTime
from app.engine.kernel import Kernel
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
from app.models import Base, TacticalEventLog, WargameSession
from app.state.ledger import LedgerEvent, LedgerWriter, verify_chain

_NS_PER_MS = 1_000_000


# ---------------- 測試替身 ----------------


class Recorder:
    def __init__(self) -> None:
        self.order: list[str] = []


class FakeClock:
    """每次呼叫前進 step_ns → 每 tick（兩次呼叫）量得 elapsed = step_ns。"""

    def __init__(self, step_ns: int = 0) -> None:
        self._t = 0
        self._step = step_ns

    def now_ns(self) -> int:
        v = self._t
        self._t += self._step
        return v


class FakeOrderSource:
    def __init__(self, rec: Recorder, orders: Sequence[object] = ()) -> None:
        self._rec = rec
        self._orders = list(orders)

    async def drain(self) -> list[object]:
        self._rec.order.append("drain")
        return list(self._orders)


class FakeAdjudicator:
    def __init__(self, rec: Recorder, events: Sequence[LedgerEvent] = ()) -> None:
        self._rec = rec
        self._events = list(events)

    def resolve(self, order: object, now: SimTime) -> list[LedgerEvent]:
        self._rec.order.append(f"resolve:{order}")
        return list(self._events)


class FakeStage:
    """通用假子系統：記錄 label、回傳（依 now.tick 蓋章的）事件。"""

    def __init__(self, rec: Recorder, label: str, event_type: str | None = None) -> None:
        self._rec = rec
        self._label = label
        self._event_type = event_type

    def _emit(self, now: SimTime) -> list[LedgerEvent]:
        self._rec.order.append(self._label)
        if self._event_type is None:
            return []
        return [LedgerEvent(event_type=self._event_type, tick=now.tick)]

    async def step(self, now: SimTime) -> list[LedgerEvent]:
        return self._emit(now)

    async def sweep(self, now: SimTime) -> list[LedgerEvent]:
        return self._emit(now)

    async def evaluate(self, now: SimTime) -> list[LedgerEvent]:
        return self._emit(now)

    async def consume(self, now: SimTime) -> list[LedgerEvent]:
        return self._emit(now)

    def check(self, now: SimTime) -> list[LedgerEvent]:
        return self._emit(now)


class FakeBroadcaster:
    def __init__(self, rec: Recorder) -> None:
        self._rec = rec

    async def publish(self, tick: int) -> None:
        self._rec.order.append("broadcast")


class FakeEventSink:
    def __init__(self) -> None:
        self.appended: list[tuple[str, list[LedgerEvent]]] = []

    def append(self, session_id: str, events: Sequence[LedgerEvent]) -> list[str]:
        self.appended.append((session_id, list(events)))
        return [f"h{i}" for i in range(len(events))]


def make_kernel(
    *,
    rec: Recorder,
    sink: FakeEventSink,
    clock: SimClock | None = None,
    wall_clock: object | None = None,
    orders: Sequence[object] = (),
    adjudicator_events: Sequence[LedgerEvent] = (),
    emit_stage_events: bool = False,
    tick_budget_ms: int = 200,
) -> Kernel:
    stage_type = "STAGE" if emit_stage_events else None
    return Kernel(
        session_id="sess-1",
        clock=clock or SimClock(),
        order_source=FakeOrderSource(rec, orders),
        adjudicator=FakeAdjudicator(rec, adjudicator_events),
        movement=FakeStage(rec, "movement", stage_type),
        sensors=FakeStage(rec, "sensors", stage_type),
        comms=FakeStage(rec, "comms", stage_type),
        logistics=FakeStage(rec, "logistics", stage_type),
        trigger_checker=FakeStage(rec, "triggers", stage_type),
        broadcaster=FakeBroadcaster(rec),
        event_sink=sink,
        wall_clock=wall_clock or NullMonotonicClock(),
        tick_budget_ms=tick_budget_ms,
    )


# ---------------- 呼叫順序 ----------------


async def test_subsystems_called_in_spec_order() -> None:
    rec = Recorder()
    kernel = make_kernel(rec=rec, sink=FakeEventSink(), orders=["o1", "o2"])
    await kernel.run_tick()
    assert rec.order == [
        "drain",
        "resolve:o1",
        "resolve:o2",
        "movement",
        "sensors",
        "comms",
        "logistics",
        "triggers",
        "broadcast",
    ]


async def test_broadcast_after_events_appended() -> None:
    rec = Recorder()
    sink = FakeEventSink()
    kernel = make_kernel(rec=rec, sink=sink)
    await kernel.run_tick()
    # 廣播必在收集/寫入之後（drain..triggers 都早於 broadcast）
    assert rec.order[-1] == "broadcast"


# ---------------- 事件收集 ----------------


async def test_events_collected_from_all_stages_in_order() -> None:
    rec = Recorder()
    sink = FakeEventSink()
    kernel = make_kernel(
        rec=rec,
        sink=sink,
        orders=["o1"],
        adjudicator_events=[LedgerEvent(event_type="ORDER_RESOLVED", tick=0)],
        emit_stage_events=True,
    )
    await kernel.run_tick()
    _, events = sink.appended[0]
    types = [e.event_type for e in events]
    # 裁決事件在前，接著 movement/sensors/comms/logistics/triggers
    assert types == ["ORDER_RESOLVED", "STAGE", "STAGE", "STAGE", "STAGE", "STAGE"]


async def test_empty_tick_appends_nothing() -> None:
    sink = FakeEventSink()
    kernel = Kernel(
        session_id="s",
        clock=SimClock(),
        order_source=NoOpOrderSource(),
        adjudicator=NoOpAdjudicator(),
        movement=NoOpMovementSystem(),
        sensors=NoOpSensorSystem(),
        comms=NoOpCommsSystem(),
        logistics=NoOpLogisticsSystem(),
        trigger_checker=NoOpTriggerChecker(),
        broadcaster=NoOpBroadcaster(),
        event_sink=sink,
        wall_clock=NullMonotonicClock(),
    )
    report = await kernel.run_tick()
    assert sink.appended == [("s", [])]
    assert report.events_written == 0


# ---------------- 時鐘推進 ----------------


async def test_clock_advances_one_tick() -> None:
    clock = SimClock(tick_rate_ms=1000)
    kernel = make_kernel(rec=Recorder(), sink=FakeEventSink(), clock=clock)
    report = await kernel.run_tick()
    assert report.tick == 0  # 處理的是 tick 0
    assert clock.now() == SimTime(tick=1, sim_time_ms=1000)  # 之後推進到 1


async def test_run_multiple_ticks() -> None:
    clock = SimClock()
    kernel = make_kernel(rec=Recorder(), sink=FakeEventSink(), clock=clock)
    reports = await kernel.run(3)
    assert [r.tick for r in reports] == [0, 1, 2]
    assert clock.tick == 3


async def test_run_zero_ticks_is_noop() -> None:
    clock = SimClock()
    kernel = make_kernel(rec=Recorder(), sink=FakeEventSink(), clock=clock)
    assert await kernel.run(0) == []
    assert clock.tick == 0


# ---------------- TICK_OVERRUN ----------------


async def test_no_overrun_under_budget() -> None:
    sink = FakeEventSink()
    kernel = make_kernel(
        rec=Recorder(), sink=sink, wall_clock=FakeClock(step_ns=0), tick_budget_ms=200
    )
    report = await kernel.run_tick()
    assert not report.overran
    assert kernel.overrun_count == 0
    _, events = sink.appended[0]
    assert all(e.event_type != "TICK_OVERRUN" for e in events)


async def test_overrun_emits_event_and_counts() -> None:
    sink = FakeEventSink()
    # elapsed = 250ms > budget 200ms
    kernel = make_kernel(
        rec=Recorder(),
        sink=sink,
        wall_clock=FakeClock(step_ns=250 * _NS_PER_MS),
        tick_budget_ms=200,
    )
    report = await kernel.run_tick()
    assert report.overran
    assert kernel.overrun_count == 1
    _, events = sink.appended[0]
    overruns = [e for e in events if e.event_type == "TICK_OVERRUN"]
    assert len(overruns) == 1
    assert overruns[0].ai_decision["budget_ms"] == 200


async def test_overrun_does_not_drop_other_events() -> None:
    sink = FakeEventSink()
    kernel = make_kernel(
        rec=Recorder(),
        sink=sink,
        wall_clock=FakeClock(step_ns=300 * _NS_PER_MS),
        emit_stage_events=True,
        tick_budget_ms=200,
    )
    await kernel.run_tick()
    _, events = sink.appended[0]
    types = [e.event_type for e in events]
    assert types.count("STAGE") == 5  # 五個 stage 事件仍在
    assert types[-1] == "TICK_OVERRUN"  # overrun 附加在最後


async def test_tick_budget_config_injection() -> None:
    sink = FakeEventSink()
    # elapsed 100ms：預算 200 不 overrun、預算 50 會 overrun
    kernel_ok = make_kernel(
        rec=Recorder(),
        sink=FakeEventSink(),
        wall_clock=FakeClock(100 * _NS_PER_MS),
        tick_budget_ms=200,
    )
    kernel_tight = make_kernel(
        rec=Recorder(), sink=sink, wall_clock=FakeClock(100 * _NS_PER_MS), tick_budget_ms=50
    )
    assert not (await kernel_ok.run_tick()).overran
    assert (await kernel_tight.run_tick()).overran


def test_invalid_tick_budget_rejected() -> None:
    with pytest.raises(ValueError, match="tick_budget_ms"):
        make_kernel(rec=Recorder(), sink=FakeEventSink(), tick_budget_ms=0)


async def test_run_negative_rejected() -> None:
    kernel = make_kernel(rec=Recorder(), sink=FakeEventSink())
    with pytest.raises(ValueError, match="n_ticks"):
        await kernel.run(-1)


# ---------------- 端到端：真 LedgerWriter (SQLite) ----------------


@pytest.fixture
def session_factory() -> Iterator[sessionmaker[Session]]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine, expire_on_commit=False, future=True)
    engine.dispose()


async def test_kernel_writes_verifiable_chain_to_real_ledger(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as db:
        ws = WargameSession(name="kernel-e2e", master_seed=1, current_weather={})
        db.add(ws)
        db.commit()
        sid = ws.id

    rec = Recorder()
    kernel = Kernel(
        session_id=sid,
        clock=SimClock(),
        order_source=NoOpOrderSource(),
        adjudicator=NoOpAdjudicator(),
        movement=FakeStage(rec, "movement", "MOVEMENT_STEP"),
        sensors=FakeStage(rec, "sensors", "DETECTION"),
        comms=NoOpCommsSystem(),
        logistics=NoOpLogisticsSystem(),
        trigger_checker=NoOpTriggerChecker(),
        broadcaster=NoOpBroadcaster(),
        event_sink=LedgerWriter(session_factory),
        wall_clock=NullMonotonicClock(),
    )
    await kernel.run(5)

    with session_factory() as db:
        rows = list(
            db.execute(
                select(TacticalEventLog)
                .where(TacticalEventLog.session_id == sid)
                .order_by(TacticalEventLog.seq.asc())
            )
            .scalars()
            .all()
        )
    assert len(rows) == 10  # 每 tick 2 事件 × 5 ticks
    assert [r.seq for r in rows] == list(range(10))
    assert verify_chain(rows).ok
