"""劇本管理端點（#5 後端）：get-one 載回 bundle + delete + RBAC（限統裁/管理）。"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator

import pytest
from _auth_fakes import TEST_SETTINGS
from _order_fakes import OrderWorld, order_token, seed_world
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.api.deps import get_db, get_settings
from app.main import app
from app.models import Scenario, UserRole


@pytest.fixture(autouse=True)
def _clear_overrides() -> Iterator[None]:
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


def _white(world: OrderWorld) -> dict[str, str]:
    tok = order_token(world.white_user_id, UserRole.WHITE_CELL_STAFF)
    return {"Authorization": f"Bearer {tok}"}


def _cmdr(world: OrderWorld) -> dict[str, str]:
    return {"Authorization": f"Bearer {order_token(world.cmdr_user_id, UserRole.COMMANDER)}"}


# 直接 seed 的 bundle（與 save_scenario 一致的序列化：sort_keys + utf-8）。
_BUNDLE = {
    "scenario": {"name": "測試想定", "version": "1.0.0"},
    "orbat": {"units": []},
    "msel": None,
}


def _seed_scenario(factory: sessionmaker[Session], created_by: str) -> str:
    blob = json.dumps(_BUNDLE, ensure_ascii=False, sort_keys=True).encode("utf-8")
    with factory() as db:
        row = Scenario(
            name="測試想定",
            version="1.0.0",
            package_blob=blob,
            checksum=hashlib.sha256(blob).hexdigest(),
            created_by=created_by,
        )
        db.add(row)
        db.commit()
        return row.id


def test_get_one_returns_bundle(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    sid = _seed_scenario(session_factory, world.white_user_id)
    r = _client(session_factory).get(f"/api/v1/scenarios/{sid}", headers=_white(world))
    assert r.status_code == 200, r.text
    assert r.json() == _BUNDLE


def test_delete_then_get_404(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    sid = _seed_scenario(session_factory, world.white_user_id)
    client = _client(session_factory)

    r = client.delete(f"/api/v1/scenarios/{sid}", headers=_white(world))
    assert r.status_code == 204, r.text

    r = client.get(f"/api/v1/scenarios/{sid}", headers=_white(world))
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "SCENARIO_NOT_FOUND"


def test_commander_forbidden(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    sid = _seed_scenario(session_factory, world.white_user_id)
    client = _client(session_factory)

    assert client.get(f"/api/v1/scenarios/{sid}", headers=_cmdr(world)).status_code == 403
    assert client.delete(f"/api/v1/scenarios/{sid}", headers=_cmdr(world)).status_code == 403
