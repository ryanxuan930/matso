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
