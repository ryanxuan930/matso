"""White Cell 時間控制端點（O7.4）：限 White Cell + 動作驗證。"""

from __future__ import annotations

import pytest
from _auth_fakes import auth_header, login, make_client, seed_user
from fakeredis import FakeStrictRedis
from sqlalchemy.orm import Session, sessionmaker

import app.api.control as control_mod
from app.api import install_error_handlers
from app.main import app
from app.models.enums import UserRole


@pytest.fixture(autouse=True)
def _handlers() -> None:
    install_error_handlers(app)


def _control(client, token: str, body: dict):  # type: ignore[no-untyped-def]
    return client.post("/api/v1/sessions/s1/control", json=body, headers=auth_header(token))


def test_non_white_cell_forbidden(session_factory: sessionmaker[Session]) -> None:
    seed_user(session_factory, "player", "pw", role=UserRole.COMMANDER)
    client = make_client(session_factory)
    token = login(client, "player", "pw")["access_token"]
    r = _control(client, token, {"action": "PAUSE"})
    assert r.status_code == 403
    app.dependency_overrides.clear()


def test_white_cell_rollback(
    session_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeStrictRedis(decode_responses=True)
    monkeypatch.setattr(control_mod.redis, "from_url", lambda *a, **k: fake)
    seed_user(session_factory, "wc", "pw", role=UserRole.EXERCISE_DIRECTOR)
    client = make_client(session_factory)
    token = login(client, "wc", "pw")["access_token"]
    r = _control(client, token, {"action": "ROLLBACK", "target_tick": 50})
    assert r.status_code == 201 and r.json()["seq"] == 1
    ring = fake.lrange("session:s1:ring", 0, -1)
    assert "SESSION_CONTROL" in ring[0] and "ROLLBACK" in ring[0]
    app.dependency_overrides.clear()


def test_unknown_action_rejected(
    session_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        control_mod.redis, "from_url", lambda *a, **k: FakeStrictRedis(decode_responses=True)
    )
    seed_user(session_factory, "wc", "pw", role=UserRole.WHITE_CELL_STAFF)
    client = make_client(session_factory)
    token = login(client, "wc", "pw")["access_token"]
    r = _control(client, token, {"action": "NUKE"})
    assert r.status_code == 403
    app.dependency_overrides.clear()
