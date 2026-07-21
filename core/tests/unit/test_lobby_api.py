"""Lobby REST 端點（O4.1）：GET/POST /sessions，認證 + 角色過濾。"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from _auth_fakes import auth_header, login, make_client, seed_user
from sqlalchemy.orm import Session, sessionmaker

from app.main import app
from app.models import UserRole


@pytest.fixture(autouse=True)
def _clear_overrides() -> Iterator[None]:
    yield
    app.dependency_overrides.clear()


def test_list_requires_auth(session_factory: sessionmaker[Session]) -> None:
    seed_user(session_factory)
    client = make_client(session_factory)
    assert client.get("/api/v1/sessions").status_code == 401


def test_list_empty_for_new_user(session_factory: sessionmaker[Session]) -> None:
    seed_user(session_factory)
    client = make_client(session_factory)
    tokens = login(client)
    r = client.get("/api/v1/sessions", headers=auth_header(tokens["access_token"]))
    assert r.status_code == 200
    assert r.json() == []


def test_create_then_appears_in_list(session_factory: sessionmaker[Session]) -> None:
    seed_user(session_factory)
    client = make_client(session_factory)
    tokens = login(client)
    h = auth_header(tokens["access_token"])
    created = client.post("/api/v1/sessions", json={"name": "演習一號"}, headers=h)
    assert created.status_code == 201
    body = created.json()
    assert body["name"] == "演習一號"
    assert body["status"] == "ACTIVE"
    assert body["my_faction"] == "WHITE_CELL"  # 建立者為統裁
    # 出現在列表
    listing = client.get("/api/v1/sessions", headers=h).json()
    assert [s["id"] for s in listing] == [body["id"]]


def test_create_requires_auth(session_factory: sessionmaker[Session]) -> None:
    seed_user(session_factory)
    client = make_client(session_factory)
    assert client.post("/api/v1/sessions", json={"name": "x"}).status_code == 401


def test_non_participant_commander_sees_only_own(session_factory: sessionmaker[Session]) -> None:
    # 指揮官甲建局 → 指揮官乙（非參與者）列表看不到
    seed_user(session_factory, username="alice", role=UserRole.COMMANDER)
    seed_user(session_factory, username="bob", role=UserRole.COMMANDER)
    client = make_client(session_factory)
    a = auth_header(login(client, "alice")["access_token"])
    client.post("/api/v1/sessions", json={"name": "alice 的局"}, headers=a)
    b = auth_header(login(client, "bob")["access_token"])
    assert client.get("/api/v1/sessions", headers=b).json() == []


def test_director_sees_all_sessions(session_factory: sessionmaker[Session]) -> None:
    # 統裁看得到別人建的局（即使非參與者）
    seed_user(session_factory, username="alice", role=UserRole.COMMANDER)
    seed_user(session_factory, username="chief", role=UserRole.EXERCISE_DIRECTOR)
    client = make_client(session_factory)
    a = auth_header(login(client, "alice")["access_token"])
    made = client.post("/api/v1/sessions", json={"name": "alice 的局"}, headers=a).json()
    chief = auth_header(login(client, "chief")["access_token"])
    listing = client.get("/api/v1/sessions", headers=chief).json()
    ids = [s["id"] for s in listing]
    assert made["id"] in ids
    assert listing[0]["my_faction"] is None  # chief 非參與者
