"""Intel 查詢端點（O3.3 + O7.5 RBAC）：認證 + 自身陣營 + White Cell god/視角。"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from _auth_fakes import TEST_SETTINGS
from _order_fakes import OrderWorld, order_token, seed_world
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.api.deps import get_db, get_settings
from app.intel import store
from app.intel.sweep import Contact
from app.main import app
from app.models.enums import IntelFidelity, UserRole


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


def _hdr(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _cmdr(world: OrderWorld) -> dict[str, str]:
    return _hdr(order_token(world.cmdr_user_id, UserRole.COMMANDER))  # BLUE COMMANDER


def _white(world: OrderWorld) -> dict[str, str]:
    return _hdr(order_token(world.white_user_id, UserRole.WHITE_CELL_STAFF))


def _seed_red_contact(factory: sessionmaker[Session], world: OrderWorld) -> None:
    with factory() as db:
        store.record(
            db,
            world.session_id,
            Contact("RED", world.blue_unit_id, IntelFidelity.DETECTED, 3, 23.75, 121.25, 500.0),
        )
        db.commit()


def test_requires_auth(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    r = _client(session_factory).get(f"/api/v1/sessions/{world.session_id}/intel")
    assert r.status_code == 401


def test_commander_sees_own_faction_only(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    _seed_red_contact(session_factory, world)
    client = _client(session_factory)
    # BLUE COMMANDER → 自身（BLUE）情報：無 BLUE contact（RED 才看到藍軍）
    r = client.get(f"/api/v1/sessions/{world.session_id}/intel", headers=_cmdr(world))
    assert r.status_code == 200 and r.json() == []


def test_commander_cannot_view_other_faction(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    client = _client(session_factory)
    r = client.get(
        f"/api/v1/sessions/{world.session_id}/intel",
        params={"as_faction": "RED"},
        headers=_cmdr(world),
    )
    assert r.status_code == 403  # 一般角色不得窺視他陣營情報


def test_white_cell_god_view_and_viewpoint(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    _seed_red_contact(session_factory, world)
    client = _client(session_factory)
    base = f"/api/v1/sessions/{world.session_id}/intel"

    god = client.get(base, headers=_white(world))  # god view 見全部 contact
    assert god.status_code == 200 and len(god.json()) == 1
    assert "target_unit_id" not in god.json()[0]  # ground truth 永不下發

    red = client.get(base, params={"as_faction": "RED"}, headers=_white(world))
    assert len(red.json()) == 1  # RED 視角見自己偵測到的
    blue = client.get(base, params={"as_faction": "BLUE"}, headers=_white(world))
    assert blue.json() == []  # BLUE 視角無 contact
