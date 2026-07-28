"""#91 關係端點 + 盟軍共享視圖。

兩條核心不變式：
1. `/relations` **只回以觀測者為中心的一列**——不洩漏第三方之間的結盟。
2. `/units` 回「自己 + 盟軍」——偵測 sweep 一直假定盟軍走共享視圖而非偵測
   （`intel/sweep.py`），#98 接上關係矩陣後若不補這個，盟軍會變成互相隱形。
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from _auth_fakes import TEST_SETTINGS
from _order_fakes import OrderWorld, order_token, seed_world
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.api.deps import get_db, get_settings
from app.main import app
from app.models.enums import UnitLevel, UserRole
from app.models.tables import TacticalUnit, WargameSession


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


def _white(world: OrderWorld) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {order_token(world.white_user_id, UserRole.WHITE_CELL_STAFF)}"
    }


def _add_yellow_and_ally(factory: sessionmaker[Session], world: OrderWorld) -> None:
    """加一個 YELLOW 單位，並宣告 BLUE↔YELLOW 為盟友。"""
    with factory() as db:
        db.add(
            TacticalUnit(
                session_id=world.session_id,
                designation="Y1",
                unit_level=UnitLevel.PLATOON,
                faction="YELLOW",
                current_lat=23.77,
                current_lng=121.27,
            )
        )
        db.get(WargameSession, world.session_id).faction_relations = [  # type: ignore[union-attr]
            ["BLUE", "YELLOW", "ALLIED"]
        ]
        db.commit()


def test_relations_are_observer_centric(session_factory: sessionmaker[Session]) -> None:
    """以 BLUE 視角查 → 只得到 BLUE 對各方的關係（不含 RED↔YELLOW 之間如何）。"""
    world = seed_world(session_factory)
    _add_yellow_and_ally(session_factory, world)
    client = _client(session_factory)

    r = client.get(
        f"/api/v1/sessions/{world.session_id}/relations?as_faction=BLUE", headers=_white(world)
    )

    assert r.status_code == 200
    body = r.json()
    assert body["observer"] == "BLUE"
    assert body["relations"] == {"BLUE": "ALLIED", "RED": "HOSTILE", "YELLOW": "ALLIED"}
    assert sorted(body["factions"]) == ["BLUE", "RED", "YELLOW"]


def test_god_view_has_no_single_observer(session_factory: sessionmaker[Session]) -> None:
    """全局視角無單一觀測者 → observer=null、relations 空（前端據此不套敵我著色）。"""
    world = seed_world(session_factory)
    _add_yellow_and_ally(session_factory, world)
    client = _client(session_factory)

    body = client.get(
        f"/api/v1/sessions/{world.session_id}/relations", headers=_white(world)
    ).json()

    assert body["observer"] is None
    assert body["relations"] == {}
    assert sorted(body["factions"]) == ["BLUE", "RED", "YELLOW"]


def test_commander_cannot_observe_other_faction(session_factory: sessionmaker[Session]) -> None:
    """一般角色不得以他陣營為觀測者（與 units/intel 同紀律）。"""
    world = seed_world(session_factory)
    client = _client(session_factory)
    hdr = {"Authorization": f"Bearer {order_token(world.cmdr_user_id, UserRole.COMMANDER)}"}

    r = client.get(f"/api/v1/sessions/{world.session_id}/relations?as_faction=RED", headers=hdr)

    assert r.status_code == 403


def test_units_include_allied_faction(session_factory: sessionmaker[Session]) -> None:
    """BLUE 視角看得到盟軍 YELLOW 的單位（共享視圖），但看不到敵對的 RED。"""
    world = seed_world(session_factory)
    _add_yellow_and_ally(session_factory, world)
    client = _client(session_factory)

    units = client.get(
        f"/api/v1/sessions/{world.session_id}/units?as_faction=BLUE", headers=_white(world)
    ).json()

    assert sorted({u["faction"] for u in units}) == ["BLUE", "YELLOW"]


def test_units_without_alliance_stay_own_faction_only(
    session_factory: sessionmaker[Session],
) -> None:
    """未宣告關係的局（既有局皆如此）：維持只看得到自己，行為與過去完全一致。"""
    world = seed_world(session_factory)
    client = _client(session_factory)

    units = client.get(
        f"/api/v1/sessions/{world.session_id}/units?as_faction=BLUE", headers=_white(world)
    ).json()

    assert {u["faction"] for u in units} == {"BLUE"}
