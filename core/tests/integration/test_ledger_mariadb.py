"""Ledger 整合測試（連 compose 的 MariaDB:3307；fixture 見 integration/conftest.py）。

驗收（TASKS.md O1.2）：寫 100 事件 → verify 過；UPDATE 一筆 → verify 必須抓到。
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from app.models import TacticalEventLog, WargameSession
from app.state.ledger import LedgerEvent, LedgerWriter, verify_chain

pytestmark = pytest.mark.integration


@pytest.fixture
def session_id(session_factory: sessionmaker[Session]) -> Iterator[str]:
    with session_factory() as db:
        ws = WargameSession(name="itest-ledger", master_seed=7, current_weather={})
        db.add(ws)
        db.commit()
        sid = ws.id
    yield sid
    # 清理：先刪事件（FK），再刪 session
    with session_factory() as db:
        db.execute(TacticalEventLog.__table__.delete().where(TacticalEventLog.session_id == sid))
        db.execute(WargameSession.__table__.delete().where(WargameSession.id == sid))
        db.commit()


def _ordered(session_factory: sessionmaker[Session], sid: str) -> list[TacticalEventLog]:
    with session_factory() as db:
        stmt = (
            select(TacticalEventLog)
            .where(TacticalEventLog.session_id == sid)
            .order_by(TacticalEventLog.seq.asc())
        )
        return list(db.execute(stmt).scalars().all())


def test_write_100_events_verify_ok(
    session_factory: sessionmaker[Session], session_id: str
) -> None:
    writer = LedgerWriter(session_factory)
    writer.append(
        session_id,
        [LedgerEvent(event_type="MOVEMENT_STEP", tick=i, terrain_modifier=1.0) for i in range(100)],
    )
    rows = _ordered(session_factory, session_id)
    assert len(rows) == 100
    result = verify_chain(rows)
    assert result.ok
    assert result.verified_count == 100


def test_tampered_row_detected(session_factory: sessionmaker[Session], session_id: str) -> None:
    writer = LedgerWriter(session_factory)
    writer.append(
        session_id,
        [LedgerEvent(event_type="ENGAGEMENT_RESOLVED", tick=i) for i in range(100)],
    )
    # 以 root 直接竄改 seq=50 的內容，模擬繞過應用層的攻擊
    with session_factory() as db:
        db.execute(
            update(TacticalEventLog)
            .where(TacticalEventLog.session_id == session_id, TacticalEventLog.seq == 50)
            .values(damage_calc=1234.5)
        )
        db.commit()
    result = verify_chain(_ordered(session_factory, session_id))
    assert not result.ok
    assert result.break_seq == 50
