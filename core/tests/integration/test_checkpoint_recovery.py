"""Checkpoint 崩潰復原整合測試（MariaDB:3307 + Redis:6379；fixture 見 conftest）。

驗收（TASKS.md O1.5）：跑 N ticks → 清 Redis 模擬崩潰 → recover → 狀態 hash 與崩潰前一致。
含 O1.7 review 修復的 DB 級回歸（R1 stale tip / R2 rollback×recover / R7 transport reset）。
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable, Iterator

import pytest
import redis
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.engine.clock import SimClock, SimTime
from app.engine.kernel import Kernel
from app.engine.rng import DeterministicRNG
from app.errors import RollbackTargetNotFoundError
from app.models import SimCheckpoint, TacticalEventLog, TacticalUnit, WargameSession
from app.models.enums import UnitLevel
from app.state.broadcaster import RedisBroadcaster
from app.state.checkpoint import CheckpointManager, compute_state_hash, recover, rollback
from app.state.hot_state import RedisHotState
from app.state.ledger import LedgerEvent, LedgerWriter, verify_chain
from app.state.resume import resume_session

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
        # WP-E1 的活 Kernel 測試會寫 initiatorId=u1 的事件，而 TacticalEventLog.initiatorId
        # 對 TacticalUnit 有 FK（MariaDB 才擋得到，SQLite 不會）。
        db.add(
            TacticalUnit(
                id="u1",
                session_id=sid,
                designation="U1",
                unit_level=UnitLevel.SQUAD,
                faction="BLUE",
            )
        )
        db.commit()
    yield sid
    with session_factory() as db:
        db.execute(TacticalUnit.__table__.delete().where(TacticalUnit.session_id == sid))
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


# --- WP-E1 驗收：真 Kernel 落快照 → 崩潰 → 復原，狀態雜湊一致 ---


class _WalkMovement:
    """每 tick 依 RNG 走一步並發 UNIT_MOVED（帶 lat/lng）——足以驗前滾與 RNG 續接。"""

    _STEPS = ((1, 0), (0, 1), (-1, 0), (0, -1))

    def __init__(self, hot: RedisHotState, rng: DeterministicRNG, unit_id: str = "u1") -> None:
        self._hot, self._rng, self._unit = hot, rng, unit_id

    async def step(self, now: SimTime) -> list[LedgerEvent]:
        dlat, dlng = self._rng.choice(self._STEPS)
        cur = self._hot.get_unit(self._unit) or {"lat": 0, "lng": 0}
        lat, lng = int(cur["lat"]) + dlat, int(cur["lng"]) + dlng  # 整數格點：避開浮點格式化
        self._hot.update_unit(self._unit, {"lat": lat, "lng": lng})
        return [
            LedgerEvent(
                event_type="UNIT_MOVED",
                tick=now.tick,
                initiator_id=self._unit,
                detail={"lat": lat, "lng": lng},
            )
        ]


def _build_kernel(
    build_noop_kernel: Callable[..., Kernel],
    session_factory: sessionmaker[Session],
    redis_client: redis.Redis,
    sid: str,
    rngs: dict[str, DeterministicRNG],
    interval: int,
    start_tick: int = 0,
) -> tuple[Kernel, RedisHotState]:
    hot = RedisHotState(redis_client, sid)
    kernel = build_noop_kernel(
        session_id=sid,
        clock=SimClock(tick_rate_ms=1000, start_tick=start_tick),
        movement=_WalkMovement(hot, rngs["movement"]),
        hot_state=hot,
        broadcaster=RedisBroadcaster(redis_client, sid),  # 真廣播器才會寫 tick 鍵
        event_sink=LedgerWriter(session_factory),
        checkpointer=CheckpointManager(
            session_factory,
            extras_provider=lambda: {"rng": {k: r.get_state() for k, r in rngs.items()}},
        ),
        checkpoint_interval=interval,
    )
    return kernel, hot


def test_live_kernel_crash_and_resume_matches_state_hash(
    build_noop_kernel: Callable[..., Kernel],
    session_factory: sessionmaker[Session],
    redis_client: redis.Redis,
    session_id: str,
) -> None:
    """WP-E1 驗收：活 Kernel 落快照 → 清 Redis（模擬 kill -9）→ resume → 雜湊一致、tick 續接。

    刻意讓崩潰落在**快照之後**（tick 5 快照、跑到 tick 7）——只還原快照會少兩步，
    要靠前滾投影 Ledger 尾段才對得上。
    """
    rngs = {s: DeterministicRNG(11, s) for s in ("movement", "adjudication")}
    kernel, hot = _build_kernel(
        build_noop_kernel, session_factory, redis_client, session_id, rngs, interval=5
    )
    hot.put_unit("u1", {"lat": 0, "lng": 0})
    asyncio.run(kernel.run(8))  # tick 0..7；於 tick 0 與 5 落快照

    pre_hash = compute_state_hash(hot.get_all())
    # 崩潰**沒發生**的話，接下來三個 tick 會抽到的東西
    live_next = [rngs["movement"].choice(_WalkMovement._STEPS) for _ in range(3)]

    _wipe_redis(redis_client, session_id)  # kill -9：Redis 一併沒了
    crashed = RedisHotState(redis_client, session_id)
    assert crashed.get_all() == {}

    fresh = {s: DeterministicRNG(11, s) for s in ("movement", "adjudication")}
    result = resume_session(
        session_factory=session_factory,
        client=redis_client,
        session_id=session_id,
        hot=crashed,
        rngs=fresh,
    )
    assert result.restored_from_checkpoint
    assert result.restored_tick == 5
    assert result.forward_rolled_events == 2  # tick 6、7 的兩步
    assert result.rng_streams_restored == 2
    assert result.start_tick == 8  # 續接點＝崩潰前跑完的最後一個 tick + 1
    assert compute_state_hash(crashed.get_all()) == pre_hash
    # RNG 接回**快照當下**（tick 5），不是崩潰當下——前滾能重建狀態，重建不了「抽過幾次」。
    # 代價是倒退兩抽（tick 6、7 各一次）後重新併回同一條序列；相對於不還原（從 tick 0
    # 整段重播）這是嚴格較好的。這條斷言把「最多倒退一個快照間隔」釘住。
    replayed = [fresh["movement"].choice(_WalkMovement._STEPS) for _ in range(5)]
    assert replayed[2:] == live_next


def test_resume_keeps_surviving_hot_state_and_does_not_rewind(
    build_noop_kernel: Callable[..., Kernel],
    session_factory: sessionmaker[Session],
    redis_client: redis.Redis,
    session_id: str,
) -> None:
    """只有 core 掛掉（Redis 活著）：熱狀態比快照新，不得被快照倒退。"""
    rngs = {"movement": DeterministicRNG(11, "movement")}
    kernel, hot = _build_kernel(
        build_noop_kernel, session_factory, redis_client, session_id, rngs, interval=5
    )
    hot.put_unit("u1", {"lat": 0, "lng": 0})
    asyncio.run(kernel.run(8))
    live_hash = compute_state_hash(hot.get_all())

    survivor = RedisHotState(redis_client, session_id)
    result = resume_session(
        session_factory=session_factory,
        client=redis_client,
        session_id=session_id,
        hot=survivor,
        rngs={"movement": DeterministicRNG(11, "movement")},
    )
    assert not result.restored_from_checkpoint
    assert result.rng_streams_restored == 1  # RNG 仍要還原（它只活在記憶體）
    assert result.start_tick == 8
    assert compute_state_hash(survivor.get_all()) == live_hash
