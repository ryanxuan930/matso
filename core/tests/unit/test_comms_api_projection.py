"""WP-C5 在 REST 投影層的後果：`/units` 位置凍結、`/intel` 敵情粗化、`/state` 通聯姿態。

驗收條文（SPEC_V2 §6 WP-C5）「己方 COP 該單位凍結、白軍視角照動」在此逐條釘住。
最容易寫錯的是**視角語義**：白軍的 god view 看真實位置，但白軍指定 `as_faction=X` 是在問
「X 看得到什麼」——那時要凍結。兩者都測。
"""

from __future__ import annotations

import json
from collections.abc import Iterator

import pytest
from _auth_fakes import TEST_SETTINGS
from _order_fakes import OrderWorld, order_token, seed_world
from fakeredis import FakeStrictRedis
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

import app.api.intel as intel_mod
import app.api.units as units_mod
from app.api.deps import get_db, get_settings
from app.intel import store
from app.intel.service import COARSE_H3_RES, coarse_error_radius_m
from app.intel.sweep import Contact
from app.models.enums import IntelFidelity, UserRole
from app.state.hot_state import unit_key

# 三個互不相同的座標，才分得出投影拿的是哪一個：
_DB_LAT, _DB_LNG = 23.750, 121.250  # seed_world 寫進 DB 的位置（`/units` 的真實來源）
_HOT_LAT, _HOT_LNG = 24.900, 121.900  # 熱狀態的真實位置（斷聯後仍照常演進）
_REPORT_LAT, _REPORT_LNG, _REPORT_TICK = 23.500, 121.000, 42  # 最後一次位置回報


@pytest.fixture
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> FakeStrictRedis:
    client = FakeStrictRedis(decode_responses=True)
    for module in (units_mod, intel_mod):
        monkeypatch.setattr(module, "make_redis", lambda *_a, **_k: client)
    return client


@pytest.fixture
def client(session_factory: sessionmaker[Session]) -> Iterator[TestClient]:
    from app.main import app

    app.dependency_overrides[get_db] = lambda: session_factory()
    app.dependency_overrides[get_settings] = lambda: TEST_SETTINGS
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def world(session_factory: sessionmaker[Session]) -> OrderWorld:
    return seed_world(session_factory)


def _hot(redis: FakeStrictRedis, world: OrderWorld, unit_id: str, **fields: object) -> None:
    redis.set(unit_key(world.session_id, unit_id), json.dumps(fields))


def _offline_blue(redis: FakeStrictRedis, world: OrderWorld) -> None:
    """藍方單位斷聯：真實位置已跑到 _HOT_*，最後回報停在 _REPORT_*。"""
    _hot(
        redis,
        world,
        world.blue_unit_id,
        comms_state="OFFLINE",
        lat=_HOT_LAT,
        lng=_HOT_LNG,
        report_lat=_REPORT_LAT,
        report_lng=_REPORT_LNG,
        report_tick=_REPORT_TICK,
    )


def _cmdr(w: OrderWorld) -> dict[str, str]:
    return {"Authorization": f"Bearer {order_token(w.cmdr_user_id, UserRole.COMMANDER)}"}


def _white(w: OrderWorld) -> dict[str, str]:
    return {"Authorization": f"Bearer {order_token(w.white_user_id, UserRole.WHITE_CELL_STAFF)}"}


def _get(client: TestClient, w: OrderWorld, path: str, headers: dict[str, str], q: str = ""):  # type: ignore[no-untyped-def]
    r = client.get(f"/api/v1/sessions/{w.session_id}/{path}{q}", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


# --- 位置凍結 ---


def test_own_offline_unit_is_frozen_for_its_commander(
    client: TestClient, world: OrderWorld, fake_redis: FakeStrictRedis
) -> None:
    _offline_blue(fake_redis, world)
    unit = _get(client, world, "units", _cmdr(world))[0]
    assert (unit["lat"], unit["lng"]) == (_REPORT_LAT, _REPORT_LNG)
    assert unit["stale_since_tick"] == _REPORT_TICK
    assert unit["comms"] == "OFFLINE"  # 通聯狀態改讀熱狀態（DB 欄位播種後從未被寫過）


def test_white_cell_god_view_sees_the_truth(
    client: TestClient, world: OrderWorld, fake_redis: FakeStrictRedis
) -> None:
    """驗收條文：「己方 COP 該單位凍結、白軍視角照動」。

    `/units` 的座標來源是 DB（movement 每 tick 落盤），故 god view 看到的是 `_DB_*`；
    重點是它**不是** `_REPORT_*`——白軍不受作戰方的通聯狀況影響。
    """
    _offline_blue(fake_redis, world)
    blue = next(
        u for u in _get(client, world, "units", _white(world)) if u["id"] == world.blue_unit_id
    )
    assert (blue["lat"], blue["lng"]) == (_DB_LAT, _DB_LNG)
    assert blue["stale_since_tick"] is None


def test_white_cell_viewpoint_switch_does_freeze(
    client: TestClient, world: OrderWorld, fake_redis: FakeStrictRedis
) -> None:
    """指定 as_faction ＝問「這一軍看得到什麼」，故凍結照套（與 O7.4 視角語義一致）。"""
    _offline_blue(fake_redis, world)
    blue = _get(client, world, "units", _white(world), "?as_faction=BLUE")[0]
    assert (blue["lat"], blue["lng"]) == (_REPORT_LAT, _REPORT_LNG)
    assert blue["stale_since_tick"] == _REPORT_TICK


def test_online_unit_is_not_frozen(
    client: TestClient, world: OrderWorld, fake_redis: FakeStrictRedis
) -> None:
    _hot(
        fake_redis,
        world,
        world.blue_unit_id,
        comms_state="ONLINE",
        report_lat=_REPORT_LAT,
        report_lng=_REPORT_LNG,
        report_tick=_REPORT_TICK,
    )
    unit = _get(client, world, "units", _cmdr(world))[0]
    assert unit["stale_since_tick"] is None
    assert (unit["lat"], unit["lng"]) == (_DB_LAT, _DB_LNG)  # DB 的真實座標


def test_no_live_sim_means_no_freeze(
    client: TestClient, world: OrderWorld, fake_redis: FakeStrictRedis
) -> None:
    """熱狀態全空（該局沒在跑）→ 一切照 DB，凍結不得無中生有。"""
    unit = _get(client, world, "units", _cmdr(world))[0]
    assert unit["stale_since_tick"] is None
    assert (unit["lat"], unit["lng"]) == (_DB_LAT, _DB_LNG)


def test_redis_outage_degrades_to_truth_not_to_blank(
    client: TestClient, world: OrderWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    """基礎設施故障不該讓玩家看不到自己的部隊（放寬的極限是己方單位，不涉敵方可見性）。"""

    class Down:
        def mget(self, _keys: list[str]) -> list[str]:
            raise ConnectionError("redis down")

    monkeypatch.setattr(units_mod, "make_redis", lambda *_a, **_k: Down())
    unit = _get(client, world, "units", _cmdr(world))[0]
    assert (unit["lat"], unit["lng"]) == (_DB_LAT, _DB_LNG)


# --- 敵情粗化 ---


def _seed_contact(factory: sessionmaker[Session], world: OrderWorld) -> None:
    """給藍方一筆 IDENTIFIED 級的紅軍接觸（粗化前後差異最大）。"""
    with factory() as db:
        store.record(
            db,
            world.session_id,
            Contact(
                "BLUE", world.red_unit_id, IntelFidelity.IDENTIFIED, 9, 23.7601, 121.2601, 50.0
            ),
        )
        db.commit()


def test_intel_is_coarsened_when_the_faction_network_degrades(
    client: TestClient,
    session_factory: sessionmaker[Session],
    world: OrderWorld,
    fake_redis: FakeStrictRedis,
) -> None:
    import h3

    _seed_contact(session_factory, world)
    sharp = _get(client, world, "intel", _cmdr(world))[0]
    assert sharp["fidelity"] == "IDENTIFIED" and sharp["designation"] == "R1"

    _offline_blue(fake_redis, world)  # 藍方唯一單位斷聯 → 全斷 → FROZEN
    coarse = _get(client, world, "intel", _cmdr(world))[0]
    assert coarse["fidelity"] == "DETECTED"
    # 身分欄位必須跟著收回——只粗化座標卻留著番號，等於 fidelity 欄位與內容不符。
    assert (coarse["designation"], coarse["unit_type"], coarse["faction"]) == (None, None, None)
    assert (coarse["lat"], coarse["lng"]) == h3.cell_to_latlng(
        h3.latlng_to_cell(sharp["lat"], sharp["lng"], COARSE_H3_RES)
    )
    assert coarse["error_radius_m"] == pytest.approx(coarse_error_radius_m())


def test_white_cell_god_view_intel_is_never_coarsened(
    client: TestClient,
    session_factory: sessionmaker[Session],
    world: OrderWorld,
    fake_redis: FakeStrictRedis,
) -> None:
    _seed_contact(session_factory, world)
    _offline_blue(fake_redis, world)
    contact = _get(client, world, "intel", _white(world))[0]
    assert contact["fidelity"] == "IDENTIFIED"
    assert contact["lat"] == pytest.approx(23.7601)


def test_enemy_comms_state_does_not_coarsen_my_picture(
    client: TestClient,
    session_factory: sessionmaker[Session],
    world: OrderWorld,
    fake_redis: FakeStrictRedis,
) -> None:
    """粒度只看**自己**陣營的網路。紅軍斷聯是紅軍的問題，藍軍的圖不該跟著糊。"""
    _seed_contact(session_factory, world)
    _hot(fake_redis, world, world.red_unit_id, comms_state="OFFLINE")
    _hot(fake_redis, world, world.blue_unit_id, comms_state="ONLINE")
    assert _get(client, world, "intel", _cmdr(world))[0]["fidelity"] == "IDENTIFIED"


# --- /state 快照的一致性（WP-E3 的「快照 == 各端點」不能被本卡弄破）---


def test_snapshot_still_matches_every_endpoint_under_degradation(
    client: TestClient,
    session_factory: sessionmaker[Session],
    world: OrderWorld,
    fake_redis: FakeStrictRedis,
) -> None:
    _seed_contact(session_factory, world)
    _offline_blue(fake_redis, world)
    snap = _get(client, world, "state", _cmdr(world))
    assert snap["units"] == _get(client, world, "units", _cmdr(world))
    assert snap["contacts"] == _get(client, world, "intel", _cmdr(world))
    assert snap["comms_posture"] == "OFFLINE"
    assert snap["units"][0]["stale_since_tick"] == _REPORT_TICK


def test_snapshot_posture_is_null_for_god_view(
    client: TestClient, world: OrderWorld, fake_redis: FakeStrictRedis
) -> None:
    _offline_blue(fake_redis, world)
    assert _get(client, world, "state", _white(world))["comms_posture"] is None
