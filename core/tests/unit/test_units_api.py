"""Units REST 端點（O4.5）：faction-scoped 單位列表。"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from _auth_fakes import TEST_SETTINGS
from _order_fakes import OrderWorld, order_token, seed_world
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.api.deps import get_db, get_settings
from app.main import app
from app.models import UserRole


@pytest.fixture(autouse=True)
def _clear() -> Iterator[None]:
    yield
    app.dependency_overrides.clear()


def _client(factory: sessionmaker[Session]) -> TestClient:
    def _db() -> Iterator[Session]:
        db = factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_settings] = lambda: TEST_SETTINGS
    return TestClient(app)


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _auth(world: OrderWorld, white: bool = False) -> dict[str, str]:
    if white:
        return _bearer(order_token(world.white_user_id, UserRole.WHITE_CELL_STAFF))
    return _bearer(order_token(world.cmdr_user_id))


def test_units_requires_auth(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    r = _client(session_factory).get(f"/api/v1/sessions/{world.session_id}/units")
    assert r.status_code == 401


def test_commander_sees_only_own_faction(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    client = _client(session_factory)
    r = client.get(f"/api/v1/sessions/{world.session_id}/units", headers=_auth(world))
    assert r.status_code == 200
    body = r.json()
    assert [u["id"] for u in body] == [world.blue_unit_id]  # 只見藍軍
    assert body[0]["faction"] == "BLUE"


def test_white_cell_sees_all(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    client = _client(session_factory)
    r = client.get(f"/api/v1/sessions/{world.session_id}/units", headers=_auth(world, white=True))
    ids = {u["id"] for u in r.json()}
    assert ids == {world.blue_unit_id, world.red_unit_id}  # 全知見雙方


def test_units_expose_is_fixed(session_factory: sessionmaker[Session]) -> None:
    # 固定單位旗標透出 UnitView（供 COP 顯示鎖定 + 前端擋 MOVE）。
    world = seed_world(session_factory)
    with session_factory() as db:
        from app.models import TacticalUnit

        unit = db.get(TacticalUnit, world.blue_unit_id)
        assert unit is not None
        unit.is_fixed = True
        db.commit()
    client = _client(session_factory)
    r = client.get(f"/api/v1/sessions/{world.session_id}/units", headers=_auth(world))
    body = {u["id"]: u for u in r.json()}
    assert body[world.blue_unit_id]["is_fixed"] is True


def test_non_participant_403(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    with session_factory() as db:  # 非參與者使用者
        from app.models import User

        outsider = User(username="outsider", password_hash="x", role=UserRole.COMMANDER)
        db.add(outsider)
        db.commit()
        oid = outsider.id
    r = _client(session_factory).get(
        f"/api/v1/sessions/{world.session_id}/units", headers=_bearer(order_token(oid))
    )
    assert r.status_code == 403


def test_white_cell_viewpoint_switch(session_factory: sessionmaker[Session]) -> None:
    """O7.4：White Cell 視角切換——as_faction=RED 只見紅、BLUE 只見藍、無參數見全部。"""
    world = seed_world(session_factory)
    client = _client(session_factory)
    base = f"/api/v1/sessions/{world.session_id}/units"

    red = client.get(base, params={"as_faction": "RED"}, headers=_auth(world, white=True))
    assert [u["id"] for u in red.json()] == [world.red_unit_id]

    blue = client.get(base, params={"as_faction": "BLUE"}, headers=_auth(world, white=True))
    assert [u["id"] for u in blue.json()] == [world.blue_unit_id]

    god = client.get(base, headers=_auth(world, white=True))
    assert {u["id"] for u in god.json()} == {world.blue_unit_id, world.red_unit_id}


def test_non_white_cell_cannot_switch_viewpoint(session_factory: sessionmaker[Session]) -> None:
    """一般角色不得以 as_faction 窺視他陣營（越權防護）。"""
    world = seed_world(session_factory)
    client = _client(session_factory)
    r = client.get(
        f"/api/v1/sessions/{world.session_id}/units",
        params={"as_faction": "RED"},
        headers=_auth(world),  # 藍方 COMMANDER
    )
    assert r.status_code == 403


def test_reposition_white_cell_ok(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    c = _client(session_factory)
    r = c.post(
        f"/api/v1/sessions/{world.session_id}/units/{world.blue_unit_id}/reposition",
        json={"lat": 24.5, "lng": 121.5},
        headers=_auth(world, white=True),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["lat"] == 24.5 and body["lng"] == 121.5
    from app.models import TacticalUnit

    with session_factory() as db:
        u = db.get(TacticalUnit, world.blue_unit_id)
        assert u is not None and u.current_lat == 24.5 and u.current_lng == 121.5


def test_reposition_commander_forbidden(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    c = _client(session_factory)
    r = c.post(
        f"/api/v1/sessions/{world.session_id}/units/{world.blue_unit_id}/reposition",
        json={"lat": 24.5, "lng": 121.5},
        headers=_auth(world),  # 一般 COMMANDER（非全知）
    )
    assert r.status_code == 403
