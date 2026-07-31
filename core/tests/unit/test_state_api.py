"""狀態快照端點（`GET /sessions/{id}/state`）——**暫停與否要看得到**。

暫停旗標過去只有 `POST /control` 會寫、runner 會讀，**沒有任何 GET 曝露它**。
於是操作員看到 tick 停住時無從判斷「白軍按了暫停」還是「系統掛了」——
前者等就好，後者要叫人，那是兩種完全不同的處置。
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from _auth_fakes import TEST_SETTINGS
from _order_fakes import order_token, seed_world
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.api.deps import get_db, get_settings
from app.main import app
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


def test_the_snapshot_says_whether_the_session_is_paused(
    session_factory: sessionmaker[Session],
) -> None:
    """**「tick 不動」與「系統掛了」要分得開。**"""
    world = seed_world(session_factory)
    client = _client(session_factory)
    hdr = {"Authorization": f"Bearer {order_token(world.cmdr_user_id, UserRole.COMMANDER)}"}
    white = {
        "Authorization": f"Bearer {order_token(world.white_user_id, UserRole.WHITE_CELL_STAFF)}"
    }

    before = client.get(f"/api/v1/sessions/{world.session_id}/state", headers=hdr)
    assert before.status_code == 200, before.text
    assert before.json()["paused"] is False

    paused = client.post(
        f"/api/v1/sessions/{world.session_id}/control",
        json={"action": "PAUSE"},
        headers=white,
    )
    assert paused.status_code == 201, paused.text
    after = client.get(f"/api/v1/sessions/{world.session_id}/state", headers=hdr)
    assert after.json()["paused"] is True, "白軍按了暫停，但快照說沒有"

    client.post(
        f"/api/v1/sessions/{world.session_id}/control",
        json={"action": "RESUME"},
        headers=white,
    )
    resumed = client.get(f"/api/v1/sessions/{world.session_id}/state", headers=hdr)
    assert resumed.json()["paused"] is False, "恢復之後還說暫停中——比不顯示更糟"
