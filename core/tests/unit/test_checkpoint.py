"""Checkpoint 序列化 / hash / recover / rollback 單元測試（SQLite in-memory，不需 compose）。

session_factory 由 core/tests/conftest.py 提供；no-op Kernel 由 build_noop_kernel 提供。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

import app.state.checkpoint as checkpoint_mod
from app.engine.kernel import Kernel
from app.errors import CheckpointTooLargeError, RollbackTargetNotFoundError
from app.models import SimCheckpoint, TacticalEventLog, WargameSession
from app.state.checkpoint import (
    CheckpointManager,
    compute_state_hash,
    deserialize_state,
    recover,
    rollback,
    serialize_state,
)
from app.state.hot_state import InMemoryHotState, UnitState
from app.state.ledger import LedgerEvent, LedgerWriter

STATE_A: dict[str, UnitState] = {
    "u1": {"lat": 25.0, "lng": 121.5, "health": 100},
    "u2": {"lat": 24.0, "lng": 120.0, "health": 80},
}


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
    mgr.checkpoint(session_id, tick=300, state=STATE_A, ledger_seq=42)
    record = mgr.load_latest(session_id)
    assert record is not None
    assert record.tick == 300
    assert record.ledger_seq == 42
    assert record.state == STATE_A
    assert record.state_hash == compute_state_hash(STATE_A)


def test_load_latest_none_when_empty(
    session_factory: sessionmaker[Session], session_id: str
) -> None:
    assert CheckpointManager(session_factory).load_latest(session_id) is None


def test_load_latest_orders_by_ledger_seq_not_tick(
    session_factory: sessionmaker[Session], session_id: str
) -> None:
    # rollback 後新世代 tick 較小但 seq 較大——「最近」必須依 seq 判定（O1.7/R3）
    mgr = CheckpointManager(session_factory)
    mgr.checkpoint(session_id, tick=300, state=STATE_A, ledger_seq=10)
    mgr.checkpoint(session_id, tick=50, state={"u1": {"health": 5}}, ledger_seq=99)
    record = mgr.load_latest(session_id)
    assert record is not None
    assert record.tick == 50
    assert record.ledger_seq == 99


def test_load_at_tick(session_factory: sessionmaker[Session], session_id: str) -> None:
    mgr = CheckpointManager(session_factory)
    mgr.checkpoint(session_id, tick=100, state=STATE_A, ledger_seq=1)
    mgr.checkpoint(session_id, tick=300, state={"u1": {"health": 10}}, ledger_seq=2)
    record = mgr.load_at_tick(session_id, 100)
    assert record is not None
    assert record.tick == 100
    assert mgr.load_at_tick(session_id, 999) is None


def test_checkpoint_size_guard(
    session_factory: sessionmaker[Session], session_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(checkpoint_mod, "MAX_CHECKPOINT_BYTES", 1)
    with pytest.raises(CheckpointTooLargeError, match="超過上限"):
        CheckpointManager(session_factory).checkpoint(session_id, 300, STATE_A, ledger_seq=0)


def test_checkpoint_same_tick_overwrites(
    session_factory: sessionmaker[Session], session_id: str
) -> None:
    mgr = CheckpointManager(session_factory)
    mgr.checkpoint(session_id, 300, STATE_A, ledger_seq=1)
    mgr.checkpoint(session_id, 300, {"u1": {"health": 1}}, ledger_seq=2)
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
    CheckpointManager(session_factory).checkpoint(session_id, 300, STATE_A, ledger_seq=7)
    hot = InMemoryHotState()  # 模擬崩潰後的空熱狀態
    result = recover(session_factory, session_id, hot)
    assert result.restored
    assert result.restored_tick == 300
    assert result.restored_ledger_seq == 7
    assert result.events_after_checkpoint == 0
    assert hot.get_all() == STATE_A


def test_recover_no_checkpoint(session_factory: sessionmaker[Session], session_id: str) -> None:
    result = recover(session_factory, session_id, InMemoryHotState())
    assert not result.restored
    assert result.restored_tick is None


def test_recover_counts_events_by_seq(
    session_factory: sessionmaker[Session], session_id: str
) -> None:
    writer = LedgerWriter(session_factory)
    writer.append(session_id, [LedgerEvent(event_type="MOVEMENT_STEP", tick=t) for t in range(3)])
    # checkpoint 錨定在 seq=2；之後兩筆事件「tick 很小」（模擬 rollback 後的新世代）
    CheckpointManager(session_factory).checkpoint(session_id, tick=2, state=STATE_A, ledger_seq=2)
    writer.append(session_id, [LedgerEvent(event_type="MOVEMENT_STEP", tick=0)])
    writer.append(session_id, [LedgerEvent(event_type="MOVEMENT_STEP", tick=1)])
    result = recover(session_factory, session_id, InMemoryHotState())
    # tick 比 checkpoint.tick 小，但 seq 在後 → 必須被算進 events_after（O1.7/R3）
    assert result.events_after_checkpoint == 2


def test_recover_invokes_transport_reset(
    session_factory: sessionmaker[Session], session_id: str
) -> None:
    CheckpointManager(session_factory).checkpoint(session_id, 1, STATE_A, ledger_seq=0)
    calls: list[bool] = []
    recover(
        session_factory, session_id, InMemoryHotState(), transport_reset=lambda: calls.append(True)
    )
    assert calls == [True]


def test_recovered_state_hash_matches_precrash() -> None:
    pre = InMemoryHotState()
    pre.put_unit("u1", {"lat": 25.0, "health": 100})
    pre.update_unit("u1", {"health": 60})
    snapshot = pre.get_all()
    pre_hash = compute_state_hash(snapshot)

    post = InMemoryHotState()
    post.restore(deserialize_state(serialize_state(snapshot)))
    assert compute_state_hash(post.get_all()) == pre_hash


# ---------------- rollback（O1.7/R1/R2 回歸） ----------------


def test_rollback_discards_later_checkpoints_so_recover_honors_rollback(
    session_factory: sessionmaker[Session], session_id: str
) -> None:
    """R2 回歸：rollback 後 crash-recover 不得復活被回滾的狀態。"""
    mgr = CheckpointManager(session_factory)
    hot = InMemoryHotState()
    hot.put_unit("u1", {"health": 100})
    mgr.checkpoint(session_id, tick=0, state=hot.get_all(), ledger_seq=0)
    hot.update_unit("u1", {"health": 20})
    mgr.checkpoint(session_id, tick=5, state=hot.get_all(), ledger_seq=10)

    result = rollback(
        session_factory, LedgerWriter(session_factory), session_id, hot, target_tick=0
    )
    assert result.checkpoints_discarded == 1

    crashed = InMemoryHotState()
    recovered = recover(session_factory, session_id, crashed)
    assert recovered.restored_tick == 0
    assert crashed.get_all() == {"u1": {"health": 100}}  # 不是被回滾掉的 h=20


def test_rollback_writes_event_with_detail(
    session_factory: sessionmaker[Session], session_id: str
) -> None:
    mgr = CheckpointManager(session_factory)
    hot = InMemoryHotState()
    hot.put_unit("u1", {"health": 100})
    mgr.checkpoint(session_id, tick=0, state=hot.get_all(), ledger_seq=-1)
    rollback(session_factory, LedgerWriter(session_factory), session_id, hot, target_tick=0)
    with session_factory() as db:
        events = list(
            db.execute(
                select(TacticalEventLog).where(TacticalEventLog.session_id == session_id)
            ).scalars()
        )
    assert len(events) == 1
    assert events[0].event_type == "ROLLBACK"
    assert events[0].detail is not None
    assert events[0].detail["rolled_back_to"] == 0
    assert events[0].ai_decision == {}  # 診斷不再塞 aiDecision（O1.7/R8）


def test_rollback_unknown_tick_raises_domain_error(
    session_factory: sessionmaker[Session], session_id: str
) -> None:
    hot = InMemoryHotState()
    hot.put_unit("u1", {"health": 100})
    CheckpointManager(session_factory).checkpoint(
        session_id, tick=0, state=hot.get_all(), ledger_seq=0
    )
    with pytest.raises(RollbackTargetNotFoundError, match="無 tick=99"):
        rollback(session_factory, LedgerWriter(session_factory), session_id, hot, target_tick=99)


def test_kernel_writer_survives_foreign_rollback(
    session_factory: sessionmaker[Session], session_id: str
) -> None:
    """R1 回歸（review 實證重現的 bug）：另一 writer rollback 後，Kernel writer 續寫不撞 seq。"""
    kernel_writer = LedgerWriter(session_factory)
    kernel_writer.append(
        session_id, [LedgerEvent(event_type="MOVEMENT_STEP", tick=t) for t in range(3)]
    )  # seq 0..2
    hot = InMemoryHotState()
    hot.put_unit("u1", {"h": 100})
    CheckpointManager(session_factory).checkpoint(
        session_id, tick=1, state=hot.get_all(), ledger_seq=2
    )
    rollback(
        session_factory, LedgerWriter(session_factory), session_id, hot, target_tick=1
    )  # seq 3

    # 修復前：IntegrityError（重複 seq 3）。修復後：偵測衝突→重讀 tip→接 seq 4
    kernel_writer.append(session_id, [LedgerEvent(event_type="MOVEMENT_STEP", tick=2)])
    from app.state.ledger import verify_chain

    with session_factory() as db:
        rows = list(
            db.execute(
                select(TacticalEventLog)
                .where(TacticalEventLog.session_id == session_id)
                .order_by(TacticalEventLog.seq.asc())
            ).scalars()
        )
    assert [r.seq for r in rows] == [0, 1, 2, 3, 4]
    assert verify_chain(rows).ok


# ---------------- Kernel checkpoint cadence（fake checkpointer） ----------------


class CollectingCheckpointer:
    def __init__(self) -> None:
        self.saved: list[tuple[str, int, dict[str, UnitState], int]] = []

    def checkpoint(
        self, session_id: str, tick: int, state: Mapping[str, UnitState], ledger_seq: int
    ) -> None:
        self.saved.append((session_id, tick, {k: dict(v) for k, v in state.items()}, ledger_seq))


async def test_kernel_checkpoints_every_interval(build_noop_kernel: Callable[..., Kernel]) -> None:
    ckpt = CollectingCheckpointer()
    kernel = build_noop_kernel(checkpointer=ckpt, checkpoint_interval=3)
    await kernel.run(7)  # ticks 0..6
    assert [tick for _, tick, _, _ in ckpt.saved] == [0, 3, 6]


def test_kernel_rejects_invalid_checkpoint_interval(
    build_noop_kernel: Callable[..., Kernel],
) -> None:
    with pytest.raises(ValueError, match="checkpoint_interval"):
        build_noop_kernel(checkpoint_interval=0)
