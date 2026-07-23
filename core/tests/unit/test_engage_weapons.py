"""ENGAGE 武器/彈種選擇（資料驅動 baseStats）：射程 / 彈種 / 武器實例 precheck + weapons 端點。

seed_world 藍(23.75,121.25)↔紅(23.76,121.26) 直線約 1507 m：
- RIFLE_556（max 600m）→ 超出射程
- AUTOCANNON_30（max 3000m, ammo AMMO_30MM）→ 在射程內
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from _auth_fakes import TEST_SETTINGS
from _order_fakes import FakeGateway, OrderWorld, order_token, seed_world
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.adjudication import ensure_weapon_templates
from app.api.deps import get_db, get_gateway, get_settings
from app.main import app
from app.models import UserRole
from app.models.tables import EquipmentInstance, TacticalUnit
from app.orders.precheck import precheck_error_code, run_precheck
from app.orders.schemas import EngagePayload, OrderType
from app.orders.validator import ValidatedOrder


@pytest.fixture(autouse=True)
def _clear_overrides() -> Iterator[None]:
    yield
    app.dependency_overrides.clear()


def _give_weapon(
    factory: sessionmaker[Session], unit_id: str, weapon_name: str, ammo: int = 100
) -> str:
    """配發一件武器 EquipmentInstance 給指定單位，回實例 id。"""
    with factory() as db:
        tids = ensure_weapon_templates(db)
        inst = EquipmentInstance(
            template_id=tids[weapon_name], owner_id=unit_id, current_state={"ammo": ammo}
        )
        db.add(inst)
        db.commit()
        return inst.id


# ---------------- precheck 層（skip / 射程 / 彈種 / 武器實例） ----------------


def _engage(db: Session, unit_id: str, target_id: str, **payload_kw: object) -> ValidatedOrder:
    unit = db.get(TacticalUnit, unit_id)
    assert unit is not None
    return ValidatedOrder(
        unit=unit,
        order_type=OrderType.ENGAGE,
        payload=EngagePayload(target_unit_id=target_id, **payload_kw),  # type: ignore[arg-type]
    )


def test_no_equipment_skips_weapon_checks(session_factory: sessionmaker[Session]) -> None:
    """單位無裝備 → 只有 line_of_sight 檢查（優雅降級，維持既有 ENGAGE 行為）。"""
    world = seed_world(session_factory)
    with session_factory() as db:
        result = run_precheck(
            db, _engage(db, world.blue_unit_id, world.red_unit_id), FakeGateway(visible=True)
        )
    assert result.feasible
    assert [c.name for c in result.checks] == ["line_of_sight"]


def test_in_range_valid_ammo_feasible(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    _give_weapon(session_factory, world.blue_unit_id, "AUTOCANNON_30")
    with session_factory() as db:
        result = run_precheck(
            db,
            _engage(db, world.blue_unit_id, world.red_unit_id, ammo_type="AMMO_30MM"),
            FakeGateway(visible=True),
        )
    assert result.feasible
    assert [c.name for c in result.checks] == ["line_of_sight", "range", "ammo"]


def test_out_of_range_infeasible(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    _give_weapon(session_factory, world.blue_unit_id, "RIFLE_556")  # 600m max < 1507m
    with session_factory() as db:
        result = run_precheck(
            db, _engage(db, world.blue_unit_id, world.red_unit_id), FakeGateway(visible=True)
        )
    assert not result.feasible
    assert precheck_error_code(result) == "ORDER_OUT_OF_RANGE"


def test_bad_ammo_infeasible(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    _give_weapon(session_factory, world.blue_unit_id, "AUTOCANNON_30")
    with session_factory() as db:
        result = run_precheck(
            db,
            _engage(db, world.blue_unit_id, world.red_unit_id, ammo_type="AMMO_9MM"),
            FakeGateway(visible=True),
        )
    assert not result.feasible
    assert precheck_error_code(result) == "ORDER_NO_AMMO"


def test_bogus_weapon_id_infeasible(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    _give_weapon(session_factory, world.blue_unit_id, "AUTOCANNON_30")
    with session_factory() as db:
        result = run_precheck(
            db,
            _engage(db, world.blue_unit_id, world.red_unit_id, weapon_id="ghost-weapon"),
            FakeGateway(visible=True),
        )
    assert not result.feasible
    assert precheck_error_code(result) == "ORDER_INVALID_PAYLOAD"


# ---------------- API 層（ENGAGE 下令 status / error code） ----------------


def _orders_client(factory: sessionmaker[Session]) -> TestClient:
    def _db() -> Iterator[Session]:
        db = factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_gateway] = lambda: FakeGateway(visible=True)
    app.dependency_overrides[get_settings] = lambda: TEST_SETTINGS
    return TestClient(app)


def _auth(world: OrderWorld) -> dict[str, str]:
    return {"Authorization": f"Bearer {order_token(world.cmdr_user_id)}"}


def _engage_body(world: OrderWorld, **payload_kw: object) -> dict[str, object]:
    return {
        "unit_id": world.blue_unit_id,
        "order_type": "ENGAGE",
        "payload": {"target_unit_id": world.red_unit_id, **payload_kw},
    }


def test_api_engage_in_range_201_validated(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    _give_weapon(session_factory, world.blue_unit_id, "AUTOCANNON_30")
    client = _orders_client(session_factory)
    r = client.post(
        f"/api/v1/sessions/{world.session_id}/orders",
        json=_engage_body(world, ammo_type="AMMO_30MM"),
        headers=_auth(world),
    )
    assert r.status_code == 201
    assert r.json()["status"] == "VALIDATED"


def test_api_engage_out_of_range_422(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    _give_weapon(session_factory, world.blue_unit_id, "RIFLE_556")
    client = _orders_client(session_factory)
    r = client.post(
        f"/api/v1/sessions/{world.session_id}/orders",
        json=_engage_body(world),
        headers=_auth(world),
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "ORDER_OUT_OF_RANGE"


def test_api_engage_bad_ammo_422(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    _give_weapon(session_factory, world.blue_unit_id, "AUTOCANNON_30")
    client = _orders_client(session_factory)
    r = client.post(
        f"/api/v1/sessions/{world.session_id}/orders",
        json=_engage_body(world, ammo_type="AMMO_9MM"),
        headers=_auth(world),
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "ORDER_NO_AMMO"


def test_api_engage_bogus_weapon_422(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    _give_weapon(session_factory, world.blue_unit_id, "AUTOCANNON_30")
    client = _orders_client(session_factory)
    r = client.post(
        f"/api/v1/sessions/{world.session_id}/orders",
        json=_engage_body(world, weapon_id="ghost-weapon"),
        headers=_auth(world),
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "ORDER_INVALID_PAYLOAD"


# ---------------- GET weapons（fog of war：己方 200 / 他方 403） ----------------


def _units_client(factory: sessionmaker[Session]) -> TestClient:
    def _db() -> Iterator[Session]:
        db = factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_settings] = lambda: TEST_SETTINGS
    return TestClient(app)


def test_get_weapons_own_faction_200(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    _give_weapon(session_factory, world.blue_unit_id, "AUTOCANNON_30")
    client = _units_client(session_factory)
    r = client.get(
        f"/api/v1/sessions/{world.session_id}/units/{world.blue_unit_id}/weapons",
        headers=_auth(world),
    )
    assert r.status_code == 200
    body = r.json()
    assert [w["name"] for w in body] == ["AUTOCANNON_30"]
    assert body[0]["ammo_types"] == ["AMMO_30MM"]
    assert body[0]["ammo_remaining"] == 100


def test_get_weapons_enemy_faction_403(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    _give_weapon(session_factory, world.red_unit_id, "AUTOCANNON_30")
    client = _units_client(session_factory)
    r = client.get(
        f"/api/v1/sessions/{world.session_id}/units/{world.red_unit_id}/weapons",
        headers=_auth(world),  # 藍方 COMMANDER 窺視紅方裝備 → 403
    )
    assert r.status_code == 403


def test_get_weapons_white_cell_sees_any(session_factory: sessionmaker[Session]) -> None:
    """全知（白軍）可查任一單位裝備。"""
    world = seed_world(session_factory)
    _give_weapon(session_factory, world.red_unit_id, "ATGM")
    client = _units_client(session_factory)
    token = order_token(world.white_user_id, UserRole.WHITE_CELL_STAFF)
    headers = {"Authorization": f"Bearer {token}"}
    r = client.get(
        f"/api/v1/sessions/{world.session_id}/units/{world.red_unit_id}/weapons",
        headers=headers,
    )
    assert r.status_code == 200
    assert [w["name"] for w in r.json()] == ["ATGM"]
