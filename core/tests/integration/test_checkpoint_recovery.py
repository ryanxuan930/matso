"""Checkpoint 崩潰復原整合測試（連 compose 的 MariaDB:3307 + Redis:6379）。

驗收（TASKS.md O1.5）：跑 N ticks → 清 Redis 模擬崩潰 → recover → 狀態 hash 與崩潰前一致。
任一服務未就緒則整組 skip。
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
import redis
from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.db import make_engine, make_session_factory
from app.models import SimCheckpoint, TacticalEventLog, WargameSession
from app.state.checkpoint import CheckpointManager, compute_state_hash, recover, rollback
from app.state.hot_state import RedisHotState
from app.state.ledger import LedgerEvent, LedgerWriter

pytestmark = pytest.mark.integration

DEV_DB_URL = "mysql+pymysql://root:matso_dev_root@localhost:3307/matso"
DEV_REDIS_URL = "redis://localhost:6379/0"


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    eng = make_engine(DEV_DB_URL)
    try:
        with eng.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:
        pytest.skip(f"MariaDB:3307 未就緒：{exc}")
    yield eng
    eng.dispose()


@pytest.fixture(scope="module")
def redis_client() -> Iterator[redis.Redis]:
    client = redis.Redis.from_url(DEV_REDIS_URL, decode_responses=True)
    try:
        client.ping()
    except Exception as exc:
        pytest.skip(f"Redis:6379 未就緒：{exc}")
    yield client
    client.close()


@pytest.fixture
def session_factory(engine: Engine) -> sessionmaker[Session]:
    return make_session_factory(engine)


@pytest.fixture
def session_id(session_factory: sessionmaker[Session], redis_client: redis.Redis) -> Iterator[str]:
    with session_factory() as db:
        ws = WargameSession(name="itest-ckpt", master_seed=7, current_weather={})
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


def test_crash_recovery_state_hash_matches(
    session_factory: sessionmaker[Session], redis_client: redis.Redis, session_id: str
) -> None:
    # 跑 N「ticks」：部署 + 逐步更新單位（模擬子系統寫熱狀態）
    hot = RedisHotState(redis_client, session_id)
    hot.put_unit("u1", {"lat": 25.0, "lng": 121.5, "health": 100})
    hot.put_unit("u2", {"lat": 24.0, "lng": 120.0, "health": 100})
    for tick in range(1, 6):
        hot.update_unit("u1", {"health": 100 - tick * 10})
        hot.update_unit("u2", {"lat": 24.0 + tick * 0.1})

    # 在崩潰 tick 存 checkpoint
    pre_state = hot.get_all()
    pre_hash = compute_state_hash(pre_state)
    CheckpointManager(session_factory).checkpoint(session_id, tick=5, state=pre_state)

    # 模擬崩潰：清空該 session 的 Redis 熱狀態
    for key in redis_client.scan_iter(match=f"session:{session_id}:*"):
        redis_client.delete(key)
    crashed = RedisHotState(redis_client, session_id)
    assert crashed.get_all() == {}

    # 復原
    result = recover(session_factory, session_id, crashed)
    assert result.restored
    assert result.restored_tick == 5
    assert result.events_after_checkpoint == 0
    assert crashed.get_all() == pre_state
    assert compute_state_hash(crashed.get_all()) == pre_hash


def test_recover_reports_events_after_checkpoint(
    session_factory: sessionmaker[Session], redis_client: redis.Redis, session_id: str
) -> None:
    hot = RedisHotState(redis_client, session_id)
    hot.put_unit("u1", {"health": 100})
    CheckpointManager(session_factory).checkpoint(session_id, tick=2, state=hot.get_all())
    # checkpoint 之後又發生事件（tick 3、4）
    LedgerWriter(session_factory).append(
        session_id,
        [
            LedgerEvent(event_type="MOVEMENT_STEP", tick=3),
            LedgerEvent(event_type="MOVEMENT_STEP", tick=4),
        ],
    )
    result = recover(session_factory, session_id, RedisHotState(redis_client, session_id))
    assert result.restored_tick == 2
    assert result.events_after_checkpoint == 2


def test_rollback_restores_and_writes_event(
    session_factory: sessionmaker[Session], redis_client: redis.Redis, session_id: str
) -> None:
    mgr = CheckpointManager(session_factory)
    hot = RedisHotState(redis_client, session_id)

    hot.put_unit("u1", {"health": 100})
    mgr.checkpoint(session_id, tick=0, state=hot.get_all())  # 早期 checkpoint
    hot.update_unit("u1", {"health": 20})
    mgr.checkpoint(session_id, tick=5, state=hot.get_all())  # 後期 checkpoint

    result = rollback(
        session_factory, LedgerWriter(session_factory), session_id, hot, target_tick=0
    )
    assert result.rolled_back_to_tick == 0
    assert hot.get_all() == {"u1": {"health": 100}}  # 回到早期狀態

    # ROLLBACK 事件已寫入 Ledger
    with session_factory() as db:
        events = list(
            db.execute(
                select(TacticalEventLog)
                .where(TacticalEventLog.session_id == session_id)
                .order_by(TacticalEventLog.seq.asc())
            ).scalars()
        )
    assert len(events) == 1
    assert events[0].event_type == "ROLLBACK"
    assert events[0].ai_decision["rolled_back_to"] == 0


def test_rollback_unknown_tick_raises(
    session_factory: sessionmaker[Session], redis_client: redis.Redis, session_id: str
) -> None:
    hot = RedisHotState(redis_client, session_id)
    hot.put_unit("u1", {"health": 100})
    CheckpointManager(session_factory).checkpoint(session_id, tick=0, state=hot.get_all())
    with pytest.raises(ValueError, match="無 tick=99"):
        rollback(session_factory, LedgerWriter(session_factory), session_id, hot, target_tick=99)
