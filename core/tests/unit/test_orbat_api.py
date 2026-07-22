"""編裝編輯 REST（#6）：White Cell 編輯單位 + per-faction 自編權限閘。"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from _auth_fakes import auth_header, make_client
from _order_fakes import order_token, seed_world
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.main import app
from app.models import UserRole


@pytest.fixture(autouse=True)
def _clear() -> Iterator[None]:
    yield
    app.dependency_overrides.clear()


def _hdrs(world):  # type: ignore[no-untyped-def]
    white = auth_header(order_token(world.white_user_id, UserRole.WHITE_CELL_STAFF))
    cmdr = auth_header(order_token(world.cmdr_user_id, UserRole.COMMANDER))
    return white, cmdr


def test_white_cell_edits_unit(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    client: TestClient = make_client(session_factory)
    white, _ = _hdrs(world)
    r = client.patch(
        f"/api/v1/sessions/{world.session_id}/units/{world.blue_unit_id}",
        json={"designation": "B1-改", "health_status": 80, "attributes": {"ammo": 5}},
        headers=white,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert (
        body["designation"] == "B1-改" and body["health"] == 80 and body["attributes"]["ammo"] == 5
    )


def test_commander_blocked_until_faction_enabled(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    client: TestClient = make_client(session_factory)
    white, cmdr = _hdrs(world)
    unit = f"/api/v1/sessions/{world.session_id}/units/{world.blue_unit_id}"

    # 未開放 → 藍軍指揮官不能編（即使是本軍）
    assert client.patch(unit, json={"designation": "x"}, headers=cmdr).status_code == 403

    # 白軍開放 BLUE 自編
    perms = f"/api/v1/sessions/{world.session_id}/orbat-permissions"
    assert client.put(perms, json={"factions": ["BLUE"]}, headers=white).status_code == 200

    # 現在藍軍可編本軍單位
    assert client.patch(unit, json={"designation": "B1x"}, headers=cmdr).status_code == 200
    # 但仍不能編他軍（RED）單位
    red = f"/api/v1/sessions/{world.session_id}/units/{world.red_unit_id}"
    assert client.patch(red, json={"designation": "hack"}, headers=cmdr).status_code == 403


def test_only_white_cell_sets_permissions(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    client: TestClient = make_client(session_factory)
    _, cmdr = _hdrs(world)
    perms = f"/api/v1/sessions/{world.session_id}/orbat-permissions"
    assert client.get(perms, headers=cmdr).status_code == 403
    assert client.put(perms, json={"factions": ["BLUE"]}, headers=cmdr).status_code == 403
