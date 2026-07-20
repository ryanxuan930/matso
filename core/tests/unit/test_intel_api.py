"""Intel 查詢端點（O3.3）：faction-scoped + 去識別化投影，經 TestClient。"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from _order_fakes import seed_world
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.api.deps import get_db
from app.intel import store
from app.intel.sweep import Contact
from app.main import app
from app.models.enums import Faction, IntelFidelity


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
    return TestClient(app)


def test_intel_endpoint_faction_scoped(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    with session_factory() as db:
        store.record(
            db,
            world.session_id,
            Contact(
                Faction.RED, world.blue_unit_id, IntelFidelity.DETECTED, 3, 23.75, 121.25, 500.0
            ),
        )
        db.commit()
    client = _client(session_factory)

    red = client.get(f"/api/v1/sessions/{world.session_id}/intel", params={"faction": "RED"})
    assert red.status_code == 200
    body = red.json()
    assert len(body) == 1
    assert body[0]["fidelity"] == "DETECTED"
    assert body[0]["designation"] is None  # 去識別化
    assert "target_unit_id" not in body[0]  # ground truth 永不下發

    # BLUE 查不到 RED 的 contact
    blue = client.get(f"/api/v1/sessions/{world.session_id}/intel", params={"faction": "BLUE"})
    assert blue.json() == []


def test_intel_endpoint_rejects_bad_faction(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    client = _client(session_factory)
    r = client.get(f"/api/v1/sessions/{world.session_id}/intel", params={"faction": "PURPLE"})
    assert r.status_code == 422  # enum 驗證
