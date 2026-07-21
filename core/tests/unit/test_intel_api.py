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
from app.models.enums import IntelFidelity


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
            Contact("RED", world.blue_unit_id, IntelFidelity.DETECTED, 3, 23.75, 121.25, 500.0),
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


def test_intel_endpoint_rejects_malformed_faction(session_factory: sessionmaker[Session]) -> None:
    """O6.7：faction 為字串 id，格式驗證（§12.1）。格式不符 → 422。
    （未宣告但格式合法的 faction＝空 contacts，session-membership 驗證屬 O6.8。）"""
    world = seed_world(session_factory)
    client = _client(session_factory)
    r = client.get(f"/api/v1/sessions/{world.session_id}/intel", params={"faction": "bad-faction"})
    assert r.status_code == 422  # 格式驗證（小寫/連字號非法）
    # 格式合法但未宣告的 faction → 200 + 空（O6.8 才做 session 成員驗證）
    ok = client.get(f"/api/v1/sessions/{world.session_id}/intel", params={"faction": "PURPLE"})
    assert ok.status_code == 200 and ok.json() == []
