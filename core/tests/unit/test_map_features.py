"""地圖標註/工事（MapFeature）CRUD（stage ③）：建立/列/改/刪 + fog of war + 陣營編修權。"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from _auth_fakes import TEST_SETTINGS
from _order_fakes import OrderWorld, order_token, seed_world
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.api.deps import get_db, get_gateway, get_settings
from app.main import app
from app.models import UserRole
from app.orders.precheck import LosOutcome


@pytest.fixture(autouse=True)
def _clear_overrides() -> Iterator[None]:
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
    tok = order_token(world.white_user_id, UserRole.WHITE_CELL_STAFF)
    return {"Authorization": f"Bearer {tok}"}


def _cmdr(world: OrderWorld) -> dict[str, str]:
    return {"Authorization": f"Bearer {order_token(world.cmdr_user_id, UserRole.COMMANDER)}"}


def _base(world: OrderWorld) -> str:
    return f"/api/v1/sessions/{world.session_id}/map-features"


_POINT = {"kind": "WEAPON_EMPLACEMENT", "geometry_type": "POINT", "geometry": [121.3, 23.8]}
_Llh = tuple[float, float, float]  # (lat, lng, 離地高 m)


def test_white_creates_common_visible_to_commander(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    client = _client(session_factory)
    # 白軍建共同（WHITE_CELL）武器據點
    r = client.post(_base(world), json={**_POINT, "label": "SAM-1"}, headers=_white(world))
    assert r.status_code == 201, r.text
    assert r.json()["owner_faction"] == "WHITE_CELL"
    # 藍軍指揮官看得到共同標註
    r = client.get(_base(world), headers=_cmdr(world))
    assert r.status_code == 200
    assert any(f["label"] == "SAM-1" for f in r.json())


def test_commander_creates_own_and_cannot_forge_faction(
    session_factory: sessionmaker[Session],
) -> None:
    world = seed_world(session_factory)
    client = _client(session_factory)
    # 指揮官建本軍標註（owner_faction 省略→本軍 BLUE）
    r = client.post(_base(world), json={**_POINT, "kind": "OBSTACLE"}, headers=_cmdr(world))
    assert r.status_code == 201
    assert r.json()["owner_faction"] == "BLUE"
    # 冒用他軍陣營 → 403
    r = client.post(_base(world), json={**_POINT, "owner_faction": "RED"}, headers=_cmdr(world))
    assert r.status_code == 403


def test_fog_of_war_hides_enemy_annotations(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    client = _client(session_factory)
    # 白軍替 RED 建一個標註
    body = {**_POINT, "owner_faction": "RED", "label": "RED-OP"}
    client.post(_base(world), json=body, headers=_white(world))
    # 藍軍指揮官不應看到 RED 的標註
    r = client.get(_base(world), headers=_cmdr(world))
    assert all(f["label"] != "RED-OP" for f in r.json())


def test_edit_delete_permission(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    client = _client(session_factory)
    # 白軍建共同標註
    fid = client.post(_base(world), json=_POINT, headers=_white(world)).json()["id"]
    # 藍軍不可編共同/他方標註
    r = client.patch(f"{_base(world)}/{fid}", json={"label": "hack"}, headers=_cmdr(world))
    assert r.status_code == 403
    # 白軍可編
    r = client.patch(f"{_base(world)}/{fid}", json={"label": "moved"}, headers=_white(world))
    assert r.status_code == 200 and r.json()["label"] == "moved"
    # 白軍可刪
    assert client.delete(f"{_base(world)}/{fid}", headers=_white(world)).status_code == 204


def test_bad_geometry_type_422(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    client = _client(session_factory)
    r = client.post(
        _base(world),
        json={"kind": "OBSTACLE", "geometry_type": "BLOB", "geometry": []},
        headers=_white(world),
    )
    assert r.status_code == 422


class _EastBlockedGateway:
    """測試用 gateway：朝東（目標經度 > 射源）方位視線被地形遮於 500m；其餘全通。"""

    def path_reachable(self, from_h3: str, to_h3: str, mobility_profile: str) -> tuple[bool, str]:
        return True, "stub"

    def has_los(
        self, observer: tuple[float, float, float], target: tuple[float, float, float]
    ) -> LosOutcome:
        if target[1] - observer[1] > 0.003:  # 目標在射源以東
            return LosOutcome(False, -5.0, target[0], observer[1] + 0.003)
        return LosOutcome(True, 30.0)


def test_terrain_footprint_clips_blocked_bearings(
    session_factory: sessionmaker[Session],
) -> None:
    world = seed_world(session_factory)
    app.dependency_overrides[get_gateway] = lambda: _EastBlockedGateway()
    client = _client(session_factory)
    body = {
        "origin": [121.3, 23.8],
        "max_range_m": 3000.0,
        "arc_deg": 360.0,
        "steps": 24,
    }
    r = client.post(
        f"/api/v1/sessions/{world.session_id}/terrain/footprint", json=body, headers=_white(world)
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["clipped"] is True  # 有朝東方位被裁切
    assert len(data["ring"]) >= 3
    assert data["max_range_m"] == 3000.0


def test_terrain_footprint_full_range_when_clear(
    session_factory: sessionmaker[Session],
) -> None:
    world = seed_world(session_factory)

    class _AllClear:
        def path_reachable(self, f: str, t: str, m: str) -> tuple[bool, str]:
            return True, "stub"

        def has_los(self, o: _Llh, t: _Llh) -> LosOutcome:
            return LosOutcome(True, 50.0)

    app.dependency_overrides[get_gateway] = lambda: _AllClear()
    client = _client(session_factory)
    body = {"origin": [121.3, 23.8], "max_range_m": 2000.0, "direction_deg": 90.0, "arc_deg": 60.0}
    r = client.post(
        f"/api/v1/sessions/{world.session_id}/terrain/footprint", json=body, headers=_white(world)
    )
    assert r.status_code == 200, r.text
    assert r.json()["clipped"] is False
