"""LedgerWriter / hash chain 單元測試（SQLite in-memory，不需 compose）。"""

from __future__ import annotations

from collections.abc import Iterator
from itertools import pairwise

import pytest
from sqlalchemy import create_engine, select, update
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.models import Base, TacticalEventLog, WargameSession
from app.state.ledger import (
    GENESIS_HASH,
    LedgerEvent,
    LedgerWriter,
    canonical_json,
    compute_self_hash,
    verify_chain,
)


@pytest.fixture
def session_factory() -> Iterator[sessionmaker[Session]]:
    # StaticPool：讓 in-memory DB 在多個 session 間共用同一連線
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine, expire_on_commit=False, future=True)
    engine.dispose()


@pytest.fixture
def session_id(session_factory: sessionmaker[Session]) -> str:
    with session_factory() as db:
        ws = WargameSession(name="test", master_seed=42, current_weather={})
        db.add(ws)
        db.commit()
        return ws.id


def _ordered_events(
    session_factory: sessionmaker[Session], session_id: str
) -> list[TacticalEventLog]:
    with session_factory() as db:
        stmt = (
            select(TacticalEventLog)
            .where(TacticalEventLog.session_id == session_id)
            .order_by(TacticalEventLog.seq.asc())
        )
        return list(db.execute(stmt).scalars().all())


# ---------------- canonical_json / hash ----------------


def test_canonical_json_key_order_independent() -> None:
    a = {"b": 1, "a": 2, "c": {"z": 9, "y": 8}}
    b = {"c": {"y": 8, "z": 9}, "a": 2, "b": 1}
    assert canonical_json(a) == canonical_json(b)


def test_canonical_json_no_whitespace() -> None:
    assert canonical_json({"a": 1, "b": 2}) == '{"a":1,"b":2}'


def test_canonical_json_preserves_unicode() -> None:
    assert "藍軍" in canonical_json({"note": "藍軍推進"})


def test_compute_self_hash_deterministic() -> None:
    payload = {"seq": 0, "eventType": "DETECTION"}
    assert compute_self_hash(GENESIS_HASH, payload) == compute_self_hash(GENESIS_HASH, payload)


def test_compute_self_hash_changes_with_prev() -> None:
    payload = {"seq": 0}
    assert compute_self_hash(GENESIS_HASH, payload) != compute_self_hash("f" * 64, payload)


# ---------------- LedgerWriter ----------------


def test_append_assigns_sequential_seq_from_zero(
    session_factory: sessionmaker[Session], session_id: str
) -> None:
    writer = LedgerWriter(session_factory)
    writer.append(session_id, [LedgerEvent(event_type="DETECTION", tick=i) for i in range(5)])
    rows = _ordered_events(session_factory, session_id)
    assert [r.seq for r in rows] == [0, 1, 2, 3, 4]


def test_append_first_event_links_to_genesis(
    session_factory: sessionmaker[Session], session_id: str
) -> None:
    writer = LedgerWriter(session_factory)
    writer.append(session_id, [LedgerEvent(event_type="ORDER_ISSUED", tick=0)])
    rows = _ordered_events(session_factory, session_id)
    assert rows[0].prev_hash == GENESIS_HASH


def test_append_chains_hashes(session_factory: sessionmaker[Session], session_id: str) -> None:
    writer = LedgerWriter(session_factory)
    writer.append(session_id, [LedgerEvent(event_type="DETECTION", tick=i) for i in range(4)])
    rows = _ordered_events(session_factory, session_id)
    for prev, cur in pairwise(rows):
        assert cur.prev_hash == prev.self_hash


def test_append_returns_written_hashes(
    session_factory: sessionmaker[Session], session_id: str
) -> None:
    writer = LedgerWriter(session_factory)
    hashes = writer.append(
        session_id, [LedgerEvent(event_type="DETECTION", tick=i) for i in range(3)]
    )
    rows = _ordered_events(session_factory, session_id)
    assert hashes == [r.self_hash for r in rows]


def test_append_empty_is_noop(session_factory: sessionmaker[Session], session_id: str) -> None:
    writer = LedgerWriter(session_factory)
    assert writer.append(session_id, []) == []
    assert _ordered_events(session_factory, session_id) == []


def test_seq_continues_across_writer_instances(
    session_factory: sessionmaker[Session], session_id: str
) -> None:
    # 第二個 writer（空記憶體快取）必須由 DB 查出鏈尾接續，模擬行程重啟
    LedgerWriter(session_factory).append(
        session_id, [LedgerEvent(event_type="DETECTION", tick=i) for i in range(5)]
    )
    LedgerWriter(session_factory).append(
        session_id, [LedgerEvent(event_type="DETECTION", tick=i) for i in range(5, 8)]
    )
    rows = _ordered_events(session_factory, session_id)
    assert [r.seq for r in rows] == list(range(8))
    assert verify_chain(rows).ok


def test_sessions_have_independent_chains(session_factory: sessionmaker[Session]) -> None:
    with session_factory() as db:
        s1 = WargameSession(name="s1", master_seed=1, current_weather={})
        s2 = WargameSession(name="s2", master_seed=2, current_weather={})
        db.add_all([s1, s2])
        db.commit()
        id1, id2 = s1.id, s2.id
    writer = LedgerWriter(session_factory)
    writer.append(id1, [LedgerEvent(event_type="DETECTION", tick=0)])
    writer.append(id2, [LedgerEvent(event_type="DETECTION", tick=0)])
    assert _ordered_events(session_factory, id1)[0].seq == 0
    assert _ordered_events(session_factory, id2)[0].seq == 0


# ---------------- verify_chain ----------------


def test_verify_passes_on_clean_chain(
    session_factory: sessionmaker[Session], session_id: str
) -> None:
    LedgerWriter(session_factory).append(
        session_id, [LedgerEvent(event_type="DETECTION", tick=i) for i in range(10)]
    )
    result = verify_chain(_ordered_events(session_factory, session_id))
    assert result.ok
    assert result.verified_count == 10


def test_verify_detects_tampered_content(
    session_factory: sessionmaker[Session], session_id: str
) -> None:
    LedgerWriter(session_factory).append(
        session_id, [LedgerEvent(event_type="ENGAGEMENT_RESOLVED", tick=i) for i in range(5)]
    )
    # 竄改 seq=2 的 damageCalc（selfHash 仍為舊值 → 重算對不上）
    with session_factory() as db:
        db.execute(
            update(TacticalEventLog)
            .where(TacticalEventLog.session_id == session_id, TacticalEventLog.seq == 2)
            .values(damage_calc=999.0)
        )
        db.commit()
    result = verify_chain(_ordered_events(session_factory, session_id))
    assert not result.ok
    assert result.break_seq == 2
    assert "selfHash" in (result.reason or "")


def test_verify_detects_deleted_event(
    session_factory: sessionmaker[Session], session_id: str
) -> None:
    LedgerWriter(session_factory).append(
        session_id, [LedgerEvent(event_type="DETECTION", tick=i) for i in range(5)]
    )
    with session_factory() as db:
        db.execute(
            TacticalEventLog.__table__.delete().where(
                (TacticalEventLog.session_id == session_id) & (TacticalEventLog.seq == 2)
            )
        )
        db.commit()
    result = verify_chain(_ordered_events(session_factory, session_id))
    assert not result.ok
    assert result.break_seq == 3  # 刪掉 2 後，第 3 筆出現在預期 seq=2 的位置
    assert "seq 不連續" in (result.reason or "")


def test_verify_empty_chain_ok() -> None:
    assert verify_chain([]).ok
