"""WP-E1：runner 重啟的續接點判定（`app.state.resume`）。

改版前 `sim_runtime` 建 `SimClock(tick_rate_ms=...)` 不帶 start_tick → 每次重建 runner
（core 重啟 / restart 旗標 / rollback）該局的 sim tick 都歸零。這批測試釘住新的判定。
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy.orm import Session, sessionmaker

from app.engine.rng import DeterministicRNG
from app.models import SimCheckpoint, WargameSession
from app.state.checkpoint import (
    CheckpointManager,
    compute_state_hash,
    serialize_state,
)
from app.state.hot_state import InMemoryHotState
from app.state.ledger import LedgerEvent, LedgerWriter
from app.state.resume import (
    ResumeResult,
    forward_roll,
    read_live_tick,
    resume_session,
    resume_tick,
)


class FakeRedis:
    """只需要 get()；`explode=True` 模擬 Redis 掛掉。"""

    def __init__(self, values: dict[str, Any] | None = None, explode: bool = False) -> None:
        self._values = values or {}
        self._explode = explode

    def get(self, key: str) -> Any:
        if self._explode:
            raise ConnectionError("redis down")
        return self._values.get(key)


@pytest.fixture
def session_id(session_factory: sessionmaker[Session]) -> str:
    with session_factory() as db:
        ws = WargameSession(name="resume", master_seed=1, current_weather={})
        db.add(ws)
        db.commit()
        return str(ws.id)


def test_resume_from_redis_tick(session_factory: sessionmaker[Session], session_id: str) -> None:
    # tick 鍵是「已跑完的 tick」→ 續接點是它的下一個
    client = FakeRedis({f"session:{session_id}:tick": "417"})
    assert resume_tick(session_factory, client, session_id) == 418


def test_resume_from_checkpoint_when_redis_wiped(
    session_factory: sessionmaker[Session], session_id: str
) -> None:
    CheckpointManager(session_factory).checkpoint(
        session_id, tick=300, state={"u1": {"health": 90}}, ledger_seq=42
    )
    assert resume_tick(session_factory, FakeRedis(), session_id) == 301


def test_redis_tick_wins_over_older_checkpoint(
    session_factory: sessionmaker[Session], session_id: str
) -> None:
    # Redis 存活但 core 崩潰：熱狀態比快照新，續接點必須跟著熱狀態走
    CheckpointManager(session_factory).checkpoint(
        session_id, tick=300, state={"u1": {"health": 90}}, ledger_seq=42
    )
    client = FakeRedis({f"session:{session_id}:tick": "355"})
    assert resume_tick(session_factory, client, session_id) == 356


def test_fresh_session_starts_at_zero(
    session_factory: sessionmaker[Session], session_id: str
) -> None:
    assert resume_tick(session_factory, FakeRedis(), session_id) == 0


def test_rolled_back_checkpoint_is_honoured(
    session_factory: sessionmaker[Session], session_id: str
) -> None:
    """rollback 已丟掉較晚的快照且清了 tick 鍵 → 續接點回到回滾目標，不得被較大的 tick 拉回去。

    這是「不看 Ledger 最大 tick」的理由：被棄世代的事件 tick 更大，看它等於抵銷回滾。
    """
    mgr = CheckpointManager(session_factory)
    mgr.checkpoint(session_id, tick=100, state={"u1": {"health": 100}}, ledger_seq=10)
    # tick=900 的快照代表被棄世代——rollback 會刪掉它，這裡直接模擬刪後的狀態
    assert resume_tick(session_factory, FakeRedis(), session_id) == 101


def test_load_latest_orders_by_ledger_seq_not_tick(
    session_factory: sessionmaker[Session], session_id: str
) -> None:
    """續接點取自 `load_latest`（依 ledgerSeq）——rollback 後 tick 非單調，seq 才是身分。"""
    mgr = CheckpointManager(session_factory)
    mgr.checkpoint(session_id, tick=900, state={"u1": {"health": 10}}, ledger_seq=5)
    mgr.checkpoint(session_id, tick=100, state={"u1": {"health": 100}}, ledger_seq=99)
    assert resume_tick(session_factory, FakeRedis(), session_id) == 101


def test_redis_failure_falls_back_instead_of_raising(
    session_factory: sessionmaker[Session], session_id: str
) -> None:
    # Redis 掛掉不該讓 runner 起不來
    assert read_live_tick(FakeRedis(explode=True), session_id) is None
    assert resume_tick(session_factory, FakeRedis(explode=True), session_id) == 0


def test_garbage_tick_value_is_ignored(
    session_factory: sessionmaker[Session], session_id: str
) -> None:
    client = FakeRedis({f"session:{session_id}:tick": "not-a-number"})
    assert read_live_tick(client, session_id) is None


def test_tick_zero_is_distinguished_from_missing(
    session_factory: sessionmaker[Session], session_id: str
) -> None:
    # 剛跑完 tick 0 的局：續接點是 1，不是 0（否則 tick 0 會重跑）
    client = FakeRedis({f"session:{session_id}:tick": "0"})
    assert read_live_tick(client, session_id) == 0
    assert resume_tick(session_factory, client, session_id) == 1


# --- WP-E1 (3)(4)：快照信封 + 前滾 + 完整復原路徑 ---


def _hot() -> InMemoryHotState:
    return InMemoryHotState()


def test_snapshot_envelope_roundtrip_carries_rng_and_orders(
    session_factory: sessionmaker[Session], session_id: str
) -> None:
    rng = DeterministicRNG(master_seed=5, stream_id="adjudication")
    rng.random()
    mgr = CheckpointManager(
        session_factory, extras_provider=lambda: {"rng": {"adjudication": rng.get_state()}}
    )
    mgr.checkpoint(session_id, tick=10, state={"u1": {"health": 70}}, ledger_seq=3)

    record = mgr.load_latest(session_id)
    assert record is not None
    assert record.state == {"u1": {"health": 70}}
    assert record.rng_states()["adjudication"]["stream_id"] == "adjudication"
    assert record.order_states() == {}  # 這局沒有令
    # 驗收比的「狀態雜湊」只涵蓋 units——不可因為信封多了 rng 就變
    assert record.state_hash == compute_state_hash({"u1": {"health": 70}})


def test_legacy_v1_blob_still_loads(
    session_factory: sessionmaker[Session], session_id: str
) -> None:
    """WP-E1 之前寫下的裸 units map 快照必須照樣讀得回來（不需資料遷移）。"""
    with session_factory() as db:
        db.add(
            SimCheckpoint(
                session_id=session_id,
                tick=4,
                ledger_seq=1,
                state_blob=serialize_state({"u1": {"health": 55}}),
                state_hash=compute_state_hash({"u1": {"health": 55}}),
            )
        )
        db.commit()
    record = CheckpointManager(session_factory).load_latest(session_id)
    assert record is not None
    assert record.state == {"u1": {"health": 55}}
    assert record.extras == {}


def test_extras_provider_failure_does_not_lose_the_snapshot(
    session_factory: sessionmaker[Session], session_id: str
) -> None:
    def _boom() -> dict[str, Any]:
        raise RuntimeError("rng 取不到")

    CheckpointManager(session_factory, extras_provider=_boom).checkpoint(
        session_id, tick=1, state={"u1": {"health": 100}}, ledger_seq=0
    )
    record = CheckpointManager(session_factory).load_latest(session_id)
    assert record is not None and record.state == {"u1": {"health": 100}}


def test_forward_roll_projects_movement_and_engagement(
    session_factory: sessionmaker[Session], session_id: str
) -> None:
    writer = LedgerWriter(session_factory)
    writer.append(session_id, [LedgerEvent(event_type="MOVEMENT_STEP", tick=0)])
    after = writer.tip_seq(session_id)  # 快照錨在這裡
    writer.append(
        session_id,
        [
            LedgerEvent(
                event_type="UNIT_MOVED",
                tick=1,
                initiator_id="u1",
                detail={"lat": 24.5, "lng": 120.5},
            ),
            LedgerEvent(
                event_type="ENGAGEMENT_RESOLVED",
                tick=2,
                initiator_id="u2",
                target_id="u1",
                ai_decision={"target_health_after": 40.0, "target_strength_after": 12.0},
            ),
            LedgerEvent(
                event_type="UNIT_ARRIVED",
                tick=3,
                initiator_id="u1",
                detail={"lat": 24.9, "lng": 120.9},
            ),
        ],
    )
    hot = _hot()
    hot.put_unit("u1", {"lat": 24.0, "lng": 120.0, "health": 100.0, "strength": 30.0})

    assert forward_roll(session_factory, session_id, hot, after) == 3
    assert hot.get_unit("u1") == {
        "lat": 24.9,  # 最後一則位置事件勝出（依 seq 順序套用）
        "lng": 120.9,
        "health": 40.0,
        "strength": 12.0,
    }


def test_forward_roll_ignores_events_before_the_checkpoint(
    session_factory: sessionmaker[Session], session_id: str
) -> None:
    writer = LedgerWriter(session_factory)
    writer.append(
        session_id,
        [
            LedgerEvent(
                event_type="UNIT_MOVED", tick=0, initiator_id="u1", detail={"lat": 1, "lng": 2}
            )
        ],
    )
    hot = _hot()
    hot.put_unit("u1", {"lat": 9.0, "lng": 9.0})
    assert forward_roll(session_factory, session_id, hot, writer.tip_seq(session_id)) == 0
    assert hot.get_unit("u1") == {"lat": 9.0, "lng": 9.0}


def test_forward_roll_skips_events_without_state(
    session_factory: sessionmaker[Session], session_id: str
) -> None:
    """只投影確實帶著結果值的事件——推測性映射會安靜地寫錯狀態。"""
    writer = LedgerWriter(session_factory)
    writer.append(
        session_id,
        [
            LedgerEvent(event_type="TICK_OVERRUN", tick=1, detail={"duration_ms": 9}),
            LedgerEvent(event_type="ORDER_SUBMITTED", tick=1, initiator_id="u1"),
            # 交戰被合法性擋下（REJECTED）→ 沒有 health/strength 可套
            LedgerEvent(
                event_type="ENGAGEMENT_RESOLVED",
                tick=1,
                target_id="u1",
                ai_decision={"status": "REJECTED", "reason": "OUT_OF_RANGE"},
            ),
        ],
    )
    hot = _hot()
    hot.put_unit("u1", {"health": 100.0})
    assert forward_roll(session_factory, session_id, hot, -1) == 0
    assert hot.get_unit("u1") == {"health": 100.0}


def test_resume_restores_hot_state_when_redis_is_gone(
    session_factory: sessionmaker[Session], session_id: str
) -> None:
    pre = {"u1": {"lat": 24.0, "lng": 120.0, "health": 80.0}}
    CheckpointManager(session_factory).checkpoint(session_id, tick=50, state=pre, ledger_seq=-1)
    writer = LedgerWriter(session_factory)
    writer.append(
        session_id,
        [
            LedgerEvent(
                event_type="UNIT_MOVED",
                tick=51,
                initiator_id="u1",
                detail={"lat": 25.0, "lng": 121.0},
            )
        ],
    )
    hot = _hot()  # 空的＝Redis 被清掉
    result = resume_session(
        session_factory=session_factory, client=FakeRedis(), session_id=session_id, hot=hot
    )
    assert result.restored_from_checkpoint
    assert result.restored_tick == 50
    assert result.forward_rolled_events == 1
    assert result.start_tick == 51
    assert hot.get_unit("u1") == {"lat": 25.0, "lng": 121.0, "health": 80.0}


def test_resume_does_not_clobber_surviving_hot_state(
    session_factory: sessionmaker[Session], session_id: str
) -> None:
    """core 崩潰但 Redis 存活：熱狀態比快照新，套快照等於把進度倒退一個間隔。"""
    CheckpointManager(session_factory).checkpoint(
        session_id, tick=50, state={"u1": {"health": 80.0}}, ledger_seq=-1
    )
    hot = _hot()
    hot.put_unit("u1", {"health": 35.0})  # 快照之後又被打了
    result = resume_session(
        session_factory=session_factory,
        client=FakeRedis({f"session:{session_id}:tick": "77"}),
        session_id=session_id,
        hot=hot,
    )
    assert not result.restored_from_checkpoint
    assert result.start_tick == 78
    assert hot.get_unit("u1") == {"health": 35.0}


def test_resume_restores_rng_even_when_redis_survived(
    session_factory: sessionmaker[Session], session_id: str
) -> None:
    """RNG 只活在記憶體，任何重啟都會失去——與 Redis 存活與否無關。"""
    live = DeterministicRNG(master_seed=5, stream_id="adjudication")
    [live.random() for _ in range(9)]
    CheckpointManager(
        session_factory, extras_provider=lambda: {"rng": {"adjudication": live.get_state()}}
    ).checkpoint(session_id, tick=50, state={"u1": {"health": 80.0}}, ledger_seq=-1)
    expected = [live.random() for _ in range(5)]

    fresh = DeterministicRNG(master_seed=5, stream_id="adjudication")
    hot = _hot()
    hot.put_unit("u1", {"health": 35.0})
    result = resume_session(
        session_factory=session_factory,
        client=FakeRedis({f"session:{session_id}:tick": "77"}),
        session_id=session_id,
        hot=hot,
        rngs={"adjudication": fresh},
    )
    assert result.rng_streams_restored == 1
    assert [fresh.random() for _ in range(5)] == expected


def test_resume_survives_a_stream_with_no_saved_state(
    session_factory: sessionmaker[Session], session_id: str
) -> None:
    # v1 快照（無 rng 區段）→ 各 stream 自種子開始，但不得爆掉
    CheckpointManager(session_factory).checkpoint(
        session_id, tick=1, state={"u1": {"health": 100}}, ledger_seq=-1
    )
    result = resume_session(
        session_factory=session_factory,
        client=FakeRedis(),
        session_id=session_id,
        hot=_hot(),
        rngs={"movement": DeterministicRNG(master_seed=1, stream_id="movement")},
    )
    assert result.rng_streams_restored == 0


def test_resume_on_a_never_checkpointed_session(
    session_factory: sessionmaker[Session], session_id: str
) -> None:
    result = resume_session(
        session_factory=session_factory, client=FakeRedis(), session_id=session_id, hot=_hot()
    )
    assert result == ResumeResult(0, False, None, 0, 0)
