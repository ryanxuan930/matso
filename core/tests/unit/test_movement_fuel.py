"""#84 油料：惰性滿油、依實際位移扣油、油盡停駛（MOVE_HALTED_FUEL）、徒步不受限。"""

from __future__ import annotations

import asyncio

import h3
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.engine.clock import SimClock
from app.engine.movement import UnitMovementSystem
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
from app.movement.fuel import burn_fuel, load_unit_fuel
from app.state.hot_state import InMemoryHotState

_START = (23.75, 121.20)
_DEST = (23.75, 121.60)  # 正東約 41km（足以燒乾小油箱）


def _veh(capacity: float, burn: float) -> dict:
    return {
        "can_self_move": True,
        "mobility_class": "TRACKED",
        "max_road_speed_kmh": 70,
        "max_cross_country_speed_kmh": 45,
        "fuel_capacity": capacity,
        "fuel_burn_per_km": burn,
    }


def _seed(db: Session, sid: str, *, vehicle: dict | None, fuel: float | None = None) -> str:
    db.add(WargameSession(id=sid, name="x", master_seed=1, current_weather={}))
    db.flush()
    unit = TacticalUnit(
        session_id=sid,
        designation="B1",
        unit_level=UnitLevel.PLATOON,
        faction="BLUE",
        current_lat=_START[0],
        current_lng=_START[1],
        authorized_strength=100.0,
        current_strength=100.0,
    )
    db.add(unit)
    db.flush()
    if vehicle is not None:
        tmpl = EquipmentTemplate(
            name=f"veh-{sid}", category="VEHICLE", base_stats={"mobility": vehicle}
        )
        db.add(tmpl)
        db.flush()
        state = {} if fuel is None else {"fuel": fuel}
        db.add(EquipmentInstance(template_id=tmpl.id, owner_id=unit.id, current_state=state))
    db.add(
        Order(
            session_id=sid,
            issuer_id="u1",
            unit_id=unit.id,
            order_type="MOVE",
            payload={
                "to_h3": h3.latlng_to_cell(_DEST[0], _DEST[1], 8),
                "to_lat": _DEST[0],
                "to_lng": _DEST[1],
                "mobility_profile": "FOOT",
            },
            status=OrderStatus.VALIDATED,
            issued_at_tick=0,
        )
    )
    return unit.id


def _run(factory: sessionmaker[Session], sid: str, ticks: int) -> list:
    mover = UnitMovementSystem(
        session_id=sid,
        session_factory=factory,
        hot_state=InMemoryHotState(),
        tick_rate_ms=60_000,
        rng=DeterministicRNG(1, "movement"),
    )
    clock = SimClock(tick_rate_ms=60_000)
    events = []
    for _ in range(ticks):
        events.extend(asyncio.run(mover.step(clock.now())))
        clock.advance()
    return events


# ---- 油箱讀寫（純邏輯）----


def test_lazy_full_tank(session_factory: sessionmaker[Session]) -> None:
    """instance 尚無 fuel 鍵 → 視為滿油（免額外 seed pass）。"""
    with session_factory() as db:
        uid = _seed(db, "lazy", vehicle=_veh(1000, 2.0))
        db.commit()
    with session_factory() as db:
        f = load_unit_fuel(db, uid)
        assert f.needs_fuel is True
        assert f.remaining == 1000.0 and f.capacity == 1000.0
        assert f.range_km() == 500.0  # 1000 / 2.0


def test_burn_writes_back_and_floors_at_zero(session_factory: sessionmaker[Session]) -> None:
    with session_factory() as db:
        uid = _seed(db, "burn", vehicle=_veh(100, 2.0), fuel=100.0)
        db.commit()
    with session_factory() as db:
        f = load_unit_fuel(db, uid)
        assert burn_fuel(f, 10.0) == 20.0  # 10km × 2.0
        assert f.remaining == 80.0
        db.commit()
    with session_factory() as db:
        assert load_unit_fuel(db, uid).remaining == 80.0  # 已寫回 DB
        f = load_unit_fuel(db, uid)
        burn_fuel(f, 999.0)  # 遠超剩餘
        assert f.remaining == 0.0  # 扣到 0 不為負


def test_foot_unit_needs_no_fuel(session_factory: sessionmaker[Session]) -> None:
    with session_factory() as db:
        uid = _seed(db, "foot", vehicle=None)
        db.commit()
    with session_factory() as db:
        f = load_unit_fuel(db, uid)
        assert f.needs_fuel is False and f.range_km() == float("inf")


# ---- 執行器整合 ----


def test_vehicle_burns_fuel_while_moving(session_factory: sessionmaker[Session]) -> None:
    with session_factory() as db:
        uid = _seed(db, "moving", vehicle=_veh(1000, 2.0))
        db.commit()
    _run(session_factory, "moving", 5)
    with session_factory() as db:
        f = load_unit_fuel(db, uid)
        assert 0 < f.remaining < 1000.0  # 已消耗但未乾


def test_runs_dry_and_halts(session_factory: sessionmaker[Session]) -> None:
    """油量僅夠走一小段 → 途中耗盡 → HALTED_FUEL + 本令結束（未抵達目的地）。"""
    with session_factory() as db:
        uid = _seed(db, "dry", vehicle=_veh(1000, 2.0), fuel=4.0)  # 4 油 / 2.0 → 僅 2km
        db.commit()
    events = _run(session_factory, "dry", 30)
    halted = [e for e in events if e.event_type == "MOVE_HALTED_FUEL"]
    assert len(halted) == 1
    assert halted[0].detail["reason"] == "OUT_OF_FUEL"
    with session_factory() as db:
        o = db.execute(select(Order).where(Order.session_id == "dry")).scalars().first()
        assert o.status == OrderStatus.COMPLETED  # 令結束（停駛）
        u = db.get(TacticalUnit, uid)
        assert abs(u.current_lng - _DEST[1]) > 0.1  # 遠未抵達
        assert load_unit_fuel(db, uid).remaining == 0.0


def test_empty_tank_does_not_depart(session_factory: sessionmaker[Session]) -> None:
    """出發前即無油 → 不出發（原地）+ HALTED_FUEL。"""
    with session_factory() as db:
        uid = _seed(db, "empty", vehicle=_veh(1000, 2.0), fuel=0.0)
        db.commit()
    events = _run(session_factory, "empty", 3)
    assert [e.event_type for e in events].count("MOVE_HALTED_FUEL") == 1
    with session_factory() as db:
        u = db.get(TacticalUnit, uid)
        assert (u.current_lat, u.current_lng) == _START  # 完全未移動


def test_foot_unit_unaffected_by_fuel(session_factory: sessionmaker[Session]) -> None:
    """徒步單位無載具 → 不受油料限制，照常前進、無 HALTED_FUEL。"""
    with session_factory() as db:
        uid = _seed(db, "footmove", vehicle=None)
        db.commit()
    events = _run(session_factory, "footmove", 5)
    assert not [e for e in events if e.event_type == "MOVE_HALTED_FUEL"]
    with session_factory() as db:
        u = db.get(TacticalUnit, uid)
        assert u.current_lng > _START[1]  # 有前進


def test_ai_context_shows_remaining_range() -> None:
    """#84：AI briefing 標「剩餘行程 N km」→ LLM 不會下超出油料的長程移動。"""
    from app.ai_loop.context import UnitMeta, _fmt_own, _own_unit_view

    meta = UnitMeta(
        faction="BLUE",
        designation="M1",
        echelon="PLATOON",
        mobility_profile="TRACKED",
        speed_kmh=45.0,
        range_km=34.0,
    )
    view = _own_unit_view("u1", {"lat": 23.75, "lng": 121.2, "strength": 100.0}, meta)
    assert view["range_km"] == 34.0 and view["mobility"] == "TRACKED"
    assert "剩餘行程 34.0km" in _fmt_own(view)


def test_ai_context_omits_range_for_foot() -> None:
    """徒步單位無油料限制 → 不顯示剩餘行程（避免誤導 LLM）。"""
    from app.ai_loop.context import UnitMeta, _fmt_own, _own_unit_view

    meta = UnitMeta(faction="BLUE", designation="F1", echelon="SQUAD", speed_kmh=5.0, range_km=None)
    view = _own_unit_view("u2", {"lat": 23.75, "lng": 121.2}, meta)
    assert "range_km" not in view
    assert "剩餘行程" not in _fmt_own(view)
