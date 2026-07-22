"""地圖標註/工事（MapFeature）CRUD（stage ③）：建立/列/改/刪 + fog of war + 陣營編修權。"""

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


def _white(world: OrderWorld) -> dict[str, str]:
    tok = order_token(world.white_user_id, UserRole.WHITE_CELL_STAFF)
    return {"Authorization": f"Bearer {tok}"}


def _cmdr(world: OrderWorld) -> dict[str, str]:
    return {"Authorization": f"Bearer {order_token(world.cmdr_user_id, UserRole.COMMANDER)}"}


def _base(world: OrderWorld) -> str:
    return f"/api/v1/sessions/{world.session_id}/map-features"


_POINT = {"kind": "WEAPON_EMPLACEMENT", "geometry_type": "POINT", "geometry": [121.3, 23.8]}


def test_white_creates_common_visible_to_commander(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    client = _client(session_factory)
    # 白軍建共同（WHITE_CELL）武器據點
    r = client.post(_base(world), json={**_POINT, "label": "SAM-1"}, headers=_white(world))
    assert r.status_code == 201, r.text
    assert r.json()["owner_faction"] == "WHITE_CELL"
    # 藍軍指揮官看得到共同標註
    r = client.get(_base(world), headers=_cmdr(world))
    assert r.status_code == 200
    assert any(f["label"] == "SAM-1" for f in r.json())


def test_commander_creates_own_and_cannot_forge_faction(
    session_factory: sessionmaker[Session],
) -> None:
    world = seed_world(session_factory)
    client = _client(session_factory)
    # 指揮官建本軍標註（owner_faction 省略→本軍 BLUE）
    r = client.post(_base(world), json={**_POINT, "kind": "OBSTACLE"}, headers=_cmdr(world))
    assert r.status_code == 201
    assert r.json()["owner_faction"] == "BLUE"
    # 冒用他軍陣營 → 403
    r = client.post(_base(world), json={**_POINT, "owner_faction": "RED"}, headers=_cmdr(world))
    assert r.status_code == 403


def test_fog_of_war_hides_enemy_annotations(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    client = _client(session_factory)
    # 白軍替 RED 建一個標註
    body = {**_POINT, "owner_faction": "RED", "label": "RED-OP"}
    client.post(_base(world), json=body, headers=_white(world))
    # 藍軍指揮官不應看到 RED 的標註
    r = client.get(_base(world), headers=_cmdr(world))
    assert all(f["label"] != "RED-OP" for f in r.json())


def test_edit_delete_permission(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    client = _client(session_factory)
    # 白軍建共同標註
    fid = client.post(_base(world), json=_POINT, headers=_white(world)).json()["id"]
    # 藍軍不可編共同/他方標註
    r = client.patch(f"{_base(world)}/{fid}", json={"label": "hack"}, headers=_cmdr(world))
    assert r.status_code == 403
    # 白軍可編
    r = client.patch(f"{_base(world)}/{fid}", json={"label": "moved"}, headers=_white(world))
    assert r.status_code == 200 and r.json()["label"] == "moved"
    # 白軍可刪
    assert client.delete(f"{_base(world)}/{fid}", headers=_white(world)).status_code == 204


def test_bad_geometry_type_422(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    client = _client(session_factory)
    r = client.post(
        _base(world),
        json={"kind": "OBSTACLE", "geometry_type": "BLOB", "geometry": []},
        headers=_white(world),
    )
    assert r.status_code == 422
