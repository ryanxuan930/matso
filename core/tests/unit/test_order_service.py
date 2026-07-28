"""OrderService 測試（O3.1）：submit（VALIDATED/REJECTED 落庫）+ cancel（狀態機）。"""

from __future__ import annotations

import pytest
from _order_fakes import FakeGateway, OrderWorld, seed_world
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.errors import OrderNotFoundError, PrecheckFailedError
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


def test_duplicate_submit_returns_existing_not_new(
    session_factory: sessionmaker[Session],
) -> None:
    # 補充 2d：同單位 + 同型別 + 同 payload 的未終結指令重複下達 → 回既有、不新增（先到先處理）。
    world = seed_world(session_factory)
    with session_factory() as db:
        service = OrderService(db, FakeGateway(reachable=True))
        first = service.submit(world.session_id, _move_req(world), world.blue_issuer_id)
        second = service.submit(world.session_id, _move_req(world), world.blue_issuer_id)
        assert second.id == first.id  # 重複被忽略，回既有指令
        count = len(db.execute(select(Order)).scalars().all())
        assert count == 1  # 只落庫一筆


def test_different_payload_is_not_duplicate(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    with session_factory() as db:
        service = OrderService(db, FakeGateway(reachable=True))
        service.submit(world.session_id, _move_req(world), world.blue_issuer_id)
        other = OrderRequest(
            unit_id=world.blue_unit_id,
            order_type=OrderType.MOVE,
            payload={"to_h3": "8a2a1072b5affff", "mobility_profile": "FOOT"},  # 不同目標
        )
        service.submit(world.session_id, other, world.blue_issuer_id)
        assert len(db.execute(select(Order)).scalars().all()) == 2  # 非重複 → 兩筆


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
        cancelled = service.cancel(world.session_id, created.id, "BLUE", False)
        assert cancelled.status is OrderStatus.CANCELLED


def test_cancel_cross_faction_denied_as_not_found(session_factory: sessionmaker[Session]) -> None:
    """C1：RED 不可取消 BLUE 的指令；為 fog of war 一致，以「不存在」回應（非 403）。"""
    world = seed_world(session_factory)
    with session_factory() as db:
        service = OrderService(db, FakeGateway(reachable=True))
        created = service.submit(world.session_id, _move_req(world), world.blue_issuer_id)
        with pytest.raises(OrderNotFoundError):
            service.cancel(world.session_id, created.id, "RED", False)  # 敵陣營
        # 全知者（統裁）仍可取消
        assert service.cancel(world.session_id, created.id, "RED", True).status is (
            OrderStatus.CANCELLED
        )


def test_cancel_unknown_order(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    with session_factory() as db, pytest.raises(OrderNotFoundError):
        OrderService(db, FakeGateway()).cancel(world.session_id, "nope", "BLUE", False)


def test_cancel_executing_order_freezes_in_place(session_factory: sessionmaker[Session]) -> None:
    # 取消執行中的移動＝就地凍結（#15）：狀態機允許 EXECUTING→CANCELLED，移動系統遂不再推進該單位。
    world = seed_world(session_factory)
    with session_factory() as db:
        service = OrderService(db, FakeGateway(reachable=True))
        created = service.submit(world.session_id, _move_req(world), world.blue_issuer_id)
        db.get(Order, created.id).status = OrderStatus.EXECUTING  # type: ignore[union-attr]
        db.commit()
        cancelled = service.cancel(world.session_id, created.id, "BLUE", False)
        assert cancelled.status is OrderStatus.CANCELLED
