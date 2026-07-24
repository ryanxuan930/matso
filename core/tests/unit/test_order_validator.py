"""Order 驗證測試（O3.1/O4.5，步驟 [1]）：單位存在性 / 權限 / 載荷語法。issuer 為獨立參數。"""

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
    }
    base.update(kw)
    return OrderRequest(**base)  # type: ignore[arg-type]


def test_valid_move_returns_unit_and_payload(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    with session_factory() as db:
        result = validate_order(db, world.session_id, _req(world), world.blue_issuer_id)
        assert result.unit.id == world.blue_unit_id
        assert isinstance(result.payload, MovePayload)


def test_unknown_session(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    with session_factory() as db, pytest.raises(SessionNotFoundError):
        validate_order(db, "no-such-session", _req(world), world.blue_issuer_id)


def test_unit_not_in_session(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    with session_factory() as db, pytest.raises(OrderValidationError) as ei:
        validate_order(db, world.session_id, _req(world, unit_id="ghost"), world.blue_issuer_id)
    assert ei.value.error_code == "ORDER_UNIT_NOT_FOUND"


def test_issuer_cannot_command_other_faction(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    with session_factory() as db, pytest.raises(OrderPermissionError):
        validate_order(
            db, world.session_id, _req(world, unit_id=world.red_unit_id), world.blue_issuer_id
        )


def test_unknown_issuer(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    with session_factory() as db, pytest.raises(OrderPermissionError):
        validate_order(db, world.session_id, _req(world), "nobody")


def test_white_cell_can_command_any_faction(session_factory: sessionmaker[Session]) -> None:
    # 白軍/導演可對任一陣營單位下令（override 角色）
    world = seed_world(session_factory)
    with session_factory() as db:
        result = validate_order(
            db, world.session_id, _req(world, unit_id=world.red_unit_id), world.white_issuer_id
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
            world.blue_issuer_id,
        )
        assert result.payload == {"area": "north"}


def test_bad_move_payload(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    with session_factory() as db, pytest.raises(OrderValidationError) as ei:
        validate_order(
            db, world.session_id, _req(world, payload={"wrong": "shape"}), world.blue_issuer_id
        )
    assert ei.value.error_code == "ORDER_INVALID_PAYLOAD"


def _make_fixed(session_factory: sessionmaker[Session], unit_id: str) -> None:
    from app.models.tables import TacticalUnit

    with session_factory() as db:
        unit = db.get(TacticalUnit, unit_id)
        assert unit is not None
        unit.is_fixed = True
        db.commit()


def test_fixed_unit_cannot_move(session_factory: sessionmaker[Session]) -> None:
    # 固定單位（指揮部等）：MOVE 令於驗證層被擋 → ORDER_UNIT_FIXED（不被派去移動）。
    world = seed_world(session_factory)
    _make_fixed(session_factory, world.blue_unit_id)
    with session_factory() as db, pytest.raises(OrderValidationError) as ei:
        validate_order(db, world.session_id, _req(world), world.blue_issuer_id)
    assert ei.value.error_code == "ORDER_UNIT_FIXED"


def test_white_cell_also_cannot_move_fixed_unit(session_factory: sessionmaker[Session]) -> None:
    # 規則與下令者無關：即使白軍/導演也不能對固定單位下移動令（防誤把指揮部派出去）。
    world = seed_world(session_factory)
    _make_fixed(session_factory, world.blue_unit_id)
    with session_factory() as db, pytest.raises(OrderValidationError) as ei:
        validate_order(db, world.session_id, _req(world), world.white_issuer_id)
    assert ei.value.error_code == "ORDER_UNIT_FIXED"


def _set_scope(session_factory: sessionmaker[Session], issuer_id: str, scope: list[str]) -> None:
    from app.models.tables import SessionParticipant

    with session_factory() as db:
        p = db.get(SessionParticipant, issuer_id)
        assert p is not None
        p.unit_scope = scope
        db.commit()


def test_unit_scope_blocks_out_of_scope(session_factory: sessionmaker[Session]) -> None:
    # 名冊限縮此帳號只指揮某些單位（不含 blue_unit）→ 對 blue_unit 下令被擋。
    world = seed_world(session_factory)
    _set_scope(session_factory, world.blue_issuer_id, ["some-other-unit"])
    with session_factory() as db, pytest.raises(OrderPermissionError):
        validate_order(db, world.session_id, _req(world), world.blue_issuer_id)


def test_unit_scope_allows_in_scope(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    _set_scope(session_factory, world.blue_issuer_id, [world.blue_unit_id])
    with session_factory() as db:
        result = validate_order(db, world.session_id, _req(world), world.blue_issuer_id)
        assert result.unit.id == world.blue_unit_id


def test_fixed_unit_can_still_engage(session_factory: sessionmaker[Session]) -> None:
    # 固定不等於非戰鬥：ENGAGE（原地自衛）不受固定限制，驗證通過（可行性另由物理預檢把關）。
    world = seed_world(session_factory)
    _make_fixed(session_factory, world.blue_unit_id)
    with session_factory() as db:
        result = validate_order(
            db,
            world.session_id,
            _req(
                world,
                order_type=OrderType.ENGAGE,
                payload={"target_unit_id": world.red_unit_id},
            ),
            world.blue_issuer_id,
        )
        assert result.order_type is OrderType.ENGAGE
