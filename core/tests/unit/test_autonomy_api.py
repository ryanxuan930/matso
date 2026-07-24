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


def test_delete_autonomy_clears_ai_status(
    session_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    """#79：清除指派同時清除 AI 狀態遙測（無 AI 即無狀態）。"""
    from app.ai_loop.orchestrator import ai_status_key

    fake = _fake(monkeypatch)
    fake.set(autonomy_config_key("s1"), "{}")
    fake.hset(ai_status_key("s1"), "BLUE", "{}")
    seed_user(session_factory, "wc", "pw", role=UserRole.WHITE_CELL_STAFF)
    client = make_client(session_factory)
    token = login(client, "wc", "pw")["access_token"]
    client.delete("/api/v1/sessions/s1/autonomy", headers=auth_header(token))
    assert not fake.hgetall(ai_status_key("s1"))
    app.dependency_overrides.clear()


def test_ai_status_countdown_for_omniscient(
    session_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    """#79：全知者見全部陣營 AI 狀態；idle 帶下一次決策倒數、thinking 標示中。"""
    import json
    import time

    from app.ai_loop.orchestrator import ai_status_key

    fake = _fake(monkeypatch)
    seed_user(session_factory, "wc", "pw", role=UserRole.WHITE_CELL_STAFF)
    client = make_client(session_factory)
    token = login(client, "wc", "pw")["access_token"]
    now = time.time()
    fake.hset(
        ai_status_key("s1"),
        "BLUE",
        json.dumps(
            {"state": "idle", "last_decision_ts": now, "heartbeat_s": 45.0, "last_submitted": 2}
        ),
    )
    fake.hset(
        ai_status_key("s1"),
        "RED",
        json.dumps({"state": "thinking", "thinking_since": now, "heartbeat_s": 45.0}),
    )
    r = client.get("/api/v1/sessions/s1/ai-status", headers=auth_header(token))
    assert r.status_code == 200, r.text
    facs = {f["faction"]: f for f in r.json()["factions"]}
    assert set(facs) == {"BLUE", "RED"}
    assert facs["BLUE"]["state"] == "idle"
    assert facs["BLUE"]["seconds_until_next"] is not None
    assert 0 <= facs["BLUE"]["seconds_until_next"] <= 45
    assert facs["RED"]["state"] == "thinking"
    app.dependency_overrides.clear()


def test_ai_status_faction_scoped_for_commander(
    session_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    """#79 fog：一般指揮官只見己方 AI 狀態，敵方陣營被過濾。"""
    import json
    import time

    from sqlalchemy import select

    from app.ai_loop.orchestrator import ai_status_key
    from app.models import SessionParticipant, User

    fake = _fake(monkeypatch)
    seed_user(session_factory, "cmd", "pw", role=UserRole.COMMANDER)
    client = make_client(session_factory)
    token = login(client, "cmd", "pw")["access_token"]
    with session_factory() as db:
        uid = db.execute(select(User.id).where(User.username == "cmd")).scalar_one()
        db.add(
            SessionParticipant(
                user_id=uid, session_id="s1", faction="BLUE", role=UserRole.COMMANDER, unit_scope=[]
            )
        )
        db.commit()
    now = time.time()
    for fac in ("BLUE", "RED"):
        fake.hset(
            ai_status_key("s1"),
            fac,
            json.dumps({"state": "idle", "last_decision_ts": now, "heartbeat_s": 45.0}),
        )
    r = client.get("/api/v1/sessions/s1/ai-status", headers=auth_header(token))
    assert r.status_code == 200
    assert [f["faction"] for f in r.json()["factions"]] == ["BLUE"]
    app.dependency_overrides.clear()
