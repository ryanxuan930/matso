"""White Cell ad-hoc inject 端點（O7.2）：權限限 White Cell + 事件發佈。"""

from __future__ import annotations

import pytest
from _auth_fakes import auth_header, login, make_client, seed_user
from fakeredis import FakeStrictRedis
from sqlalchemy.orm import Session, sessionmaker

import app.api.inject as inject_mod
from app.api import install_error_handlers
from app.main import app
from app.models.enums import UserRole


@pytest.fixture(autouse=True)
def _handlers() -> None:
    install_error_handlers(app)


def _inject(client, token: str):  # type: ignore[no-untyped-def]
    return client.post(
        "/api/v1/sessions/s1/inject",
        json={"event_type": "BRIDGE_DESTROYED", "payload": {"hex": "8a11"}},
        headers=auth_header(token),
    )


def test_non_white_cell_forbidden(session_factory: sessionmaker[Session]) -> None:
    seed_user(session_factory, "player", "pw", role=UserRole.COMMANDER)
    client = make_client(session_factory)
    token = login(client, "player", "pw")["access_token"]
    r = _inject(client, token)
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "AUTH_FORBIDDEN"
    app.dependency_overrides.clear()


@pytest.mark.parametrize("role", [UserRole.EXERCISE_DIRECTOR, UserRole.WHITE_CELL_STAFF])
def test_white_cell_can_inject(
    session_factory: sessionmaker[Session], role: UserRole, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeStrictRedis(decode_responses=True)
    monkeypatch.setattr(inject_mod.redis, "from_url", lambda *a, **k: fake)
    seed_user(session_factory, "wc", "pw", role=role)
    client = make_client(session_factory)
    token = login(client, "wc", "pw")["access_token"]
    r = _inject(client, token)
    assert r.status_code == 201
    assert r.json()["seq"] == 1  # 首個事件 seq=1
    ring = fake.lrange("session:s1:ring", 0, -1)
    assert len(ring) == 1 and "BRIDGE_DESTROYED" in ring[0]
    app.dependency_overrides.clear()
