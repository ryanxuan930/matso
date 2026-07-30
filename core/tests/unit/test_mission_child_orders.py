"""子令與母任務令的連動（WP-A2 卡 2）：去重鍵、取消連帶、既成事實不追溯。

`OrderService.cancel` 在此之前**只動一列**——取消任務令不會取消它分解出來的子令。
操作員按了取消卻看見部隊照樣前進，那比不給取消更糟。
"""

from __future__ import annotations

import pytest
from _order_fakes import OrderWorld, seed_world
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.models.enums import OrderStatus
from app.models.tables import Order
from app.orders.schemas import OrderRequest, OrderType
from app.orders.service import OrderService


@pytest.fixture
def world(session_factory: sessionmaker[Session]) -> OrderWorld:
    return seed_world(session_factory)


def _svc(session_factory: sessionmaker[Session]) -> tuple[OrderService, Session]:
    from _order_fakes import FakeGateway

    db = session_factory()
    return OrderService(db, FakeGateway()), db


def _move(unit_id: str, h3: str = "8a2a1072b59ffff") -> OrderRequest:
    return OrderRequest(
        unit_id=unit_id,
        order_type=OrderType.MOVE,
        payload={"to_h3": h3, "mobility_profile": "FOOT"},
    )


def test_child_orders_record_their_parent(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    svc, db = _svc(session_factory)
    parent = svc.submit(
        world.session_id,
        OrderRequest(
            unit_id=world.blue_unit_id,
            order_type=OrderType.MISSION,
            payload={
                "mission_type": "SEIZE",
                "params": {"objective": {"lat": 23.75, "lng": 121.25}},
            },
        ),
        world.blue_issuer_id,
    )
    child = svc.submit(
        world.session_id,
        _move(world.blue_unit_id),
        world.blue_issuer_id,
        parent_order_id=parent.id,
    )
    assert child.parent_order_id == parent.id
    assert parent.parent_order_id is None  # 直接下的令沒有母令
    assert parent.mission_type == "SEIZE"
    db.close()


def test_cancelling_the_mission_cancels_its_children(
    session_factory: sessionmaker[Session],
) -> None:
    """**這是本檔的主線**：只取消母令的話，部隊會繼續執行已經送出去的子令。"""
    world = seed_world(session_factory)
    svc, db = _svc(session_factory)
    parent = svc.submit(
        world.session_id,
        OrderRequest(
            unit_id=world.blue_unit_id,
            order_type=OrderType.MISSION,
            payload={
                "mission_type": "SEIZE",
                "params": {"objective": {"lat": 23.75, "lng": 121.25}},
            },
        ),
        world.blue_issuer_id,
    )
    child = svc.submit(
        world.session_id,
        _move(world.blue_unit_id),
        world.blue_issuer_id,
        parent_order_id=parent.id,
    )
    svc.cancel(world.session_id, parent.id, "BLUE", omniscient=True)

    rows = {o.id: o.status for o in db.execute(select(Order)).scalars().all()}
    assert rows[parent.id] is OrderStatus.CANCELLED
    assert rows[child.id] is OrderStatus.CANCELLED
    db.close()


def test_finished_children_are_not_retroactively_cancelled(
    session_factory: sessionmaker[Session],
) -> None:
    """已終結的子令是**既成事實**（走過的路、開過的火）——AAR 要看得到，不追溯改寫。"""
    world = seed_world(session_factory)
    svc, db = _svc(session_factory)
    parent = svc.submit(
        world.session_id,
        OrderRequest(
            unit_id=world.blue_unit_id,
            order_type=OrderType.MISSION,
            payload={"mission_type": "DEFEND", "params": {"area": {"lat": 23.75, "lng": 121.25}}},
        ),
        world.blue_issuer_id,
    )
    done = svc.submit(
        world.session_id,
        _move(world.blue_unit_id),
        world.blue_issuer_id,
        parent_order_id=parent.id,
    )
    row = db.get(Order, done.id)
    assert row is not None
    row.status = OrderStatus.COMPLETED
    db.commit()

    svc.cancel(world.session_id, parent.id, "BLUE", omniscient=True)
    assert db.get(Order, done.id).status is OrderStatus.COMPLETED  # type: ignore[union-attr]
    db.close()


def test_dedupe_key_includes_the_parent(session_factory: sessionmaker[Session]) -> None:
    """兩道不同的任務可能對同一個單位分解出**座標完全相同**的 MOVE。

    不分母令的話，後一道任務會拿到前一道的子令當成自己的——於是取消前一道任務
    會連帶取消後一道的子令。
    """
    world = seed_world(session_factory)
    svc, db = _svc(session_factory)
    a = svc.submit(
        world.session_id,
        _move(world.blue_unit_id),
        world.blue_issuer_id,
        parent_order_id="mission-A",
    )
    b = svc.submit(
        world.session_id,
        _move(world.blue_unit_id),
        world.blue_issuer_id,
        parent_order_id="mission-B",
    )
    assert a.id != b.id, "不同母令的同款子令不得被去重合併"
    db.close()


def test_same_mission_redecomposing_is_deduped(session_factory: sessionmaker[Session]) -> None:
    """分解器每 tick 都會跑，「還在路上」時就是會重覆算出同一個目標點——那要被去重。"""
    world = seed_world(session_factory)
    svc, db = _svc(session_factory)
    a = svc.submit(
        world.session_id,
        _move(world.blue_unit_id),
        world.blue_issuer_id,
        parent_order_id="mission-A",
    )
    again = svc.submit(
        world.session_id,
        _move(world.blue_unit_id),
        world.blue_issuer_id,
        parent_order_id="mission-A",
    )
    assert a.id == again.id
    db.close()


def test_hand_issued_orders_still_dedupe_among_themselves(
    session_factory: sessionmaker[Session],
) -> None:
    """既有行為零變更：直接下的令（parent=None）之間照舊去重。"""
    world = seed_world(session_factory)
    svc, db = _svc(session_factory)
    a = svc.submit(world.session_id, _move(world.blue_unit_id), world.blue_issuer_id)
    b = svc.submit(world.session_id, _move(world.blue_unit_id), world.blue_issuer_id)
    assert a.id == b.id
    assert a.parent_order_id is None
    db.close()
