"""Checkpoint 崩潰復原整合測試（MariaDB:3307 + Redis:6379；fixture 見 conftest）。

驗收（TASKS.md O1.5）：跑 N ticks → 清 Redis 模擬崩潰 → recover → 狀態 hash 與崩潰前一致。
含 O1.7 review 修復的 DB 級回歸（R1 stale tip / R2 rollback×recover / R7 transport reset）。
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
import redis
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.errors import RollbackTargetNotFoundError
from app.models import SimCheckpoint, TacticalEventLog, WargameSession
from app.state.broadcaster import RedisBroadcaster
from app.state.checkpoint import CheckpointManager, compute_state_hash, recover, rollback
from app.state.hot_state import RedisHotState
from app.state.ledger import LedgerEvent, LedgerWriter, verify_chain

pytestmark = pytest.mark.integration


@pytest.fixture
def session_id(session_factory: sessionmaker[Session], redis_client: redis.Redis) -> Iterator[str]:
    with session_factory() as db:
        ws = WargameSession(
            name=f"itest-ckpt-{uuid.uuid4().hex[:8]}", master_seed=7, current_weather={}
        )
        db.add(ws)
        db.commit()
        sid = ws.id
    yield sid
    with session_factory() as db:
        db.execute(SimCheckpoint.__table__.delete().where(SimCheckpoint.session_id == sid))
        db.execute(TacticalEventLog.__table__.delete().where(TacticalEventLog.session_id == sid))
        db.execute(WargameSession.__table__.delete().where(WargameSession.id == sid))
        db.commit()
    for key in redis_client.scan_iter(match=f"session:{sid}:*"):
        redis_client.delete(key)


def _wipe_redis(redis_client: redis.Redis, sid: str) -> None:
    for key in redis_client.scan_iter(match=f"session:{sid}:*"):
        redis_client.delete(key)


def test_crash_recovery_state_hash_matches(
    session_factory: sessionmaker[Session], redis_client: redis.Redis, session_id: str
) -> None:
    hot = RedisHotState(redis_client, session_id)
    hot.put_unit("u1", {"lat": 25.0, "lng": 121.5, "health": 100})
    hot.put_unit("u2", {"lat": 24.0, "lng": 120.0, "health": 100})
    for tick in range(1, 6):
        hot.update_unit("u1", {"health": 100 - tick * 10})
        hot.update_unit("u2", {"lat": 24.0 + tick * 0.1})

    pre_state = hot.get_all()
    pre_hash = compute_state_hash(pre_state)
    CheckpointManager(session_factory).checkpoint(
        session_id, tick=5, state=pre_state, ledger_seq=-1
    )

    _wipe_redis(redis_client, session_id)  # 模擬崩潰
    crashed = RedisHotState(redis_client, session_id)
    assert crashed.get_all() == {}

    result = recover(session_factory, session_id, crashed)
    assert result.restored
    assert result.restored_tick == 5
    assert result.events_after_checkpoint == 0
    assert crashed.get_all() == pre_state
    assert compute_state_hash(crashed.get_all()) == pre_hash


def test_recover_counts_events_after_by_seq(
    session_factory: sessionmaker[Session], redis_client: redis.Redis, session_id: str
) -> None:
    writer = LedgerWriter(session_factory)
    writer.append(session_id, [LedgerEvent(event_type="MOVEMENT_STEP", tick=t) for t in range(3)])
    hot = RedisHotState(redis_client, session_id)
    hot.put_unit("u1", {"health": 100})
    CheckpointManager(session_factory).checkpoint(
        session_id, tick=2, state=hot.get_all(), ledger_seq=writer.tip_seq(session_id)
    )
    # checkpoint 後兩筆事件（tick 倒著走，模擬 rollback 後新世代）——必須仍被計入
    writer.append(session_id, [LedgerEvent(event_type="MOVEMENT_STEP", tick=0)])
    writer.append(session_id, [LedgerEvent(event_type="MOVEMENT_STEP", tick=1)])
    result = recover(session_factory, session_id, RedisHotState(redis_client, session_id))
    assert result.restored_tick == 2
    assert result.events_after_checkpoint == 2


def test_rollback_then_recover_does_not_resurrect(
    session_factory: sessionmaker[Session], redis_client: redis.Redis, session_id: str
) -> None:
    """R2 回歸（review 實證重現）：rollback 後 crash-recover 不得復活被回滾的狀態。"""
    mgr = CheckpointManager(session_factory)
    writer = LedgerWriter(session_factory)
    hot = RedisHotState(redis_client, session_id)

    hot.put_unit("u1", {"health": 100})
    mgr.checkpoint(session_id, tick=0, state=hot.get_all(), ledger_seq=writer.tip_seq(session_id))
    hot.update_unit("u1", {"health": 20})
    mgr.checkpoint(session_id, tick=5, state=hot.get_all(), ledger_seq=99)

    result = rollback(session_factory, writer, session_id, hot, target_tick=0)
    assert result.checkpoints_discarded == 1
    assert hot.get_all() == {"u1": {"health": 100}}

    _wipe_redis(redis_client, session_id)  # rollback 後崩潰
    crashed = RedisHotState(redis_client, session_id)
    recovered = recover(session_factory, session_id, crashed)
    assert recovered.restored_tick == 0
    assert crashed.get_all() == {"u1": {"health": 100}}  # 不是被回滾掉的 h=20

    # ROLLBACK 事件在帳本（append-only 證據保留）
    with session_factory() as db:
        events = list(
            db.execute(
                select(TacticalEventLog)
                .where(TacticalEventLog.session_id == session_id)
                .order_by(TacticalEventLog.seq.asc())
            ).scalars()
        )
    assert events[-1].event_type == "ROLLBACK"
    assert events[-1].detail is not None
    assert events[-1].detail["rolled_back_to"] == 0


def test_kernel_writer_continues_after_rollback_mariadb(
    session_factory: sessionmaker[Session], redis_client: redis.Redis, session_id: str
) -> None:
    """R1 回歸（review 實證重現）：rollback 經另一 writer 後，原 writer 續寫不撞 seq。"""
    kernel_writer = LedgerWriter(session_factory)
    kernel_writer.append(
        session_id, [LedgerEvent(event_type="MOVEMENT_STEP", tick=t) for t in range(3)]
    )
    hot = RedisHotState(redis_client, session_id)
    hot.put_unit("u1", {"health": 100})
    CheckpointManager(session_factory).checkpoint(
        session_id, tick=1, state=hot.get_all(), ledger_seq=kernel_writer.tip_seq(session_id)
    )
    rollback(session_factory, LedgerWriter(session_factory), session_id, hot, target_tick=1)

    kernel_writer.append(session_id, [LedgerEvent(event_type="MOVEMENT_STEP", tick=2)])
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


def test_recover_resets_broadcast_transport(
    session_factory: sessionmaker[Session], redis_client: redis.Redis, session_id: str
) -> None:
    """R7 回歸：recover 帶 transport_reset 時，殘留的 ring/seq key 被清掉。"""
    hot = RedisHotState(redis_client, session_id)
    hot.put_unit("u1", {"health": 100})
    CheckpointManager(session_factory).checkpoint(
        session_id, tick=0, state=hot.get_all(), ledger_seq=-1
    )
    # 模擬崩潰前殘留的傳輸層 key（部分遺留情境）
    redis_client.set(f"session:{session_id}:broadcast_seq", 6000)
    redis_client.rpush(f"session:{session_id}:ring", "stale")

    bc = RedisBroadcaster(redis_client, session_id)
    recover(
        session_factory,
        session_id,
        RedisHotState(redis_client, session_id),
        transport_reset=bc.reset_stream,
    )
    assert redis_client.exists(f"session:{session_id}:broadcast_seq") == 0
    assert redis_client.exists(f"session:{session_id}:ring") == 0


def test_rollback_unknown_tick_raises(
    session_factory: sessionmaker[Session], redis_client: redis.Redis, session_id: str
) -> None:
    hot = RedisHotState(redis_client, session_id)
    hot.put_unit("u1", {"health": 100})
    CheckpointManager(session_factory).checkpoint(
        session_id, tick=0, state=hot.get_all(), ledger_seq=0
    )
    with pytest.raises(RollbackTargetNotFoundError, match="無 tick=99"):
        rollback(session_factory, LedgerWriter(session_factory), session_id, hot, target_tick=99)
