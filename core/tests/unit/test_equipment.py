"""編裝（裝備/武器裝載）編輯端點（stage ①）：範本目錄 + 增/列/改/刪 + RBAC + fog of war。"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from _auth_fakes import TEST_SETTINGS
from _order_fakes import OrderWorld, order_token, seed_world
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.adjudication import ensure_weapon_templates
from app.api.deps import get_db, get_settings
from app.main import app
from app.models import UserRole, WargameSession


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


def _seed_templates(factory: sessionmaker[Session]) -> dict[str, str]:
    with factory() as db:
        tids = ensure_weapon_templates(db)
        db.commit()
        return tids


def _set_orbat_edit(factory: sessionmaker[Session], session_id: str, factions: list[str]) -> None:
    with factory() as db:
        s = db.get(WargameSession, session_id)
        assert s is not None
        s.orbat_edit_factions = factions
        db.commit()


def _base(world: OrderWorld, unit_id: str) -> str:
    return f"/api/v1/sessions/{world.session_id}/units/{unit_id}/equipment"


def test_list_templates(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    _seed_templates(session_factory)
    r = _client(session_factory).get("/api/v1/equipment-templates", headers=_white(world))
    assert r.status_code == 200
    assert "RIFLE_556" in {t["name"] for t in r.json()}


def test_white_add_list_patch_delete(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    tids = _seed_templates(session_factory)
    client = _client(session_factory)
    base = _base(world, world.blue_unit_id)

    r = client.post(base, json={"template_id": tids["RIFLE_556"]}, headers=_white(world))
    assert r.status_code == 201, r.text
    inst = r.json()
    assert inst["category"] == "KINETIC"
    assert inst["current_state"]["ammo"] == 100
    iid = inst["id"]

    r = client.get(base, headers=_white(world))
    assert r.status_code == 200
    assert any(i["id"] == iid for i in r.json())

    r = client.patch(f"{base}/{iid}", json={"current_state": {"ammo": 42}}, headers=_white(world))
    assert r.status_code == 200
    assert r.json()["current_state"]["ammo"] == 42

    r = client.delete(f"{base}/{iid}", headers=_white(world))
    assert r.status_code == 204
    r = client.get(base, headers=_white(world))
    assert all(i["id"] != iid for i in r.json())


def test_equipment_quantity_roundtrip(session_factory: sessionmaker[Session]) -> None:
    # #30：配發時帶 quantity + PATCH 調整建制數量（squad 火力容量來源）。
    world = seed_world(session_factory)
    tids = _seed_templates(session_factory)
    client = _client(session_factory)
    base = _base(world, world.blue_unit_id)

    r = client.post(
        base, json={"template_id": tids["RIFLE_556"], "quantity": 7}, headers=_white(world)
    )
    assert r.status_code == 201, r.text
    inst = r.json()
    assert inst["quantity"] == 7
    iid = inst["id"]

    r = client.patch(
        f"{base}/{iid}", json={"current_state": {}, "quantity": 9}, headers=_white(world)
    )
    assert r.status_code == 200 and r.json()["quantity"] == 9

    # 預設不帶 quantity → 1。
    r = client.post(base, json={"template_id": tids["RIFLE_556"]}, headers=_white(world))
    assert r.json()["quantity"] == 1


def test_commander_needs_orbat_permission(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    tids = _seed_templates(session_factory)
    client = _client(session_factory)
    base = _base(world, world.blue_unit_id)

    # 預設 BLUE 不在自編清單 → 403
    r = client.post(base, json={"template_id": tids["RIFLE_556"]}, headers=_cmdr(world))
    assert r.status_code == 403

    # 開放 BLUE 自編 → 201
    _set_orbat_edit(session_factory, world.session_id, ["BLUE"])
    r = client.post(base, json={"template_id": tids["RIFLE_556"]}, headers=_cmdr(world))
    assert r.status_code == 201


def test_commander_cannot_touch_enemy(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    tids = _seed_templates(session_factory)
    _set_orbat_edit(session_factory, world.session_id, ["BLUE"])
    client = _client(session_factory)
    red = _base(world, world.red_unit_id)

    assert client.get(red, headers=_cmdr(world)).status_code == 403  # 讀他方 loadout
    r = client.post(red, json={"template_id": tids["RIFLE_556"]}, headers=_cmdr(world))
    assert r.status_code == 403  # 編他方裝備


def test_bad_template_422(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    _seed_templates(session_factory)
    client = _client(session_factory)
    r = client.post(
        _base(world, world.blue_unit_id), json={"template_id": "nope"}, headers=_white(world)
    )
    assert r.status_code == 422


# ---------------- stage ②：武器庫（範本）建立/更新 + 驗證 + admin RBAC ----------------

_VALID_KINETIC = {
    "max_range_m": 800,
    "min_range_m": 0,
    "ph_by_range_band": [[400, 0.6], [800, 0.3]],
    "damage_by_armor_class": {"INFANTRY": 30},
    "ammo_types": ["AMMO_X"],
}


def test_create_template_admin_ok(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    client = _client(session_factory)
    r = client.post(
        "/api/v1/equipment-templates",
        json={"name": "TEST_GUN", "category": "KINETIC", "base_stats": _VALID_KINETIC},
        headers=_white(world),
    )
    assert r.status_code == 201, r.text
    tid = r.json()["id"]
    # 更新射程
    upd = {**_VALID_KINETIC, "max_range_m": 1200}
    r = client.put(
        f"/api/v1/equipment-templates/{tid}",
        json={"name": "TEST_GUN", "category": "KINETIC", "base_stats": upd},
        headers=_white(world),
    )
    assert r.status_code == 200
    assert r.json()["base_stats"]["max_range_m"] == 1200


_MISSILE_STATS = {
    "max_range_m": 2500,
    "min_range_m": 65,
    "ph_by_range_band": [[500, 0.9], [2500, 0.85]],
    "damage_by_armor_class": {"ARMOR": 200},
    "pk_by_armor_class": {"ARMOR": 0.9},
    "ammo_types": ["FGM148"],
    "missile_kind": "ATGM",
    "guidance": "FIRE_AND_FORGET",
    "warhead": "TANDEM_HEAT",
    "top_attack": True,
    "flight_speed_ms": 200,
}


def test_create_missile_template_validates(session_factory: sessionmaker[Session]) -> None:
    # #飛彈：MISSILE（allOf kinetic + 飛彈諸元）建立成功。
    world = seed_world(session_factory)
    client = _client(session_factory)
    r = client.post(
        "/api/v1/equipment-templates",
        json={"name": "JAVELIN", "category": "MISSILE", "base_stats": _MISSILE_STATS},
        headers=_white(world),
    )
    assert r.status_code == 201, r.text
    assert r.json()["category"] == "MISSILE"

    # 缺 kinetic 必填（ammo_types）→ 422
    bad = {k: v for k, v in _MISSILE_STATS.items() if k != "ammo_types"}
    r2 = client.post(
        "/api/v1/equipment-templates",
        json={"name": "BAD", "category": "MISSILE", "base_stats": bad},
        headers=_white(world),
    )
    assert r2.status_code == 422


def test_create_support_category_templates(session_factory: sessionmaker[Session]) -> None:
    # 感測器/通信/後勤/無人機結構化屬性建立成功（enriched schema，含新增可選欄位）。
    world = seed_world(session_factory)
    client = _client(session_factory)
    cases = {
        "SENSOR": {
            "sensor_kind": "RADAR",
            "max_range_m": 40000,
            "detect_curve": [[20000, 0.9]],
            "fov_deg": 120,
            "passive": False,
            "min_target_rcs_m2": 2,
        },
        "COMMS": {
            "band": "SATCOM",
            "tx_power_dbm": 40,
            "rx_sensitivity_dbm": -110,
            "freq_mhz": 1500,
            "encrypted": True,
            "leo_satcom": True,
        },
        "LOGISTICS": {
            "capacity": {"AMMO": 500, "FUEL": 800},
            "resupply_rate_per_tick": 50,
            "crew": 3,
        },
        "DRONE": {
            "drone_kind": "LOITERING_MUNITION",
            "endurance_ticks": 60,
            "cruise_speed_ms": 45,
            "is_expendable": True,
            "sensor_payload": "EO_IR",
            "weather_limits": {"max_wind_ms": 15},
        },
    }
    for cat, stats in cases.items():
        r = client.post(
            "/api/v1/equipment-templates",
            json={"name": f"T_{cat}", "category": cat, "base_stats": stats},
            headers=_white(world),
        )
        assert r.status_code == 201, f"{cat}: {r.text}"


def test_create_template_commander_403(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    client = _client(session_factory)
    r = client.post(
        "/api/v1/equipment-templates",
        json={"name": "X", "category": "KINETIC", "base_stats": _VALID_KINETIC},
        headers=_cmdr(world),
    )
    assert r.status_code == 403


def test_create_template_invalid_stats_422(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    client = _client(session_factory)
    # 缺 required 的 ph_by_range_band / ammo_types → schema 驗證失敗
    r = client.post(
        "/api/v1/equipment-templates",
        json={"name": "X", "category": "KINETIC", "base_stats": {"max_range_m": 500}},
        headers=_white(world),
    )
    assert r.status_code == 422
