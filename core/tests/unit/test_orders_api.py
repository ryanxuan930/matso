"""Order REST 端點測試（O3.1）：TestClient + dependency_overrides（SQLite + 假 gateway）。"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from _order_fakes import DownGateway, FakeGateway, OrderWorld, seed_world
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.api.deps import get_db, get_gateway
from app.main import app
from app.orders.precheck import PhysicsGateway


def _client(factory: sessionmaker[Session], gateway: PhysicsGateway) -> TestClient:
    def _db() -> Iterator[Session]:
        db = factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_gateway] = lambda: gateway
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clear_overrides() -> Iterator[None]:
    yield
    app.dependency_overrides.clear()


def _move_body(world: OrderWorld, **kw: object) -> dict[str, object]:
    body: dict[str, object] = {
        "unit_id": world.blue_unit_id,
        "order_type": "MOVE",
        "payload": {"to_h3": "8a2a1072b59ffff", "mobility_profile": "FOOT"},
        "issuer_id": world.blue_issuer_id,
    }
    body.update(kw)
    return body


def test_post_feasible_201_validated(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    client = _client(session_factory, FakeGateway(reachable=True))
    r = client.post(f"/api/v1/sessions/{world.session_id}/orders", json=_move_body(world))
    assert r.status_code == 201
    body = r.json()
    assert body["status"] == "VALIDATED"
    assert body["precheck"]["feasible"] is True


def test_post_infeasible_422_with_code(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    client = _client(session_factory, FakeGateway(reachable=False))
    r = client.post(f"/api/v1/sessions/{world.session_id}/orders", json=_move_body(world))
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["code"] == "ORDER_UNREACHABLE"
    assert "order_id" in err["details"]  # REJECTED order 已落庫


def test_post_unknown_session_404(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    client = _client(session_factory, FakeGateway())
    r = client.post("/api/v1/sessions/ghost/orders", json=_move_body(world))
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "SESSION_NOT_FOUND"


def test_post_bad_payload_422(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    client = _client(session_factory, FakeGateway())
    r = client.post(
        f"/api/v1/sessions/{world.session_id}/orders",
        json=_move_body(world, payload={"wrong": "shape"}),
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "ORDER_INVALID_PAYLOAD"


def test_post_terrain_down_503(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    client = _client(session_factory, DownGateway())
    r = client.post(f"/api/v1/sessions/{world.session_id}/orders", json=_move_body(world))
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "TERRAIN_UNAVAILABLE"


def test_delete_cancels(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    client = _client(session_factory, FakeGateway(reachable=True))
    created = client.post(
        f"/api/v1/sessions/{world.session_id}/orders", json=_move_body(world)
    ).json()
    r = client.delete(f"/api/v1/sessions/{world.session_id}/orders/{created['id']}")
    assert r.status_code == 200
    assert r.json()["status"] == "CANCELLED"


def test_delete_unknown_order_404(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    client = _client(session_factory, FakeGateway())
    r = client.delete(f"/api/v1/sessions/{world.session_id}/orders/nope")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "ORDER_NOT_FOUND"
