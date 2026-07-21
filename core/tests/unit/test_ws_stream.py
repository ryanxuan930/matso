"""WS 串流端點（O4.3）：TestClient + fakeredis（不需 compose，CI python job 常駐）。

驗收：斷線重連補齊缺漏事件；缺口過大 → RESYNC_REQUIRED；token 認證 + faction 過濾。
"""

from __future__ import annotations

import json
from collections.abc import Iterator

import pytest
from _auth_fakes import TEST_SETTINGS, seed_user
from fakeredis import FakeAsyncRedis, FakeServer, FakeStrictRedis
from sqlalchemy.orm import Session, sessionmaker
from starlette.websockets import WebSocketDisconnect

from app.api.deps import get_db, get_settings
from app.auth.tokens import JwtCodec, TokenType
from app.main import app
from app.models import SessionParticipant, UserRole

_SID = "sess-1"


@pytest.fixture(autouse=True)
def _clear_overrides() -> Iterator[None]:
    yield
    app.dependency_overrides.clear()


@pytest.fixture
def fake_ring(monkeypatch: pytest.MonkeyPatch) -> FakeStrictRedis:
    """共用 FakeServer：async 給 WS handler，sync 回傳供測試預填 ring / publish。"""
    server = FakeServer()
    monkeypatch.setattr(
        "app.api.ws.aioredis.from_url",
        lambda *_a, **_k: FakeAsyncRedis(server=server, decode_responses=True),
    )
    return FakeStrictRedis(server=server, decode_responses=True)


def _token(user_id: str, role: UserRole = UserRole.COMMANDER) -> str:
    return JwtCodec(secret=TEST_SETTINGS.jwt_secret).issue(
        user_id, role.value, TokenType.ACCESS, 900
    )


def _client(factory: sessionmaker[Session]):  # type: ignore[no-untyped-def]
    def _db() -> Iterator[Session]:
        db = factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_settings] = lambda: TEST_SETTINGS
    from fastapi.testclient import TestClient

    return TestClient(app)


def _seed_participant(
    factory: sessionmaker[Session],
    user_id: str,
    faction: str = "BLUE",
    role: UserRole = UserRole.COMMANDER,
) -> None:
    db = factory()
    db.add(
        SessionParticipant(
            user_id=user_id, session_id=_SID, faction=faction, role=role, unit_scope=[]
        )
    )
    db.commit()
    db.close()


def _env(seq: int, faction: str | None = None) -> dict:
    e: dict = {"v": 1, "seq": seq, "tick": seq * 10, "type": "STATE_DIFF", "payload": {}}
    if faction is not None:
        e["faction"] = faction
    return e


def _fill_ring(ring: FakeStrictRedis, seqs: list[int]) -> None:
    for s in seqs:
        ring.rpush(f"session:{_SID}:ring", json.dumps(_env(s)))


def _url(token: str) -> str:
    return f"/api/v1/sessions/{_SID}/stream?token={token}"


def test_reconnect_backfills_missed_events(
    session_factory: sessionmaker[Session], fake_ring: FakeStrictRedis
) -> None:
    uid = seed_user(session_factory)
    _seed_participant(session_factory, uid)
    _fill_ring(fake_ring, [1, 2, 3, 4, 5])
    client = _client(session_factory)

    with client.websocket_connect(_url(_token(uid))) as ws:
        ws.send_json({"last_seq": 2})
        welcome = ws.receive_json()
        assert welcome["type"] == "WELCOME"
        assert welcome["payload"]["resumed_from_seq"] == 2
        assert welcome["payload"]["faction"] == "BLUE"
        # 補送缺漏 seq 3,4,5
        assert [ws.receive_json()["seq"] for _ in range(3)] == [3, 4, 5]


def test_gap_too_large_triggers_resync(
    session_factory: sessionmaker[Session], fake_ring: FakeStrictRedis
) -> None:
    uid = seed_user(session_factory)
    _seed_participant(session_factory, uid)
    _fill_ring(fake_ring, [10, 11, 12, 13, 14, 15])  # 最舊=10
    client = _client(session_factory)

    with client.websocket_connect(_url(_token(uid))) as ws:
        ws.send_json({"last_seq": 2})  # 遠低於 ring_min-1 → 缺口
        assert ws.receive_json()["type"] == "RESYNC_REQUIRED"
        assert ws.receive_json()["type"] == "WELCOME"


def test_fresh_client_gets_welcome(
    session_factory: sessionmaker[Session], fake_ring: FakeStrictRedis
) -> None:
    uid = seed_user(session_factory)
    _seed_participant(session_factory, uid)
    client = _client(session_factory)
    with client.websocket_connect(_url(_token(uid))) as ws:
        ws.send_json({"last_seq": None})
        w = ws.receive_json()
        assert w["type"] == "WELCOME" and w["payload"]["resumed_from_seq"] == 0


def test_missing_token_rejected(
    session_factory: sessionmaker[Session], fake_ring: FakeStrictRedis
) -> None:
    seed_user(session_factory)
    client = _client(session_factory)
    # 未帶 token → close 前即斷
    with pytest.raises(WebSocketDisconnect), client.websocket_connect(_url("")) as ws:
        ws.receive_json()


def test_non_participant_non_omniscient_rejected(
    session_factory: sessionmaker[Session], fake_ring: FakeStrictRedis
) -> None:
    uid = seed_user(session_factory)  # 未加入此 session、COMMANDER（非全知）
    client = _client(session_factory)
    with pytest.raises(WebSocketDisconnect), client.websocket_connect(_url(_token(uid))) as ws:
        ws.receive_json()


def test_omniscient_non_participant_allowed(
    session_factory: sessionmaker[Session], fake_ring: FakeStrictRedis
) -> None:
    uid = seed_user(session_factory, username="chief", role=UserRole.EXERCISE_DIRECTOR)
    client = _client(session_factory)
    with client.websocket_connect(_url(_token(uid, UserRole.EXERCISE_DIRECTOR))) as ws:
        ws.send_json({"last_seq": None})
        w = ws.receive_json()
        assert w["type"] == "WELCOME" and w["payload"]["faction"] == "WHITE_CELL"


def test_faction_filter_drops_other_faction_backfill(
    session_factory: sessionmaker[Session], fake_ring: FakeStrictRedis
) -> None:
    uid = seed_user(session_factory)
    _seed_participant(session_factory, uid, faction="BLUE")
    # seq1 無受眾（全體）、seq2 RED（應被 BLUE client 濾掉）、seq3 BLUE
    ring_key = f"session:{_SID}:ring"
    fake_ring.rpush(ring_key, json.dumps(_env(1)))
    fake_ring.rpush(ring_key, json.dumps(_env(2, "RED")))
    fake_ring.rpush(ring_key, json.dumps(_env(3, "BLUE")))
    client = _client(session_factory)

    with client.websocket_connect(_url(_token(uid))) as ws:
        ws.send_json({"last_seq": 0})  # 補送整個 ring
        assert ws.receive_json()["type"] == "WELCOME"
        # BLUE client 只收 seq1（全體）+ seq3（BLUE）；seq2（RED）被濾
        assert [ws.receive_json()["seq"] for _ in range(2)] == [1, 3]


def test_parse_last_seq_rejects_non_int() -> None:
    """C6：HELLO.last_seq 非 int（bool/字串/缺失/非 dict）一律當 None，不炸 handler。"""
    from app.api.ws import _parse_last_seq

    assert _parse_last_seq({"last_seq": 5}) == 5
    assert _parse_last_seq({"last_seq": "5"}) is None  # 字串
    assert _parse_last_seq({"last_seq": True}) is None  # bool 非 seq
    assert _parse_last_seq({}) is None  # 缺失
    assert _parse_last_seq("nope") is None  # 非 dict
