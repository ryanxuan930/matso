"""Intel store（O3.3）：upsert（等級取最佳、位置取最新）+ faction 過濾。"""

from __future__ import annotations

from _order_fakes import seed_world
from sqlalchemy.orm import Session, sessionmaker

from app.intel import store
from app.intel.sweep import Contact
from app.models.enums import IntelFidelity


def _contact(target: str, fidelity: IntelFidelity, tick: int, lat: float = 23.7) -> Contact:
    return Contact(
        observer_faction="RED",
        target_unit_id=target,
        fidelity=fidelity,
        tick=tick,
        lat=lat,
        lng=121.2,
        error_radius_m=100.0,
    )


def test_record_then_query_faction_scoped(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    with session_factory() as db:
        store.record(db, world.session_id, _contact(world.blue_unit_id, IntelFidelity.DETECTED, 1))
        db.commit()
        red = store.query(db, world.session_id, "RED")
        assert len(red) == 1 and red[0].target_unit_id == world.blue_unit_id
        # BLUE 看不到 RED 的 contact（faction 過濾）
        assert store.query(db, world.session_id, "BLUE") == []


def test_upsert_keeps_best_fidelity(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    with session_factory() as db:
        store.record(db, world.session_id, _contact(world.blue_unit_id, IntelFidelity.DETECTED, 1))
        store.record(
            db, world.session_id, _contact(world.blue_unit_id, IntelFidelity.IDENTIFIED, 2)
        )
        # 較差的後續觀測不得降級，但位置/tick 仍更新
        store.record(
            db, world.session_id, _contact(world.blue_unit_id, IntelFidelity.DETECTED, 5, lat=23.9)
        )
        db.commit()
        rows = store.query(db, world.session_id, "RED")
        assert len(rows) == 1  # 同目標只一筆
        assert rows[0].fidelity is IntelFidelity.IDENTIFIED  # 不降級
        assert rows[0].last_seen_tick == 5  # 位置/tick 最新
        assert rows[0].last_seen_lat == 23.9


def test_records_are_per_target(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    with session_factory() as db:
        store.record(db, world.session_id, _contact(world.blue_unit_id, IntelFidelity.DETECTED, 1))
        store.record(db, world.session_id, _contact("other-unit", IntelFidelity.DETECTED, 1))
        db.commit()
        assert len(store.query(db, world.session_id, "RED")) == 2


def test_record_all_batch(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    with session_factory() as db:
        store.record_all(
            db,
            world.session_id,
            [
                _contact(world.blue_unit_id, IntelFidelity.DETECTED, 1),
                _contact("u2", IntelFidelity.CLASSIFIED, 1),
            ],
        )
        db.commit()
        assert len(store.query(db, world.session_id, "RED")) == 2
