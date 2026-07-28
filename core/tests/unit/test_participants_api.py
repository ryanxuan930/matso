"""參與者名冊 REST 端點：指派帳號×陣營×角色（決定操控/查看範圍）。限統裁/白軍/管理。"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from _auth_fakes import TEST_SETTINGS
from _order_fakes import OrderWorld, order_token, seed_world
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.api.deps import get_db, get_settings
from app.main import app
from app.models import User, UserRole


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


def _director(world: OrderWorld) -> dict[str, str]:
    # 白軍 = 全知 → 可管理名冊
    return {
        "Authorization": f"Bearer {order_token(world.white_user_id, UserRole.WHITE_CELL_STAFF)}"
    }


def _commander(world: OrderWorld) -> dict[str, str]:
    return {"Authorization": f"Bearer {order_token(world.cmdr_user_id, UserRole.COMMANDER)}"}


def _make_user(factory: sessionmaker[Session], username: str, role: UserRole) -> str:
    with factory() as db:
        u = User(username=username, password_hash="x", role=role)
        db.add(u)
        db.commit()
        return u.id


def _base(world: OrderWorld) -> str:
    return f"/api/v1/sessions/{world.session_id}/participants"


def test_list_roster_and_factions(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    client = _client(session_factory)
    r = client.get(_base(world), headers=_director(world))
    assert r.status_code == 200
    body = r.json()
    assert set(body["factions"]) >= {"BLUE", "RED", "WHITE_CELL"}  # 單位陣營 + WHITE_CELL
    assert world.cmdr_user_id in {p["user_id"] for p in body["participants"]}


def test_commander_cannot_manage_roster(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    client = _client(session_factory)
    r = client.get(_base(world), headers=_commander(world))
    assert r.status_code == 403


def test_assign_update_and_remove(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    uid = _make_user(session_factory, "player2", UserRole.COMMANDER)
    client = _client(session_factory)
    # 指派 player2 為 RED 指揮官
    r = client.put(
        f"{_base(world)}/{uid}",
        json={"faction": "RED", "role": "COMMANDER"},
        headers=_director(world),
    )
    assert r.status_code == 200
    assert r.json()["faction"] == "RED"
    assert r.json()["username"] == "player2"
    # upsert：改為 STAFF（同一 userId 不重複建立）
    r = client.put(
        f"{_base(world)}/{uid}", json={"faction": "RED", "role": "STAFF"}, headers=_director(world)
    )
    assert r.status_code == 200
    assert r.json()["role"] == "STAFF"
    # 名冊含 player2
    roster = client.get(_base(world), headers=_director(world)).json()["participants"]
    assert uid in {p["user_id"] for p in roster}
    # 移除
    r = client.delete(f"{_base(world)}/{uid}", headers=_director(world))
    assert r.status_code == 204


def test_assign_faction_not_in_session_rejected(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    uid = _make_user(session_factory, "player3", UserRole.COMMANDER)
    client = _client(session_factory)
    r = client.put(
        f"{_base(world)}/{uid}",
        json={"faction": "GREEN", "role": "COMMANDER"},  # GREEN 非本局陣營
        headers=_director(world),
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "FACTION_INVALID"


def test_assign_unknown_user_404(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    client = _client(session_factory)
    r = client.put(
        f"{_base(world)}/ghost",
        json={"faction": "BLUE", "role": "COMMANDER"},
        headers=_director(world),
    )
    assert r.status_code == 404


def test_assign_white_cell_faction_ok(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    uid = _make_user(session_factory, "analyst1", UserRole.ANALYST)
    client = _client(session_factory)
    r = client.put(
        f"{_base(world)}/{uid}",
        json={"faction": "WHITE_CELL", "role": "ANALYST"},
        headers=_director(world),
    )
    assert r.status_code == 200
    assert r.json()["faction"] == "WHITE_CELL"


def test_roster_includes_units(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    client = _client(session_factory)
    body = client.get(_base(world), headers=_director(world)).json()
    assert {u["designation"] for u in body["units"]} >= {"B1", "R1"}


def test_assign_with_unit_scope_roundtrip(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    uid = _make_user(session_factory, "scoped", UserRole.COMMANDER)
    client = _client(session_factory)
    r = client.put(
        f"{_base(world)}/{uid}",
        json={"faction": "BLUE", "role": "COMMANDER", "unit_scope": [world.blue_unit_id]},
        headers=_director(world),
    )
    assert r.status_code == 200
    assert r.json()["unit_scope"] == [world.blue_unit_id]


def test_unit_scope_rejects_foreign_unit(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    uid = _make_user(session_factory, "scoped2", UserRole.COMMANDER)
    client = _client(session_factory)
    # RED 單位不能放進 BLUE 指揮官的 scope
    r = client.put(
        f"{_base(world)}/{uid}",
        json={"faction": "BLUE", "role": "COMMANDER", "unit_scope": [world.red_unit_id]},
        headers=_director(world),
    )
    assert r.status_code == 422


def test_cannot_remove_last_director(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    uid = _make_user(session_factory, "director2", UserRole.EXERCISE_DIRECTOR)
    client = _client(session_factory)
    client.put(
        f"{_base(world)}/{uid}",
        json={"faction": "WHITE_CELL", "role": "EXERCISE_DIRECTOR"},
        headers=_director(world),
    )
    # uid 為唯一 EXERCISE_DIRECTOR 參與者 → 移除被擋
    r = client.delete(f"{_base(world)}/{uid}", headers=_director(world))
    assert r.status_code == 403
