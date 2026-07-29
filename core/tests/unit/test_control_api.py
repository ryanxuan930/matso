"""White Cell 時間控制端點（O7.4 + WP-E1）：限 White Cell、動作驗證、ROLLBACK 接活。"""

from __future__ import annotations

import pytest
from _auth_fakes import auth_header, login, make_client, seed_user
from fakeredis import FakeStrictRedis
from sqlalchemy.orm import Session, sessionmaker

import app.api.control as control_mod
from app.api import install_error_handlers
from app.main import app
from app.models import WargameSession
from app.models.enums import UserRole
from app.sim_control import (
    session_concluded_key,
    session_pause_key,
    session_restart_key,
    session_rollback_key,
)
from app.state.checkpoint import CheckpointManager


@pytest.fixture(autouse=True)
def _handlers() -> None:
    install_error_handlers(app)


def _control(client, sid: str, token: str, body: dict):  # type: ignore[no-untyped-def]
    return client.post(f"/api/v1/sessions/{sid}/control", json=body, headers=auth_header(token))


def _seed_session(session_factory: sessionmaker[Session]) -> str:
    with session_factory() as db:
        ws = WargameSession(name="ctl", master_seed=1, current_weather={})
        db.add(ws)
        db.commit()
        return str(ws.id)


@pytest.fixture
def wired(
    session_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> tuple[object, FakeStrictRedis, str]:
    """已登入的白軍 client + 假 Redis + 一個真 session id。"""
    fake = FakeStrictRedis(decode_responses=True)
    monkeypatch.setattr(control_mod.redis, "from_url", lambda *a, **k: fake)
    monkeypatch.setattr(control_mod, "default_session_factory", lambda: session_factory)
    seed_user(session_factory, "wc", "pw", role=UserRole.EXERCISE_DIRECTOR)
    client = make_client(session_factory)
    token = login(client, "wc", "pw")["access_token"]
    yield client, fake, _seed_session(session_factory), token  # type: ignore[misc]
    app.dependency_overrides.clear()


def test_non_white_cell_forbidden(session_factory: sessionmaker[Session]) -> None:
    seed_user(session_factory, "player", "pw", role=UserRole.COMMANDER)
    client = make_client(session_factory)
    token = login(client, "player", "pw")["access_token"]
    assert _control(client, "s1", token, {"action": "PAUSE"}).status_code == 403
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
    assert _control(client, "s1", token, {"action": "NUKE"}).status_code == 403
    app.dependency_overrides.clear()


def test_rollback_requests_and_pauses(wired: tuple, session_factory: sessionmaker[Session]) -> None:
    """WP-E1：ROLLBACK 不再只是發事件——排入請求、暫停、要求 runner 重建、清收場旗標。"""
    client, fake, sid, token = wired
    CheckpointManager(session_factory).checkpoint(
        sid, tick=50, state={"u1": {"health": 100}}, ledger_seq=-1
    )
    fake.set(session_concluded_key(sid), "1")  # 假設這局已判定收場

    r = _control(client, sid, token, {"action": "ROLLBACK", "target_tick": 50})
    assert r.status_code == 201
    assert r.json()["rollback_requested_tick"] == 50
    assert fake.get(session_rollback_key(sid)) == "50"
    assert fake.exists(session_pause_key(sid))  # 先凍結才回滾
    assert fake.exists(session_restart_key(sid))  # runner 收工 → 掃描層重建 → 執行回滾
    assert not fake.exists(session_concluded_key(sid))  # 回到分勝負之前＝不再是收場
    assert "SESSION_CONTROL" in fake.lrange(f"session:{sid}:ring", 0, -1)[0]


def test_rollback_to_a_tick_without_a_checkpoint_is_404(wired: tuple) -> None:
    """挑了不存在的快照點：當場回錯，而不是排入一個永遠做不了的請求。"""
    client, fake, sid, token = wired
    r = _control(client, sid, token, {"action": "ROLLBACK", "target_tick": 999})
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "ROLLBACK_TARGET_NOT_FOUND"
    assert not fake.exists(session_rollback_key(sid))
    assert not fake.exists(session_pause_key(sid))  # 失敗就不該留下暫停的局


def test_rollback_without_target_tick_is_404(wired: tuple) -> None:
    client, _fake, sid, token = wired
    assert _control(client, sid, token, {"action": "ROLLBACK"}).status_code == 404


def test_pause_and_resume_toggle_the_flag(wired: tuple) -> None:
    client, fake, sid, token = wired
    _control(client, sid, token, {"action": "PAUSE"})
    assert fake.exists(session_pause_key(sid))
    _control(client, sid, token, {"action": "RESUME"})
    assert not fake.exists(session_pause_key(sid))


def test_checkpoint_list_is_white_cell_only(
    session_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(control_mod, "default_session_factory", lambda: session_factory)
    seed_user(session_factory, "player", "pw", role=UserRole.COMMANDER)
    client = make_client(session_factory)
    token = login(client, "player", "pw")["access_token"]
    r = client.get("/api/v1/sessions/s1/checkpoints", headers=auth_header(token))
    assert r.status_code == 403
    app.dependency_overrides.clear()


def test_checkpoint_list_orders_newest_first(
    wired: tuple, session_factory: sessionmaker[Session]
) -> None:
    """依 ledgerSeq 由新到舊——rollback 後 tick 非單調，用 tick 排序會給出錯的順序。"""
    client, _fake, sid, token = wired
    mgr = CheckpointManager(session_factory)
    mgr.checkpoint(sid, tick=900, state={"u1": {"health": 10}}, ledger_seq=5)
    mgr.checkpoint(sid, tick=100, state={"u1": {"health": 90}}, ledger_seq=99)
    body = client.get(f"/api/v1/sessions/{sid}/checkpoints", headers=auth_header(token)).json()
    assert [p["tick"] for p in body] == [100, 900]
    assert body[0]["ledger_seq"] == 99 and body[0]["state_hash"]
