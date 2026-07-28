"""#80 Phase A：機動能力導出 + per-unit 移動速度（機械化 vs 徒步）+ 行軍耗損。"""

from __future__ import annotations

import asyncio

from sqlalchemy.orm import Session, sessionmaker

from app.engine.clock import SimClock
from app.engine.movement import UnitMovementSystem, _haversine_km
from app.engine.rng import DeterministicRNG
from app.models import (
    EquipmentInstance,
    EquipmentTemplate,
    Order,
    OrderStatus,
    TacticalUnit,
    UnitLevel,
    WargameSession,
)
from app.movement.mobility import FOOT, mobility_from_stats, resolve_unit_mobility
from app.state.hot_state import InMemoryHotState

_IFV = {
    "can_self_move": True,
    "mobility_class": "TRACKED",
    "max_road_speed_kmh": 65,
    "max_cross_country_speed_kmh": 40,
}
_TRUCK = {
    "can_self_move": True,
    "mobility_class": "WHEELED",
    "max_road_speed_kmh": 85,
    "max_cross_country_speed_kmh": 40,
}
_MANPACK = {"can_self_move": False, "mobility_class": "MAN_PORTABLE"}


# ---- 純函數：由編裝 base_stats 導出 profile ----


def test_no_vehicle_is_foot() -> None:
    assert mobility_from_stats([]) == FOOT
    assert mobility_from_stats([{"mobility": _MANPACK}]) == FOOT
    assert mobility_from_stats([{"foo": 1}]) == FOOT  # 無 mobility 區塊


def test_tracked_and_wheeled() -> None:
    tr = mobility_from_stats([{"mobility": _IFV}])
    assert tr.profile == "TRACKED" and tr.xc_kmh == 40 and tr.road_kmh == 65
    wh = mobility_from_stats([{"mobility": _TRUCK}])
    assert wh.profile == "WHEELED" and wh.xc_kmh == 40 and wh.road_kmh == 85


def test_prefers_tracked_over_wheeled() -> None:
    m = mobility_from_stats([{"mobility": _TRUCK}, {"mobility": _IFV}])
    assert m.profile == "TRACKED"


def test_convoy_limited_to_slowest_of_class() -> None:
    slow = {**_IFV, "max_cross_country_speed_kmh": 25, "max_road_speed_kmh": 45}
    m = mobility_from_stats([{"mobility": _IFV}, {"mobility": slow}])
    assert m.profile == "TRACKED" and m.xc_kmh == 25 and m.road_kmh == 45  # 取最慢


# ---- DB：resolve_unit_mobility 讀單位編裝 ----


def _seed_unit(db: Session, sid: str, desig: str, *, vehicle: dict | None) -> str:
    unit = TacticalUnit(
        session_id=sid,
        designation=desig,
        unit_level=UnitLevel.PLATOON,
        faction="BLUE",
        current_lat=23.75,
        current_lng=121.20,
        authorized_strength=100.0,
        current_strength=100.0,
    )
    db.add(unit)
    db.flush()
    if vehicle is not None:
        tmpl = EquipmentTemplate(
            name=f"veh-{desig}", category="VEHICLE", base_stats={"mobility": vehicle}
        )
        db.add(tmpl)
        db.flush()
        db.add(EquipmentInstance(template_id=tmpl.id, owner_id=unit.id, current_state={}))
    return unit.id


def test_resolve_reads_equipment(session_factory: sessionmaker[Session]) -> None:
    sid = "m-sess"
    with session_factory() as db:
        db.add(WargameSession(id=sid, name="x", master_seed=1, current_weather={}))
        db.flush()
        foot_id = _seed_unit(db, sid, "F1", vehicle=None)
        mech_id = _seed_unit(db, sid, "M1", vehicle=_IFV)
        db.commit()
    with session_factory() as db:
        assert resolve_unit_mobility(db, foot_id).profile == "FOOT"
        mech = resolve_unit_mobility(db, mech_id)
        assert mech.profile == "TRACKED" and mech.xc_kmh == 40


# ---- 執行器：機械化比徒步快（headline 驗收）----


def _order(db: Session, sid: str, uid: str, to_lat: float, to_lng: float) -> None:
    import h3

    db.add(
        Order(
            session_id=sid,
            issuer_id="u1",
            unit_id=uid,
            order_type="MOVE",
            payload={
                "to_h3": h3.latlng_to_cell(to_lat, to_lng, 8),
                "to_lat": to_lat,
                "to_lng": to_lng,
                "mobility_profile": "FOOT",
            },
            status=OrderStatus.VALIDATED,
            issued_at_tick=0,
        )
    )


def test_mechanized_moves_faster_than_foot(session_factory: sessionmaker[Session]) -> None:
    sid = "eta-sess"
    with session_factory() as db:
        db.add(WargameSession(id=sid, name="x", master_seed=1, current_weather={}))
        db.flush()
        foot_id = _seed_unit(db, sid, "F1", vehicle=None)
        mech_id = _seed_unit(db, sid, "M1", vehicle=_IFV)
        # 同一目的地（正東約 3km）。
        _order(db, sid, foot_id, 23.75, 121.23)
        _order(db, sid, mech_id, 23.75, 121.23)
        db.commit()
    mover = UnitMovementSystem(
        session_id=sid,
        session_factory=session_factory,
        hot_state=InMemoryHotState(),
        tick_rate_ms=60_000,
        rng=DeterministicRNG(1, "movement"),
    )
    clock = SimClock(tick_rate_ms=60_000)
    for _ in range(10):
        asyncio.run(mover.step(clock.now()))
        clock.advance()
    with session_factory() as db:
        foot = db.get(TacticalUnit, foot_id)
        mech = db.get(TacticalUnit, mech_id)
        start = (23.75, 121.20)
        foot_dist = _haversine_km(start[0], start[1], foot.current_lat, foot.current_lng)
        mech_dist = _haversine_km(start[0], start[1], mech.current_lat, mech.current_lng)
        # 機械化（越野 40km/h）10 tick 走遠比徒步（5km/h）多——約 8×。
        assert mech_dist > foot_dist * 3


def test_march_attrition_recorded_and_scales(session_factory: sessionmaker[Session]) -> None:
    sid = "march-sess"
    with session_factory() as db:
        db.add(WargameSession(id=sid, name="x", master_seed=1, current_weather={}))
        db.flush()
        uid = _seed_unit(db, sid, "F1", vehicle=None)
        _order(db, sid, uid, 23.75, 121.30)  # 遠（~10km）徒步
        db.commit()
    mover = UnitMovementSystem(
        session_id=sid,
        session_factory=session_factory,
        hot_state=InMemoryHotState(),
        tick_rate_ms=60_000,
        rng=DeterministicRNG(1, "movement"),
    )
    events = asyncio.run(mover.step(SimClock(tick_rate_ms=60_000).now()))
    march = [
        e for e in events if e.event_type == "MOVE_ATTRITION" and e.detail["reason"] == "MARCH"
    ]
    assert len(march) == 1
    assert march[0].detail["profile"] == "FOOT"
    assert march[0].damage_calc > 0  # 遠程徒步行軍有耗損
    with session_factory() as db:
        u = db.get(TacticalUnit, uid)
        assert u.current_strength < 100.0
