"""RBAC 存取矩陣（O7.5，SPEC §12）——角色 × 端點的存取權限 contract test。

矩陣（White Cell = EXERCISE_DIRECTOR/WHITE_CELL_STAFF；全知 = White Cell + ADMIN）：
| 端點            | White Cell | ADMIN | 作戰/觀察方 |
|-----------------|-----------|-------|-------------|
| inject          | ✓ 201     | ✗ 403 | ✗ 403       |
| control         | ✓ 201     | ✗ 403 | ✗ 403       |
| units?as_faction| ✓ 200     | ✓ 200 | ✗ 403       |
| intel?as_faction(他陣營) | ✓ 200 | ✓ 200 | ✗ 403 |
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from _auth_fakes import TEST_SETTINGS
from fakeredis import FakeStrictRedis
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

import app.api.control as control_mod
import app.api.inject as inject_mod
from app.api.deps import get_db, get_settings
from app.auth.hashing import hash_password
from app.auth.tokens import JwtCodec, TokenType
from app.main import app
from app.models import SessionParticipant, User, WargameSession
from app.models.enums import SessionMode, UserRole

_SID = "rbac-sess"

WHITE_CELL = {UserRole.EXERCISE_DIRECTOR, UserRole.WHITE_CELL_STAFF}
OMNISCIENT = WHITE_CELL | {UserRole.ADMIN}
ALL_ROLES = [
    UserRole.EXERCISE_DIRECTOR,
    UserRole.WHITE_CELL_STAFF,
    UserRole.ADMIN,
    UserRole.COMMANDER,
    UserRole.STAFF,
    UserRole.OBSERVER,
    UserRole.ANALYST,
]


@pytest.fixture(autouse=True)
def _redis(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    fake = FakeStrictRedis(decode_responses=True)
    monkeypatch.setattr(inject_mod.redis, "from_url", lambda *a, **k: fake)
    monkeypatch.setattr(control_mod.redis, "from_url", lambda *a, **k: fake)
    yield
    app.dependency_overrides.clear()


def _seed(factory: sessionmaker[Session], role: UserRole) -> str:
    with factory() as db:
        db.add(
            WargameSession(
                id=_SID, name="s", master_seed=1, mode=SessionMode.REALTIME, current_weather={}
            )
        )
        user = User(username="u", password_hash=hash_password("pw"), role=role)
        db.add(user)
        db.flush()
        db.add(
            SessionParticipant(
                user_id=user.id, session_id=_SID, faction="BLUE", role=role, unit_scope=[]
            )
        )
        db.commit()
        return _token(user.id, role)


def _token(uid: str, role: UserRole) -> str:
    codec = JwtCodec(secret=TEST_SETTINGS.jwt_secret)
    return codec.issue(uid, role.value, TokenType.ACCESS, 900)


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


def _hdr(t: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {t}"}


@pytest.mark.parametrize("role", ALL_ROLES)
def test_inject_matrix(session_factory: sessionmaker[Session], role: UserRole) -> None:
    token = _seed(session_factory, role)
    r = _client(session_factory).post(
        f"/api/v1/sessions/{_SID}/inject", json={"event_type": "X"}, headers=_hdr(token)
    )
    assert r.status_code == (201 if role in WHITE_CELL else 403)


@pytest.mark.parametrize("role", ALL_ROLES)
def test_control_matrix(session_factory: sessionmaker[Session], role: UserRole) -> None:
    token = _seed(session_factory, role)
    r = _client(session_factory).post(
        f"/api/v1/sessions/{_SID}/control", json={"action": "PAUSE"}, headers=_hdr(token)
    )
    assert r.status_code == (201 if role in WHITE_CELL else 403)


@pytest.mark.parametrize("role", ALL_ROLES)
def test_units_viewpoint_matrix(session_factory: sessionmaker[Session], role: UserRole) -> None:
    token = _seed(session_factory, role)
    r = _client(session_factory).get(
        f"/api/v1/sessions/{_SID}/units", params={"as_faction": "RED"}, headers=_hdr(token)
    )
    assert r.status_code == (200 if role in OMNISCIENT else 403)


@pytest.mark.parametrize("role", ALL_ROLES)
def test_intel_viewpoint_matrix(session_factory: sessionmaker[Session], role: UserRole) -> None:
    token = _seed(session_factory, role)
    # 帶「他陣營」as_faction（參與者為 BLUE，查 RED）
    r = _client(session_factory).get(
        f"/api/v1/sessions/{_SID}/intel", params={"as_faction": "RED"}, headers=_hdr(token)
    )
    assert r.status_code == (200 if role in OMNISCIENT else 403)
