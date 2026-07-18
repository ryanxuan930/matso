"""Checkpoint 序列化 / hash / recover 單元測試（SQLite in-memory，不需 compose）。"""

from __future__ import annotations

from collections.abc import Iterator, Mapping

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.state.checkpoint as checkpoint_mod
from app.models import Base, SimCheckpoint, WargameSession
from app.state.checkpoint import (
    CheckpointManager,
    CheckpointTooLargeError,
    compute_state_hash,
    deserialize_state,
    recover,
    serialize_state,
)
from app.state.hot_state import InMemoryHotState, UnitState

STATE_A: dict[str, UnitState] = {
    "u1": {"lat": 25.0, "lng": 121.5, "health": 100},
    "u2": {"lat": 24.0, "lng": 120.0, "health": 80},
}


@pytest.fixture
def session_factory() -> Iterator[sessionmaker[Session]]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine, expire_on_commit=False, future=True)
    engine.dispose()


@pytest.fixture
def session_id(session_factory: sessionmaker[Session]) -> str:
    with session_factory() as db:
        ws = WargameSession(name="ckpt", master_seed=1, current_weather={})
        db.add(ws)
        db.commit()
        return ws.id


# ---------------- serialize / hash ----------------


def test_serialize_deserialize_roundtrip() -> None:
    assert deserialize_state(serialize_state(STATE_A)) == STATE_A


def test_compression_reduces_size() -> None:
    big: dict[str, UnitState] = {f"u{i}": {"health": 100, "posture": "DEFEND"} for i in range(200)}
    assert len(serialize_state(big)) < len(str(big).encode())


def test_state_hash_deterministic() -> None:
    assert compute_state_hash(STATE_A) == compute_state_hash(dict(STATE_A))


def test_state_hash_key_order_independent() -> None:
    reordered: dict[str, UnitState] = {"u2": STATE_A["u2"], "u1": STATE_A["u1"]}
    assert compute_state_hash(STATE_A) == compute_state_hash(reordered)


def test_state_hash_changes_with_content() -> None:
    mutated = {**STATE_A, "u1": {**STATE_A["u1"], "health": 50}}
    assert compute_state_hash(STATE_A) != compute_state_hash(mutated)


# ---------------- CheckpointManager ----------------


def test_checkpoint_persists_and_loads(
    session_factory: sessionmaker[Session], session_id: str
) -> None:
    mgr = CheckpointManager(session_factory)
    mgr.checkpoint(session_id, tick=300, state=STATE_A)
    record = mgr.load_latest(session_id)
    assert record is not None
    assert record.tick == 300
    assert record.state == STATE_A
    assert record.state_hash == compute_state_hash(STATE_A)


def test_load_latest_none_when_empty(
    session_factory: sessionmaker[Session], session_id: str
) -> None:
    assert CheckpointManager(session_factory).load_latest(session_id) is None


def test_load_latest_returns_highest_tick(
    session_factory: sessionmaker[Session], session_id: str
) -> None:
    mgr = CheckpointManager(session_factory)
    mgr.checkpoint(session_id, 100, STATE_A)
    mgr.checkpoint(session_id, 300, {"u1": {"health": 10}})
    mgr.checkpoint(session_id, 200, STATE_A)
    record = mgr.load_latest(session_id)
    assert record is not None
    assert record.tick == 300


def test_load_latest_at_or_before(session_factory: sessionmaker[Session], session_id: str) -> None:
    mgr = CheckpointManager(session_factory)
    mgr.checkpoint(session_id, 100, STATE_A)
    mgr.checkpoint(session_id, 300, {"u1": {"health": 10}})
    record = mgr.load_latest(session_id, at_or_before_tick=200)
    assert record is not None
    assert record.tick == 100


def test_checkpoint_size_guard(
    session_factory: sessionmaker[Session], session_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 把上限壓到 1 byte，任何快照都超過 → 應拋 CheckpointTooLargeError（ADR 002 護欄）
    monkeypatch.setattr(checkpoint_mod, "MAX_CHECKPOINT_BYTES", 1)
    with pytest.raises(CheckpointTooLargeError, match="超過上限"):
        CheckpointManager(session_factory).checkpoint(session_id, 300, STATE_A)


def test_checkpoint_same_tick_overwrites(
    session_factory: sessionmaker[Session], session_id: str
) -> None:
    mgr = CheckpointManager(session_factory)
    mgr.checkpoint(session_id, 300, STATE_A)
    mgr.checkpoint(session_id, 300, {"u1": {"health": 1}})
    with session_factory() as db:
        rows = list(
            db.execute(
                select(SimCheckpoint).where(SimCheckpoint.session_id == session_id)
            ).scalars()
        )
    assert len(rows) == 1
    record = mgr.load_latest(session_id)
    assert record is not None
    assert record.state == {"u1": {"health": 1}}


# ---------------- recover ----------------


def test_recover_restores_hot_state(
    session_factory: sessionmaker[Session], session_id: str
) -> None:
    CheckpointManager(session_factory).checkpoint(session_id, 300, STATE_A)
    hot = InMemoryHotState()  # 模擬崩潰後的空熱狀態
    result = recover(session_factory, session_id, hot)
    assert result.restored
    assert result.restored_tick == 300
    assert result.events_after_checkpoint == 0
    assert hot.get_all() == STATE_A


def test_recover_no_checkpoint(session_factory: sessionmaker[Session], session_id: str) -> None:
    result = recover(session_factory, session_id, InMemoryHotState())
    assert not result.restored
    assert result.restored_tick is None


def test_recovered_state_hash_matches_precrash() -> None:
    # 崩潰前後狀態 hash 一致（不需 DB：直接比對 serialize/hash roundtrip）
    pre = InMemoryHotState()
    pre.put_unit("u1", {"lat": 25.0, "health": 100})
    pre.update_unit("u1", {"health": 60})
    snapshot = pre.get_all()
    pre_hash = compute_state_hash(snapshot)

    post = InMemoryHotState()
    post.restore(deserialize_state(serialize_state(snapshot)))
    assert compute_state_hash(post.get_all()) == pre_hash


# ---------------- Kernel checkpoint cadence（fake checkpointer） ----------------


class CollectingCheckpointer:
    def __init__(self) -> None:
        self.saved: list[tuple[str, int, dict[str, UnitState]]] = []

    def checkpoint(self, session_id: str, tick: int, state: Mapping[str, UnitState]) -> None:
        self.saved.append((session_id, tick, {k: dict(v) for k, v in state.items()}))


async def test_kernel_checkpoints_every_interval() -> None:
    from app.engine.clock import SimClock
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

    ckpt = CollectingCheckpointer()
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
        event_sink=_NullSink(),
        hot_state=InMemoryHotState(),
        wall_clock=NullMonotonicClock(),
        checkpointer=ckpt,
        checkpoint_interval=3,
    )
    await kernel.run(7)  # ticks 0..6
    assert [tick for _, tick, _ in ckpt.saved] == [0, 3, 6]


def test_kernel_rejects_invalid_checkpoint_interval() -> None:
    from app.engine.clock import SimClock
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

    with pytest.raises(ValueError, match="checkpoint_interval"):
        Kernel(
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
            event_sink=_NullSink(),
            hot_state=InMemoryHotState(),
            wall_clock=NullMonotonicClock(),
            checkpoint_interval=0,
        )


class _NullSink:
    def append(self, session_id: str, events: object) -> list[str]:
        return []
