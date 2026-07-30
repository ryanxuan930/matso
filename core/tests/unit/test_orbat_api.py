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
        json={"designation": "B1-改", "current_strength": 80, "attributes": {"ammo": 5}},
        headers=white,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["designation"] == "B1-改"
    assert body["strength"] == 80
    assert body["attributes"]["ammo"] == 5


def test_health_is_derived_not_editable(session_factory: sessionmaker[Session]) -> None:
    """作戰效能是導出量——**明確拒絕勝過靜默忽略**。

    裁決層每次命中都會由戰力比重算並覆寫它；接受這個欄位等於讓統裁以為自己
    幫某個單位補了血，而下一次交戰就會把它打回去。
    """
    world = seed_world(session_factory)
    client: TestClient = make_client(session_factory)
    white, _ = _hdrs(world)
    unit = f"/api/v1/sessions/{world.session_id}/units/{world.blue_unit_id}"

    r = client.patch(unit, json={"health_status": 80}, headers=white)
    assert r.status_code == 422, r.text
    assert "current_strength" in r.text, "拒絕時要說得出正確的做法是什麼"


def test_strength_edit_recomputes_effectiveness(session_factory: sessionmaker[Session]) -> None:
    """改戰力 → 作戰效能跟著重算（而不是停在舊值）。"""
    world = seed_world(session_factory)
    client: TestClient = make_client(session_factory)
    white, _ = _hdrs(world)
    unit = f"/api/v1/sessions/{world.session_id}/units/{world.blue_unit_id}"

    full = client.patch(unit, json={"current_strength": 100}, headers=white).json()
    half = client.patch(unit, json={"current_strength": 50}, headers=white).json()
    assert half["health"] < full["health"], (
        f"戰力砍半，效能卻沒降（{full['health']} → {half['health']}）"
    )


def test_personnel_edit_changes_effective_platform_count(
    session_factory: sessionmaker[Session],
) -> None:
    """人數是**建制數**的來源之一——改了人數，齊射發數與面射擊佔地都會跟著變。

    這條盯的是「改了畫面上的數字，引擎卻用舊值」：`platform_count` 由
    `attributes.platform_count → personnel_current → 依級別導出` 三段推導，
    回應要把**實際生效**的那一個講出來，而不是把使用者填的原值回吐。
    """
    world = seed_world(session_factory)
    client: TestClient = make_client(session_factory)
    white, _ = _hdrs(world)
    unit = f"/api/v1/sessions/{world.session_id}/units/{world.blue_unit_id}"

    body = client.patch(unit, json={"personnel_current": 42}, headers=white).json()
    assert body["personnel_current"] == 42
    assert body["platform_count"] == 42, "沒有明示 platform_count 時，人數就是建制數"

    # 明示優先：attributes.platform_count 蓋過人數。
    body = client.patch(unit, json={"attributes": {"platform_count": 7}}, headers=white).json()
    assert body["personnel_current"] == 42
    assert body["platform_count"] == 7, "明示的 platform_count 應優先於人數"


def test_unit_level_edit_flags_restart_required(session_factory: sessionmaker[Session]) -> None:
    """改編制級別要說「這要重啟才生效」。

    聚合裁決門檻與武器/機動解析器的快取都是 runner 起跑那一刻讀的。
    不講的話，統裁改完級別、看畫面確實變了，就會以為聚合裁決也換了。
    """
    world = seed_world(session_factory)
    client: TestClient = make_client(session_factory)
    white, _ = _hdrs(world)
    unit = f"/api/v1/sessions/{world.session_id}/units/{world.blue_unit_id}"

    body = client.patch(unit, json={"unit_level": "BATTALION"}, headers=white).json()
    assert body["unit_level"] == "BATTALION"
    assert body["restart_required"] is True

    # 只改番號不需要重啟——別讓這個旗標變成永遠亮著的雜訊。
    body = client.patch(unit, json={"designation": "B9"}, headers=white).json()
    assert body["restart_required"] is False


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
