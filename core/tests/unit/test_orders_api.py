"""Order REST 端點測試（O3.1/O4.5）：TestClient + SQLite + 假 gateway + bearer auth。

issuer 由 token 推導（不再 body 帶 issuer_id）；非參與者 → 403。含 GET 列表。
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from _auth_fakes import TEST_SETTINGS
from _order_fakes import DownGateway, FakeGateway, OrderWorld, order_token, seed_world
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.api.deps import get_db, get_gateway, get_settings
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
    app.dependency_overrides[get_settings] = lambda: TEST_SETTINGS
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clear_overrides() -> Iterator[None]:
    yield
    app.dependency_overrides.clear()


def _auth(world: OrderWorld) -> dict[str, str]:
    return {"Authorization": f"Bearer {order_token(world.cmdr_user_id)}"}


def _move_body(world: OrderWorld, **kw: object) -> dict[str, object]:
    body: dict[str, object] = {
        "unit_id": world.blue_unit_id,
        "order_type": "MOVE",
        "payload": {"to_h3": "8a2a1072b59ffff", "mobility_profile": "FOOT"},
    }
    body.update(kw)
    return body


def test_post_requires_auth(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    client = _client(session_factory, FakeGateway(reachable=True))
    r = client.post(f"/api/v1/sessions/{world.session_id}/orders", json=_move_body(world))
    assert r.status_code == 401


def test_post_feasible_201_validated(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    client = _client(session_factory, FakeGateway(reachable=True))
    r = client.post(
        f"/api/v1/sessions/{world.session_id}/orders", json=_move_body(world), headers=_auth(world)
    )
    assert r.status_code == 201
    body = r.json()
    assert body["status"] == "VALIDATED"
    assert body["precheck"]["feasible"] is True


def test_post_infeasible_422_with_code(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    client = _client(session_factory, FakeGateway(reachable=False))
    r = client.post(
        f"/api/v1/sessions/{world.session_id}/orders", json=_move_body(world), headers=_auth(world)
    )
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["code"] == "ORDER_UNREACHABLE"
    assert "order_id" in err["details"]


def test_post_unknown_session_404(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    client = _client(session_factory, FakeGateway())
    # 非該（不存在）session 的參與者 → 403（權限先於 session 存在性）
    r = client.post("/api/v1/sessions/ghost/orders", json=_move_body(world), headers=_auth(world))
    assert r.status_code == 403


def test_post_bad_payload_422(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    client = _client(session_factory, FakeGateway())
    r = client.post(
        f"/api/v1/sessions/{world.session_id}/orders",
        json=_move_body(world, payload={"wrong": "shape"}),
        headers=_auth(world),
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "ORDER_INVALID_PAYLOAD"


def test_post_terrain_down_503(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    client = _client(session_factory, DownGateway())
    r = client.post(
        f"/api/v1/sessions/{world.session_id}/orders", json=_move_body(world), headers=_auth(world)
    )
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "TERRAIN_UNAVAILABLE"


def test_list_and_delete_flow(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    client = _client(session_factory, FakeGateway(reachable=True))
    created = client.post(
        f"/api/v1/sessions/{world.session_id}/orders", json=_move_body(world), headers=_auth(world)
    ).json()
    # GET 列表含此 order
    listing = client.get(f"/api/v1/sessions/{world.session_id}/orders", headers=_auth(world)).json()
    assert [o["id"] for o in listing] == [created["id"]]
    # DELETE 取消
    r = client.delete(
        f"/api/v1/sessions/{world.session_id}/orders/{created['id']}", headers=_auth(world)
    )
    assert r.status_code == 200
    assert r.json()["status"] == "CANCELLED"


def test_delete_unknown_order_404(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    client = _client(session_factory, FakeGateway())
    r = client.delete(f"/api/v1/sessions/{world.session_id}/orders/nope", headers=_auth(world))
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "ORDER_NOT_FOUND"
