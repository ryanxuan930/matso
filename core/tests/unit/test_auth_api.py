"""Auth REST 端點（O4.1）：login/refresh/me/logout，TestClient + SQLite。

驗收要點：登入成功回 token 對、**錯誤密碼被拒（401）**、refresh 換發、bearer 保護。
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from _auth_fakes import auth_header, login, make_client, seed_user
from sqlalchemy.orm import Session, sessionmaker

from app.main import app


@pytest.fixture(autouse=True)
def _clear_overrides() -> Iterator[None]:
    yield
    app.dependency_overrides.clear()


def test_login_success_returns_token_pair(session_factory: sessionmaker[Session]) -> None:
    seed_user(session_factory)
    client = make_client(session_factory)
    r = client.post("/api/v1/auth/login", json={"username": "cmdr", "password": "pw123"})
    assert r.status_code == 200
    body = r.json()
    assert body["access_token"] and body["refresh_token"]
    assert body["token_type"] == "bearer"
    assert body["expires_in"] > 0


def test_login_wrong_password_401(session_factory: sessionmaker[Session]) -> None:
    seed_user(session_factory)
    client = make_client(session_factory)
    r = client.post("/api/v1/auth/login", json={"username": "cmdr", "password": "nope"})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "AUTH_INVALID_CREDENTIALS"


def test_login_unknown_user_401(session_factory: sessionmaker[Session]) -> None:
    seed_user(session_factory)
    client = make_client(session_factory)
    r = client.post("/api/v1/auth/login", json={"username": "ghost", "password": "x"})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "AUTH_INVALID_CREDENTIALS"


def test_me_requires_bearer(session_factory: sessionmaker[Session]) -> None:
    seed_user(session_factory)
    client = make_client(session_factory)
    assert client.get("/api/v1/auth/me").status_code == 401


def test_me_returns_current_user(session_factory: sessionmaker[Session]) -> None:
    seed_user(session_factory)
    client = make_client(session_factory)
    tokens = login(client)
    r = client.get("/api/v1/auth/me", headers=auth_header(tokens["access_token"]))
    assert r.status_code == 200
    assert r.json()["username"] == "cmdr"
    assert r.json()["role"] == "COMMANDER"


def test_refresh_returns_new_access(session_factory: sessionmaker[Session]) -> None:
    seed_user(session_factory)
    client = make_client(session_factory)
    tokens = login(client)
    r = client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert r.status_code == 200
    assert r.json()["access_token"]
    # 新 access 可用於受保護端點
    me = client.get("/api/v1/auth/me", headers=auth_header(r.json()["access_token"]))
    assert me.status_code == 200


def test_refresh_rejects_access_token_401(session_factory: sessionmaker[Session]) -> None:
    seed_user(session_factory)
    client = make_client(session_factory)
    tokens = login(client)
    r = client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["access_token"]})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "AUTH_INVALID_TOKEN"


def test_logout_requires_auth_then_204(session_factory: sessionmaker[Session]) -> None:
    seed_user(session_factory)
    client = make_client(session_factory)
    assert client.post("/api/v1/auth/logout").status_code == 401
    tokens = login(client)
    r = client.post("/api/v1/auth/logout", headers=auth_header(tokens["access_token"]))
    assert r.status_code == 204


def test_invalid_bearer_rejected(session_factory: sessionmaker[Session]) -> None:
    seed_user(session_factory)
    client = make_client(session_factory)
    r = client.get("/api/v1/auth/me", headers=auth_header("garbage.token.here"))
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "AUTH_INVALID_TOKEN"
