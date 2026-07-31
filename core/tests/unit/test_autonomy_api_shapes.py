"""自主指派端點的回應形狀（UI-P4／G5）。

這三條端點過去回**裸 dict**，契約裡也沒有它們——前端只能手抄型別。
補契約時發現一個真的矛盾，這一檔把它釘住。
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


def test_put_and_get_agree_on_the_shape_of_factions(
    session_factory: sessionmaker[Session],
) -> None:
    """**同一個欄位不可以在 PUT 與 GET 回不同型別。**

    PUT 曾經回 `list(cfg.factions)`——`list(dict)` 取的是**鍵**，於是
    PUT 回 `["BLUE"]` 而 GET 回 `{"BLUE": {...}}`。存檔後重載會拿到兩種形狀。
    當時沒炸只是因為前端丟棄了 PUT 的回應；那是運氣不是設計。
    """
    world = seed_world(session_factory)
    client = _client(session_factory)
    tok = order_token(world.white_user_id, UserRole.WHITE_CELL_STAFF)
    hdr = {"Authorization": f"Bearer {tok}"}
    body = {
        "factions": {"BLUE": {"mission": "守住隘口", "objectives": [{"hold": "H1"}]}},
        "heartbeat_s": 30.0,
        "ai_ground_truth": True,
    }

    put = client.put(f"/api/v1/sessions/{world.session_id}/autonomy", json=body, headers=hdr)
    assert put.status_code == 200, put.text
    got = client.get(f"/api/v1/sessions/{world.session_id}/autonomy", headers=hdr)
    assert got.status_code == 200, got.text

    assert isinstance(put.json()["factions"], dict), "PUT 的 factions 不是物件"
    assert put.json()["factions"] == got.json()["factions"]
    # 回的是**存進去之後**的那份：非預設值要原樣帶回來，不是回請求的複本也不是回預設。
    assert put.json()["heartbeat_s"] == 30.0
    assert put.json()["ai_ground_truth"] is True
    assert got.json()["ai_ground_truth"] is True


def test_unset_autonomy_reads_back_as_empty_not_error(
    session_factory: sessionmaker[Session],
) -> None:
    """沒設定過＝空 `factions`，不是 404——白軍主控台第一次打開就是這個狀態。"""
    world = seed_world(session_factory)
    client = _client(session_factory)
    tok = order_token(world.white_user_id, UserRole.WHITE_CELL_STAFF)

    r = client.get(
        f"/api/v1/sessions/{world.session_id}/autonomy",
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert r.status_code == 200
    assert r.json()["factions"] == {}
