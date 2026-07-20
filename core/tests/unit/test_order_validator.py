"""Order 驗證測試（O3.1，步驟 [1]）：單位存在性 / 權限 / 載荷語法。"""

from __future__ import annotations

import pytest
from _order_fakes import OrderWorld, seed_world
from sqlalchemy.orm import Session, sessionmaker

from app.errors import OrderPermissionError, OrderValidationError, SessionNotFoundError
from app.orders.schemas import MovePayload, OrderRequest, OrderType
from app.orders.validator import validate_order


def _req(world: OrderWorld, **kw: object) -> OrderRequest:
    base: dict[str, object] = {
        "unit_id": world.blue_unit_id,
        "order_type": OrderType.MOVE,
        "payload": {"to_h3": "8a2a1072b59ffff", "mobility_profile": "FOOT"},
        "issuer_id": world.blue_issuer_id,
    }
    base.update(kw)
    return OrderRequest(**base)  # type: ignore[arg-type]


def test_valid_move_returns_unit_and_payload(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    with session_factory() as db:
        result = validate_order(db, world.session_id, _req(world))
        assert result.unit.id == world.blue_unit_id
        assert isinstance(result.payload, MovePayload)


def test_unknown_session(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    with session_factory() as db, pytest.raises(SessionNotFoundError):
        validate_order(db, "no-such-session", _req(world))


def test_unit_not_in_session(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    with session_factory() as db, pytest.raises(OrderValidationError) as ei:
        validate_order(db, world.session_id, _req(world, unit_id="ghost"))
    assert ei.value.error_code == "ORDER_UNIT_NOT_FOUND"


def test_issuer_cannot_command_other_faction(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    with session_factory() as db, pytest.raises(OrderPermissionError):
        validate_order(db, world.session_id, _req(world, unit_id=world.red_unit_id))


def test_unknown_issuer(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    with session_factory() as db, pytest.raises(OrderPermissionError):
        validate_order(db, world.session_id, _req(world, issuer_id="nobody"))


def test_white_cell_can_command_any_faction(session_factory: sessionmaker[Session]) -> None:
    # 白軍/導演可對任一陣營單位下令（override 角色）
    world = seed_world(session_factory)
    with session_factory() as db:
        result = validate_order(
            db,
            world.session_id,
            _req(world, unit_id=world.red_unit_id, issuer_id=world.white_issuer_id),
        )
        assert result.unit.id == world.red_unit_id


def test_recon_payload_accepted_generic(session_factory: sessionmaker[Session]) -> None:
    # 非 MOVE/ENGAGE 類型的載荷目前以泛型 dict 通過（RECON/RESUPPLY/POSTURE，O3.x 細化）
    world = seed_world(session_factory)
    with session_factory() as db:
        result = validate_order(
            db,
            world.session_id,
            _req(world, order_type=OrderType.RECON, payload={"area": "north"}),
        )
        assert result.payload == {"area": "north"}


def test_bad_move_payload(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    with session_factory() as db, pytest.raises(OrderValidationError) as ei:
        validate_order(db, world.session_id, _req(world, payload={"wrong": "shape"}))
    assert ei.value.error_code == "ORDER_INVALID_PAYLOAD"
