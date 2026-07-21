"""AAR 端點存取控制（O8，SPEC §14/§12）——參與者/ANALYST/全知可，其餘 403。"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from _auth_fakes import TEST_SETTINGS
from _order_fakes import order_token, seed_world
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.api.deps import get_db, get_settings
from app.main import app
from app.models import User
from app.models.enums import UserRole


@pytest.fixture(autouse=True)
def _clear() -> Iterator[None]:
    yield
    app.dependency_overrides.clear()


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


def _mk_user(factory: sessionmaker[Session], role: UserRole) -> str:
    with factory() as db:
        u = User(username=f"u-{role.value}", password_hash="x", role=role)
        db.add(u)
        db.commit()
        return u.id


def test_participant_can_access_aar(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    client = _client(session_factory)
    tok = order_token(world.cmdr_user_id, UserRole.COMMANDER)
    for path in ("aar/replay", "aar/stats", "aar/report"):
        r = client.get(f"/api/v1/sessions/{world.session_id}/{path}", headers=_hdr(tok))
        assert r.status_code == 200, path


def test_analyst_can_access(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    analyst = _mk_user(session_factory, UserRole.ANALYST)  # 非參與者，但 ANALYST 可看 AAR
    client = _client(session_factory)
    r = client.get(
        f"/api/v1/sessions/{world.session_id}/aar/stats",
        headers=_hdr(order_token(analyst, UserRole.ANALYST)),
    )
    assert r.status_code == 200


def test_non_participant_non_analyst_forbidden(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    outsider = _mk_user(session_factory, UserRole.OBSERVER)
    client = _client(session_factory)
    r = client.get(
        f"/api/v1/sessions/{world.session_id}/aar/replay",
        headers=_hdr(order_token(outsider, UserRole.OBSERVER)),
    )
    assert r.status_code == 403


def test_export_anonymize(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    client = _client(session_factory)
    tok = order_token(world.cmdr_user_id, UserRole.COMMANDER)
    r = client.get(
        f"/api/v1/sessions/{world.session_id}/aar/export",
        params={"fmt": "csv", "anonymize": "true"},
        headers=_hdr(tok),
    )
    assert r.status_code == 200 and "seq,tick,event_type" in r.text
