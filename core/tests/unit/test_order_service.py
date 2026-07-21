"""OrderService 測試（O3.1）：submit（VALIDATED/REJECTED 落庫）+ cancel（狀態機）。"""

from __future__ import annotations

import pytest
from _order_fakes import FakeGateway, OrderWorld, seed_world
from sqlalchemy.orm import Session, sessionmaker

from app.errors import IllegalOrderTransitionError, OrderNotFoundError, PrecheckFailedError
from app.models.enums import OrderStatus
from app.models.tables import Order
from app.orders.schemas import OrderRequest, OrderType
from app.orders.service import OrderService


def _move_req(world: OrderWorld) -> OrderRequest:
    return OrderRequest(
        unit_id=world.blue_unit_id,
        order_type=OrderType.MOVE,
        payload={"to_h3": "8a2a1072b59ffff", "mobility_profile": "FOOT"},
    )


def test_submit_feasible_persists_validated(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    with session_factory() as db:
        resp = OrderService(db, FakeGateway(reachable=True), tick_source=lambda: 7).submit(
            world.session_id, _move_req(world), world.blue_issuer_id
        )
        assert resp.status is OrderStatus.VALIDATED
        assert resp.precheck is not None and resp.precheck.feasible
        assert resp.issued_at_tick == 7
        stored = db.get(Order, resp.id)
        assert stored is not None and stored.status is OrderStatus.VALIDATED
        assert stored.precheck["feasible"] is True  # 預檢快照落庫


def test_submit_infeasible_persists_rejected_and_raises(
    session_factory: sessionmaker[Session],
) -> None:
    world = seed_world(session_factory)
    with session_factory() as db:
        service = OrderService(db, FakeGateway(reachable=False))
        with pytest.raises(PrecheckFailedError) as ei:
            service.submit(world.session_id, _move_req(world), world.blue_issuer_id)
        assert ei.value.error_code == "ORDER_UNREACHABLE"
        assert ei.value.http_status == 422
        order_id = ei.value.details["order_id"]
        stored = db.get(Order, order_id)
        assert stored is not None and stored.status is OrderStatus.REJECTED  # 仍落庫供稽核


def test_cancel_validated_order(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    with session_factory() as db:
        service = OrderService(db, FakeGateway(reachable=True))
        created = service.submit(world.session_id, _move_req(world), world.blue_issuer_id)
        cancelled = service.cancel(world.session_id, created.id)
        assert cancelled.status is OrderStatus.CANCELLED


def test_cancel_unknown_order(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    with session_factory() as db, pytest.raises(OrderNotFoundError):
        OrderService(db, FakeGateway()).cancel(world.session_id, "nope")


def test_cannot_cancel_executing(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    with session_factory() as db:
        service = OrderService(db, FakeGateway(reachable=True))
        created = service.submit(world.session_id, _move_req(world), world.blue_issuer_id)
        db.get(Order, created.id).status = OrderStatus.EXECUTING  # type: ignore[union-attr]
        db.commit()
        with pytest.raises(IllegalOrderTransitionError):
            service.cancel(world.session_id, created.id)
