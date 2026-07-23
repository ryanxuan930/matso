"""帳號管理端點（#32）：白軍/管理建立/改角色/重設密碼/刪除 + 權限與防呆。"""

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


def _admin_header(factory: sessionmaker[Session], client) -> dict[str, str]:  # type: ignore[no-untyped-def]
    seed_user(factory, username="chief", role=UserRole.WHITE_CELL_STAFF)
    return auth_header(login(client, "chief")["access_token"])


def test_non_admin_cannot_list(session_factory: sessionmaker[Session]) -> None:
    seed_user(session_factory, username="joe", role=UserRole.COMMANDER)
    client = make_client(session_factory)
    h = auth_header(login(client, "joe")["access_token"])
    assert client.get("/api/v1/users", headers=h).status_code == 403


def test_admin_create_list_update_delete(session_factory: sessionmaker[Session]) -> None:
    client = make_client(session_factory)
    h = _admin_header(session_factory, client)

    # 建立
    r = client.post(
        "/api/v1/users",
        json={"username": "newbie", "password": "s3cretpass", "role": "COMMANDER"},
        headers=h,
    )
    assert r.status_code == 201, r.text
    uid = r.json()["id"]
    assert r.json()["role"] == "COMMANDER"

    # 列表含新帳號
    listing = client.get("/api/v1/users", headers=h).json()
    assert any(u["id"] == uid for u in listing)

    # 新帳號可用新密碼登入
    assert login(client, "newbie", "s3cretpass")["access_token"]

    # 改角色 + 重設密碼
    r = client.patch(
        f"/api/v1/users/{uid}", json={"role": "STAFF", "password": "brandnew1"}, headers=h
    )
    assert r.status_code == 200 and r.json()["role"] == "STAFF"
    assert login(client, "newbie", "brandnew1")["access_token"]

    # 刪除
    assert client.delete(f"/api/v1/users/{uid}", headers=h).status_code == 204
    assert all(u["id"] != uid for u in client.get("/api/v1/users", headers=h).json())


def test_duplicate_username_409(session_factory: sessionmaker[Session]) -> None:
    client = make_client(session_factory)
    h = _admin_header(session_factory, client)
    body = {"username": "dupe", "password": "password1", "role": "OBSERVER"}
    assert client.post("/api/v1/users", json=body, headers=h).status_code == 201
    assert client.post("/api/v1/users", json=body, headers=h).status_code == 409


def test_cannot_delete_self(session_factory: sessionmaker[Session]) -> None:
    client = make_client(session_factory)
    seed_user(session_factory, username="chief", role=UserRole.WHITE_CELL_STAFF)
    h = auth_header(login(client, "chief")["access_token"])
    me = client.get("/api/v1/auth/me", headers=h).json()
    assert client.delete(f"/api/v1/users/{me['id']}", headers=h).status_code == 409


def test_short_password_422(session_factory: sessionmaker[Session]) -> None:
    client = make_client(session_factory)
    h = _admin_header(session_factory, client)
    r = client.post(
        "/api/v1/users", json={"username": "x", "password": "short", "role": "OBSERVER"}, headers=h
    )
    assert r.status_code == 422  # password minLength 8
