"""禁射區 G4 護欄（WP-A3）——釘住「G4 真的會攔」與兩種級別的處置差異。

改版前 G4 形同虛設：只比對令面自帶的 `target_h3`，而 AI 的 ENGAGE 令帶的是 `target_unit_id`
→ 永遠對不上；且 `no_strike_hexes` 恆為空（無資料源）。本測試釘住修好後的行為：
1. 幾何（polygon/circle）→ h3 格集，含座標順序（GeoJSON [lng,lat] vs h3 (lat,lng)）不可寫反；
2. G4 以**目標實際位置**判定（unit_id 與 lat/lng 兩種表達都要攔得到）；
3. NO_STRIKE 硬擋、RESTRICTED_FIRE 只升白軍（令保留）；
4. MOVE 不受禁射區約束（規格：開進去不違規，打進去才是）；
5. 人類下令：NO_STRIKE 一律拒；RESTRICTED_FIRE 未確認拒、確認後放行。
"""

from __future__ import annotations

from typing import Any

import h3
import pytest
from _order_fakes import FakeGateway, seed_world
from sqlalchemy.orm import Session, sessionmaker

from app.errors import PrecheckFailedError
from app.guardrails import GuardrailGateway
from app.guardrails.gateway import _ZONE_NO_STRIKE, _ZONE_RESTRICTED
from app.models.enums import AiMode
from app.models.tables import MapFeature, TacticalUnit, WargameSession
from app.orders.no_strike import (
    NO_STRIKE_H3_RES,
    ZoneClass,
    load_no_strike_cells,
    zones_to_cells,
)
from app.orders.schemas import OrderRequest, OrderType
from app.orders.service import OrderService

# 台灣本島陸地上的一點（與 _order_fakes 的種子世界同區）。
_LAT, _LNG = 23.76, 121.26


def _decision(orders: list[dict[str, Any]]) -> dict[str, Any]:
    """通得過 G1（schema：intent/ihl_self_check/chain ≥80 字）與 G2（≥3 步）的最小輸出。"""
    chain = "\n".join(
        [
            "1. 評估當面敵情與我方可用火力，確認接觸線位置與雙方態勢的相對優劣。",
            "2. 比較各可行方案的風險與收益，選擇最能達成任務且我方損失可控的一案。",
            "3. 下達交戰命令，指定目標、武器與火力政策，並要求回報效果。",
        ]
    )
    return {
        "reasoning_chain": chain,
        "confidence": 0.8,
        "intent": "壓制當面之敵",
        "ihl_self_check": {"civilian_risk_assessed": True},
        "orders": orders,
    }


class _StubLocator:
    """把 target_unit_id / target_lat+lng 解析成格（模擬 UnitTargetLocator，不需 DB）。"""

    def __init__(self, by_unit: dict[str, tuple[float, float]]) -> None:
        self._by_unit = by_unit

    def locate(self, order: dict[str, Any]) -> str | None:
        uid = order.get("target_unit_id")
        if isinstance(uid, str) and uid in self._by_unit:
            lat, lng = self._by_unit[uid]
            return str(h3.latlng_to_cell(lat, lng, NO_STRIKE_H3_RES))
        lat, lng = order.get("target_lat"), order.get("target_lng")
        if isinstance(lat, int | float) and isinstance(lng, int | float):
            return str(h3.latlng_to_cell(float(lat), float(lng), NO_STRIKE_H3_RES))
        return None


# ---- 幾何 → 格集 ----


def test_polygon_zone_covers_its_interior() -> None:
    ring = [[121.25, 23.75], [121.28, 23.75], [121.28, 23.78], [121.25, 23.78]]
    cells = zones_to_cells(
        [{"name": "A", "zone_class": "NO_STRIKE", "geometry": {"type": "polygon", "ring": ring}}]
    )
    inside = h3.latlng_to_cell(23.765, 121.265, NO_STRIKE_H3_RES)
    assert inside in cells.no_strike
    assert cells.classify(inside) is ZoneClass.NO_STRIKE


def test_coordinate_order_is_not_flipped() -> None:
    """[lng,lat] 寫反成 [lat,lng] 會讓整個區跑到地球另一端——這條測試就是防這個。"""
    ring = [[121.25, 23.75], [121.28, 23.75], [121.28, 23.78], [121.25, 23.78]]
    cells = zones_to_cells(
        [{"name": "A", "zone_class": "NO_STRIKE", "geometry": {"type": "polygon", "ring": ring}}]
    )
    # 台灣的格在集合裡；把經緯對調後的位置（沙烏地阿拉伯外海附近）不得在集合裡。
    assert h3.latlng_to_cell(23.76, 121.26, NO_STRIKE_H3_RES) in cells.no_strike
    assert h3.latlng_to_cell(121.26, 23.76, NO_STRIKE_H3_RES) not in cells.no_strike


def test_circle_zone_covers_center_and_excludes_far_point() -> None:
    zones = [
        {
            "name": "醫院",
            "zone_class": "NO_STRIKE",
            "geometry": {"type": "circle", "center": [_LNG, _LAT], "radius_m": 1200},
        }
    ]
    cells = zones_to_cells(zones)
    assert h3.latlng_to_cell(_LAT, _LNG, NO_STRIKE_H3_RES) in cells.no_strike
    assert h3.latlng_to_cell(_LAT + 0.5, _LNG + 0.5, NO_STRIKE_H3_RES) not in cells.no_strike


def test_restricted_and_no_strike_are_kept_apart() -> None:
    zones = [
        {
            "name": "H",
            "zone_class": "NO_STRIKE",
            "geometry": {"type": "circle", "center": [_LNG, _LAT], "radius_m": 500},
        },
        {
            "name": "R",
            "zone_class": "RESTRICTED_FIRE",
            "geometry": {"type": "circle", "center": [_LNG + 0.2, _LAT], "radius_m": 500},
        },
    ]
    cells = zones_to_cells(zones)
    assert cells.classify_latlng(_LAT, _LNG) is ZoneClass.NO_STRIKE
    assert cells.classify_latlng(_LAT, _LNG + 0.2) is ZoneClass.RESTRICTED_FIRE
    assert not (cells.no_strike & cells.restricted)  # 不重疊


def test_unknown_zone_class_defaults_to_hard_block() -> None:
    """打錯字的級別一律從嚴——安全機制的預設不能是「放行」。"""
    zones = [
        {
            "name": "X",
            "zone_class": "TYPO",
            "geometry": {"type": "circle", "center": [_LNG, _LAT], "radius_m": 300},
        }
    ]
    assert zones_to_cells(zones).classify_latlng(_LAT, _LNG) is ZoneClass.NO_STRIKE


def test_broken_zone_does_not_disable_the_others() -> None:
    zones = [
        "not-a-dict",
        {"name": "壞的", "zone_class": "NO_STRIKE", "geometry": {"type": "polygon"}},  # 缺 ring
        {
            "name": "好的",
            "zone_class": "NO_STRIKE",
            "geometry": {"type": "circle", "center": [_LNG, _LAT], "radius_m": 400},
        },
    ]
    assert zones_to_cells(zones).classify_latlng(_LAT, _LNG) is ZoneClass.NO_STRIKE


def test_zone_class_literals_match_the_guardrail_side() -> None:
    """guardrails 刻意不 import 本模組（維持零 DB），兩處字面值必須一致。"""
    assert ZoneClass.NO_STRIKE.value == _ZONE_NO_STRIKE
    assert ZoneClass.RESTRICTED_FIRE.value == _ZONE_RESTRICTED


# ---- 資料源（DB） ----


def test_zones_load_from_session_and_map_features(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    with session_factory() as db:
        session = db.get(WargameSession, world.session_id)
        assert session is not None
        session.no_strike_zones = [
            {
                "name": "醫院",
                "zone_class": "NO_STRIKE",
                "geometry": {"type": "circle", "center": [_LNG, _LAT], "radius_m": 600},
            }
        ]
        # 白軍在 COP 圈的限制射擊區（走 MapFeature.attributes.zone_class）
        db.add(
            MapFeature(
                session_id=world.session_id,
                kind="CONTROL_MEASURE",
                geometry_type="POLYGON",
                geometry=[[121.30, 23.80], [121.33, 23.80], [121.33, 23.83], [121.30, 23.83]],
                owner_faction="WHITE_CELL",
                label="文化資產",
                attributes={"zone_class": "RESTRICTED_FIRE"},
            )
        )
        db.commit()
        cells = load_no_strike_cells(db, world.session_id)
    assert cells.classify_latlng(_LAT, _LNG) is ZoneClass.NO_STRIKE
    assert cells.classify_latlng(23.815, 121.315) is ZoneClass.RESTRICTED_FIRE


def test_session_without_zones_blocks_nothing(session_factory: sessionmaker[Session]) -> None:
    """既有局（無宣告）零行為變更。"""
    world = seed_world(session_factory)
    with session_factory() as db:
        assert not load_no_strike_cells(db, world.session_id).any_cells


# ---- G4 護欄 ----


@pytest.mark.parametrize("by_unit_id", [True, False])
def test_g4_blocks_strike_on_no_strike_zone_by_either_target_form(by_unit_id: bool) -> None:
    """規格明列：`target_unit_id` 與 `target_lat/lng` 兩種表達都要攔得到。"""
    cell = h3.latlng_to_cell(_LAT, _LNG, NO_STRIKE_H3_RES)
    order: dict[str, Any] = {"unit_id": "b1", "order_type": "ENGAGE"}
    if by_unit_id:
        order["target_unit_id"] = "r1"
    else:
        order["target_lat"], order["target_lng"] = _LAT, _LNG
    outcome = GuardrailGateway().evaluate(
        _decision([order]),
        schema_ref="opfor_decision",
        mode=AiMode.AI_BARE,
        no_strike_hexes=frozenset({cell}),
        target_locator=_StubLocator({"r1": (_LAT, _LNG)}),
    )
    assert outcome.accepted is False  # 硬阻擋 → 整批不接受
    assert outcome.escalate_white_cell is True
    assert (outcome.sanitized or {}).get("orders") == []  # 違規令已被剔除
    g4 = [f for f in outcome.interventions if f.check == "G4"]
    assert g4 and g4[0].detail["zone_class"] == _ZONE_NO_STRIKE


def test_g4_keeps_restricted_fire_orders_but_escalates() -> None:
    cell = h3.latlng_to_cell(_LAT, _LNG, NO_STRIKE_H3_RES)
    outcome = GuardrailGateway().evaluate(
        _decision([{"unit_id": "b1", "order_type": "ENGAGE", "target_unit_id": "r1"}]),
        schema_ref="opfor_decision",
        mode=AiMode.AI_BARE,
        restricted_fire_hexes=frozenset({cell}),
        target_locator=_StubLocator({"r1": (_LAT, _LNG)}),
    )
    assert outcome.accepted is True  # 不硬擋
    assert outcome.escalate_white_cell is True  # 但要白軍確認
    assert len((outcome.sanitized or {}).get("orders", [])) == 1  # 令保留
    assert any(f.detail.get("zone_class") == _ZONE_RESTRICTED for f in outcome.interventions)


def test_g4_does_not_block_movement_into_a_zone() -> None:
    """規格：開進禁射區不是違規，打進去才是。"""
    cell = h3.latlng_to_cell(_LAT, _LNG, NO_STRIKE_H3_RES)
    outcome = GuardrailGateway().evaluate(
        _decision(
            [{"unit_id": "b1", "order_type": "MOVE", "target_lat": _LAT, "target_lng": _LNG}]
        ),
        schema_ref="opfor_decision",
        mode=AiMode.AI_BARE,
        no_strike_hexes=frozenset({cell}),
        target_locator=_StubLocator({}),
    )
    assert outcome.accepted is True
    assert len((outcome.sanitized or {}).get("orders", [])) == 1


def test_g4_passes_when_target_cannot_be_located() -> None:
    """定位不到不擋——寧漏擋也不誤殺合法令（真正的把關在 submit 端 precheck）。"""
    outcome = GuardrailGateway().evaluate(
        _decision([{"unit_id": "b1", "order_type": "ENGAGE", "target_unit_id": "unknown"}]),
        schema_ref="opfor_decision",
        mode=AiMode.AI_BARE,
        no_strike_hexes=frozenset({h3.latlng_to_cell(_LAT, _LNG, NO_STRIKE_H3_RES)}),
        target_locator=_StubLocator({}),
    )
    assert outcome.accepted is True


# ---- 人類下令路徑 ----


def _engage(world: Any, ack: bool = False) -> OrderRequest:
    return OrderRequest(
        unit_id=world.blue_unit_id,
        order_type=OrderType.ENGAGE,
        payload={"target_unit_id": world.red_unit_id},
        acknowledge_restricted=ack,
    )


def _set_zone(
    factory: sessionmaker[Session], session_id: str, unit_id: str, zone_class: str
) -> None:
    with factory() as db:
        target = db.get(TacticalUnit, unit_id)
        assert target is not None and target.current_lat is not None
        session = db.get(WargameSession, session_id)
        assert session is not None
        session.no_strike_zones = [
            {
                "name": "保護區",
                "zone_class": zone_class,
                "geometry": {
                    "type": "circle",
                    "center": [float(target.current_lng or 0), float(target.current_lat)],
                    "radius_m": 500,
                },
            }
        ]
        db.commit()


def test_human_engage_into_no_strike_zone_is_rejected(
    session_factory: sessionmaker[Session],
) -> None:
    world = seed_world(session_factory)
    _set_zone(session_factory, world.session_id, world.red_unit_id, "NO_STRIKE")
    with session_factory() as db:
        service = OrderService(db, FakeGateway())
        with pytest.raises(PrecheckFailedError) as err:
            service.submit(world.session_id, _engage(world), world.blue_issuer_id)
    assert err.value.error_code == "ORDER_NO_STRIKE_ZONE"


def test_no_strike_cannot_be_overridden(session_factory: sessionmaker[Session]) -> None:
    """NO_STRIKE 是硬規則——勾了確認也不放行（只有 RESTRICTED_FIRE 可 override）。"""
    world = seed_world(session_factory)
    _set_zone(session_factory, world.session_id, world.red_unit_id, "NO_STRIKE")
    with session_factory() as db:
        service = OrderService(db, FakeGateway())
        with pytest.raises(PrecheckFailedError):
            service.submit(world.session_id, _engage(world, ack=True), world.blue_issuer_id)


def test_restricted_fire_requires_explicit_acknowledgement(
    session_factory: sessionmaker[Session],
) -> None:
    world = seed_world(session_factory)
    _set_zone(session_factory, world.session_id, world.red_unit_id, "RESTRICTED_FIRE")
    with session_factory() as db:
        service = OrderService(db, FakeGateway())
        with pytest.raises(PrecheckFailedError) as err:  # 未確認 → 拒
            service.submit(world.session_id, _engage(world), world.blue_issuer_id)
        assert err.value.error_code == "ORDER_NO_STRIKE_ZONE"

    with session_factory() as db:  # 明確確認 → 放行
        service = OrderService(db, FakeGateway())
        resp = service.submit(world.session_id, _engage(world, ack=True), world.blue_issuer_id)
    assert resp.status == "VALIDATED"


# ---- 想定載入 / roundtrip ----


def test_scenario_bundle_loads_and_persists_zones(session_factory: sessionmaker[Session]) -> None:
    """想定宣告的禁射區要落到 session（否則 schema 白宣告、執行期讀不到）。"""
    from app.scenario.loader import create_session_from_scenario, load_scenario_bundle

    zone = {
        "name": "市立醫院",
        "zone_class": "NO_STRIKE",
        "geometry": {"type": "circle", "center": [_LNG, _LAT], "radius_m": 700},
    }
    bundle = {
        "scenario": {
            "name": "t",
            "version": "1",
            "bbox": [121.0, 23.0, 122.0, 24.0],
            "mode": "REALTIME",
            "tick_rate_ms": 1000,
            "factions": [{"id": "BLUE"}, {"id": "RED"}],
            "victory_conditions": [
                {"faction": "BLUE", "condition": {"type": "faction_eliminated", "faction": "RED"}}
            ],
            "no_strike_zones": [zone],
        },
        "orbat": {},
    }
    loaded = load_scenario_bundle(bundle)
    assert loaded.no_strike_zones == [zone]

    with session_factory() as db:
        summary = create_session_from_scenario(db, loaded, master_seed=1)
        session = db.get(WargameSession, summary if isinstance(summary, str) else summary.id)
        assert session is not None
        assert session.no_strike_zones == [zone]
        cells = load_no_strike_cells(db, session.id)
    assert cells.classify_latlng(_LAT, _LNG) is ZoneClass.NO_STRIKE


def test_zone_that_covers_no_cell_is_rejected_at_load() -> None:
    """幾何算不出任何格 → 拒絕載入。悄悄放行等於讓作者以為保護了醫院，實際上沒有。"""
    from app.scenario.loader import ScenarioError, load_scenario_bundle

    bundle = {
        "scenario": {
            "name": "t",
            "version": "1",
            "bbox": [121.0, 23.0, 122.0, 24.0],
            "mode": "REALTIME",
            "tick_rate_ms": 1000,
            "factions": [{"id": "BLUE"}],
            "victory_conditions": [
                {"faction": "BLUE", "condition": {"type": "faction_eliminated", "faction": "RED"}}
            ],
            "no_strike_zones": [
                {"name": "壞的", "zone_class": "NO_STRIKE", "geometry": {"type": "polygon"}}
            ],
        },
        "orbat": {},
    }
    with pytest.raises(ScenarioError, match="no_strike_zones"):
        load_scenario_bundle(bundle)


def test_zones_survive_export_import_roundtrip() -> None:
    """匯出再匯入不得掉保護區（`fixed` 旗標曾遺失的同類前例）。"""
    from app.scenario.dump import scenario_to_dict
    from app.scenario.loader import load_scenario_bundle

    zone = {
        "name": "文化資產",
        "zone_class": "RESTRICTED_FIRE",
        "geometry": {"type": "circle", "center": [_LNG, _LAT], "radius_m": 500},
    }
    scenario = {
        "name": "t",
        "version": "1",
        "bbox": [121.0, 23.0, 122.0, 24.0],
        "mode": "REALTIME",
        "tick_rate_ms": 1000,
        "factions": [{"id": "BLUE"}],
        "victory_conditions": [
            {"faction": "BLUE", "condition": {"type": "faction_eliminated", "faction": "RED"}}
        ],
        "no_strike_zones": [zone],
    }
    loaded = load_scenario_bundle({"scenario": scenario, "orbat": {}})
    exported = scenario_to_dict(loaded)
    assert exported["no_strike_zones"] == [zone]
    again = load_scenario_bundle({"scenario": exported, "orbat": {}})
    assert again.no_strike_zones == [zone]


# ---- 面目標射擊也要過禁射區（Backlog 清理；缺口自 WP-C10.2 就存在）----

_HOWITZER_NS = {
    "max_range_m": 25000,
    "ph_by_range_band": [[25000, 0.5]],
    "damage_by_armor_class": {"INFANTRY": 60},
    "ammo_types": ["HE"],
    "indirect_fire": True,
    "dispersion_cep_m": 100,
    "lethal_radius_m": 50,
}


def _give_howitzer_ns(factory: sessionmaker[Session], unit_id: str) -> None:
    from app.models.tables import EquipmentInstance, EquipmentTemplate

    with factory() as db:
        t = EquipmentTemplate(name="M109-ns", category="ARTILLERY", base_stats=_HOWITZER_NS)
        db.add(t)
        db.flush()
        db.add(EquipmentInstance(template_id=t.id, owner_id=unit_id, current_state={"ammo": 40}))
        db.commit()


def _fire_mission_at(world: Any, lat: float, lng: float, ack: bool = False) -> OrderRequest:
    return OrderRequest(
        unit_id=world.blue_unit_id,
        order_type=OrderType.FIRE_MISSION,
        payload={"target_lat": lat, "target_lng": lng, "rounds": 2},
        acknowledge_restricted=ack,
    )


def _red_pos(factory: sessionmaker[Session], unit_id: str) -> tuple[float, float]:
    with factory() as db:
        u = db.get(TacticalUnit, unit_id)
        assert u is not None and u.current_lat is not None and u.current_lng is not None
        return float(u.current_lat), float(u.current_lng)


def test_fire_mission_into_a_no_strike_zone_is_rejected(
    session_factory: sessionmaker[Session],
) -> None:
    """**同一個座標，ENGAGE 打不了、FIRE_MISSION 卻可以——那不是保護，是繞道。**

    禁射區保護的是那塊地，不是站在上面的人。
    """
    world = seed_world(session_factory)
    _give_howitzer_ns(session_factory, world.blue_unit_id)
    _set_zone(session_factory, world.session_id, world.red_unit_id, "NO_STRIKE")
    lat, lng = _red_pos(session_factory, world.red_unit_id)
    with session_factory() as db:
        service = OrderService(db, FakeGateway())
        with pytest.raises(PrecheckFailedError) as err:
            service.submit(
                world.session_id, _fire_mission_at(world, lat, lng), world.blue_issuer_id
            )
    assert err.value.error_code == "ORDER_NO_STRIKE_ZONE"


def test_fire_mission_outside_the_zone_is_fine(session_factory: sessionmaker[Session]) -> None:
    """保護區只保護那一塊——旁邊照打，否則整個功能等於關掉火力。"""
    world = seed_world(session_factory)
    _give_howitzer_ns(session_factory, world.blue_unit_id)
    _set_zone(session_factory, world.session_id, world.red_unit_id, "NO_STRIKE")
    lat, lng = _red_pos(session_factory, world.red_unit_id)
    with session_factory() as db:
        resp = OrderService(db, FakeGateway()).submit(
            world.session_id, _fire_mission_at(world, lat + 0.02, lng), world.blue_issuer_id
        )
    assert resp.status == "VALIDATED"


def test_fire_mission_into_restricted_fire_needs_acknowledgement(
    session_factory: sessionmaker[Session],
) -> None:
    """限制射擊區：與 ENGAGE 同一條規則——明確確認才放行。"""
    world = seed_world(session_factory)
    _give_howitzer_ns(session_factory, world.blue_unit_id)
    _set_zone(session_factory, world.session_id, world.red_unit_id, "RESTRICTED_FIRE")
    lat, lng = _red_pos(session_factory, world.red_unit_id)
    with session_factory() as db, pytest.raises(PrecheckFailedError):
        OrderService(db, FakeGateway()).submit(
            world.session_id, _fire_mission_at(world, lat, lng), world.blue_issuer_id
        )
    with session_factory() as db:
        resp = OrderService(db, FakeGateway()).submit(
            world.session_id,
            _fire_mission_at(world, lat, lng, ack=True),
            world.blue_issuer_id,
        )
    assert resp.status == "VALIDATED"
