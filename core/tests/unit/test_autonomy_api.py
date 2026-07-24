"""自主推演端點：指派存 Redis + 設 runner 重啟旗標（讓指派立即生效、不需新建 session）。"""

from __future__ import annotations

import pytest
from _auth_fakes import auth_header, login, make_client, seed_user
from fakeredis import FakeStrictRedis
from sqlalchemy.orm import Session, sessionmaker

import app.api.autonomy as autonomy_mod
from app.ai_loop.orchestrator import autonomy_config_key
from app.api import install_error_handlers
from app.main import app
from app.models.enums import UserRole
from app.sim_control import session_restart_key


@pytest.fixture(autouse=True)
def _handlers() -> None:
    install_error_handlers(app)


def _fake(monkeypatch: pytest.MonkeyPatch) -> FakeStrictRedis:
    fake = FakeStrictRedis(decode_responses=True)
    monkeypatch.setattr(autonomy_mod, "make_redis", lambda *a, **k: fake)
    autonomy_mod._redis.cache_clear()
    return fake


def test_put_autonomy_sets_config_and_restart(
    session_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _fake(monkeypatch)
    seed_user(session_factory, "wc", "pw", role=UserRole.WHITE_CELL_STAFF)
    client = make_client(session_factory)
    token = login(client, "wc", "pw")["access_token"]
    r = client.put(
        "/api/v1/sessions/s1/autonomy",
        json={"factions": {"BLUE": {"mission": "hold"}}, "heartbeat_s": 30},
        headers=auth_header(token),
    )
    assert r.status_code == 200
    assert r.json()["restarted"] is True
    assert fake.get(autonomy_config_key("s1"))  # 指派已存
    assert fake.get(session_restart_key("s1")) == "1"  # 重啟旗標已設（runner 數秒內重讀）
    app.dependency_overrides.clear()


def test_delete_autonomy_sets_restart(
    session_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _fake(monkeypatch)
    fake.set(autonomy_config_key("s1"), "{}")
    seed_user(session_factory, "wc", "pw", role=UserRole.WHITE_CELL_STAFF)
    client = make_client(session_factory)
    token = login(client, "wc", "pw")["access_token"]
    r = client.delete("/api/v1/sessions/s1/autonomy", headers=auth_header(token))
    assert r.status_code == 200
    assert fake.get(autonomy_config_key("s1")) is None
    assert fake.get(session_restart_key("s1")) == "1"
    app.dependency_overrides.clear()


def test_non_white_cell_forbidden(
    session_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake(monkeypatch)
    seed_user(session_factory, "p", "pw", role=UserRole.COMMANDER)
    client = make_client(session_factory)
    token = login(client, "p", "pw")["access_token"]
    r = client.put(
        "/api/v1/sessions/s1/autonomy", json={"factions": {}}, headers=auth_header(token)
    )
    assert r.status_code == 403
    app.dependency_overrides.clear()
