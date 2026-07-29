"""WP-E1：runner 重啟的續接點判定（`app.state.resume`）。

改版前 `sim_runtime` 建 `SimClock(tick_rate_ms=...)` 不帶 start_tick → 每次重建 runner
（core 重啟 / restart 旗標 / rollback）該局的 sim tick 都歸零。這批測試釘住新的判定。
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy.orm import Session, sessionmaker

from app.models import WargameSession
from app.state.checkpoint import CheckpointManager
from app.state.resume import read_live_tick, resume_tick


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
