"""移動路徑預覽端點（#28）：距離/tick/可行性 + fog of war 阻礙 + 自訂 waypoints。"""

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


def _cmdr(world: OrderWorld) -> dict[str, str]:
    return {"Authorization": f"Bearer {order_token(world.cmdr_user_id, UserRole.COMMANDER)}"}


def _white(world: OrderWorld) -> dict[str, str]:
    tok = order_token(world.white_user_id, UserRole.WHITE_CELL_STAFF)
    return {"Authorization": f"Bearer {tok}"}


def _url(world: OrderWorld) -> str:
    return f"/api/v1/sessions/{world.session_id}/movement/preview"


def test_preview_clear_route(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    client = _client(session_factory)
    r = client.post(
        _url(world),
        json={"unit_id": world.blue_unit_id, "to_lat": 23.75, "to_lng": 121.30},
        headers=_cmdr(world),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["feasible"] is True and body["forced"] is False
    assert body["distance_m"] > 0 and body["duration_ticks"] > 0
    assert len(body["path"]) == 2 and body["crossings"] == []


def test_preview_forced_through_obstacle(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    client = _client(session_factory)
    # 白軍共同障礙擋在 121.25→121.30 路徑上。
    client.post(
        f"/api/v1/sessions/{world.session_id}/map-features",
        json={
            "kind": "OBSTACLE",
            "geometry_type": "POLYGON",
            "geometry": [
                [121.275, 23.745],
                [121.285, 23.745],
                [121.285, 23.755],
                [121.275, 23.755],
            ],
            "label": "雷區",
        },
        headers=_white(world),
    )
    r = client.post(
        _url(world),
        json={"unit_id": world.blue_unit_id, "to_lat": 23.75, "to_lng": 121.30},
        headers=_cmdr(world),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["forced"] is True and body["feasible"] is False
    assert len(body["crossings"]) == 1 and body["crossings"][0]["kind"] == "OBSTACLE"


def test_preview_custom_waypoints(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    client = _client(session_factory)
    r = client.post(
        _url(world),
        json={"unit_id": world.blue_unit_id, "waypoints": [[121.27, 23.77], [121.30, 23.75]]},
        headers=_cmdr(world),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    # 起點 + 2 waypoint = 3 點。
    assert len(body["path"]) == 3


def test_preview_missing_dest_400(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    client = _client(session_factory)
    r = client.post(_url(world), json={"unit_id": world.blue_unit_id}, headers=_cmdr(world))
    assert r.status_code >= 400
