"""#85 補給：RESUPPLY 令加油、距離閘門、只補自軍、載運耗盡；**拋錨單位可救回**。"""

from __future__ import annotations

import asyncio

import h3
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.engine.clock import SimClock
from app.engine.logistics import ResupplySystem
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
from app.movement.fuel import load_unit_fuel
from app.state.hot_state import InMemoryHotState

_SID = "sup"
_POS = (23.75, 121.20)

_TANK_VEH = {
    "can_self_move": True,
    "mobility_class": "TRACKED",
    "max_road_speed_kmh": 70,
    "max_cross_country_speed_kmh": 45,
    "fuel_capacity": 1000,
    "fuel_burn_per_km": 2.0,
}
_TRUCK_LOG = {
    "capacity": {"FUEL": 5000},
    "resupply_rate_per_tick": 400,
    "mobility": {
        "can_self_move": True,
        "mobility_class": "WHEELED",
        "max_road_speed_kmh": 80,
        "max_cross_country_speed_kmh": 30,
        "fuel_capacity": 300,
        "fuel_burn_per_km": 0.5,
    },
}


def _unit(db: Session, desig: str, faction: str, pos: tuple[float, float]) -> TacticalUnit:
    u = TacticalUnit(
        session_id=_SID,
        designation=desig,
        unit_level=UnitLevel.PLATOON,
        faction=faction,
        current_lat=pos[0],
        current_lng=pos[1],
        authorized_strength=100.0,
        current_strength=100.0,
    )
    db.add(u)
    db.flush()
    return u


def _equip(
    db: Session, unit: TacticalUnit, name: str, stats: dict, state: dict | None = None
) -> None:
    tmpl = db.execute(
        select(EquipmentTemplate).where(EquipmentTemplate.name == name)
    ).scalar_one_or_none()
    if tmpl is None:
        tmpl = EquipmentTemplate(name=name, category="VEHICLE", base_stats=stats)
        db.add(tmpl)
        db.flush()
    db.add(EquipmentInstance(template_id=tmpl.id, owner_id=unit.id, current_state=state or {}))


def _resupply_order(db: Session, supplier: TacticalUnit, target: TacticalUnit) -> None:
    db.add(
        Order(
            session_id=_SID,
            issuer_id="u1",
            unit_id=supplier.id,
            order_type="RESUPPLY",
            payload={"target_unit_id": target.id},
            status=OrderStatus.VALIDATED,
            issued_at_tick=0,
        )
    )


def _run(factory: sessionmaker[Session], ticks: int) -> list:
    sysm = ResupplySystem(session_id=_SID, session_factory=factory, hot_state=InMemoryHotState())
    clock = SimClock(tick_rate_ms=60_000)
    events = []
    for _ in range(ticks):
        events.extend(asyncio.run(sysm.consume(clock.now())))
        clock.advance()
    return events


def _setup(
    factory: sessionmaker[Session],
    *,
    target_fuel: float = 0.0,
    supplier_pos: tuple[float, float] = _POS,
    target_faction: str = "BLUE",
    supplier_is_truck: bool = True,
) -> tuple[str, str]:
    with factory() as db:
        db.add(WargameSession(id=_SID, name="x", master_seed=1, current_weather={}))
        db.flush()
        sup = _unit(db, "SUP", "BLUE", supplier_pos)
        tgt = _unit(db, "T1", target_faction, _POS)
        if supplier_is_truck:
            _equip(db, sup, "FUEL_TRUCK_T", {"logistics": _TRUCK_LOG})
        else:
            _equip(db, sup, "TANK_T", {"mobility": _TANK_VEH})
        _equip(db, tgt, "TANK_T", {"mobility": _TANK_VEH}, {"fuel": target_fuel})
        _resupply_order(db, sup, tgt)
        db.commit()
        return sup.id, tgt.id


def test_refuels_target_over_ticks(session_factory: sessionmaker[Session]) -> None:
    """補給車就近 → 每 tick 撥交 400 → 空油箱(0)/容量 1000 約 3 tick 加滿。"""
    _sup, tgt = _setup(session_factory)
    events = _run(session_factory, 5)
    kinds = [e.event_type for e in events]
    assert "RESUPPLY_TICK" in kinds
    assert "RESUPPLY_COMPLETED" in kinds
    with session_factory() as db:
        assert load_unit_fuel(db, tgt).remaining == 1000.0  # 已加滿
        o = db.execute(select(Order).where(Order.session_id == _SID)).scalars().first()
        assert o.status == OrderStatus.COMPLETED


def test_out_of_range_waits_not_fails(session_factory: sessionmaker[Session]) -> None:
    """補給車太遠（>2km）→ 不算失敗，標 EXECUTING 等它開過來。"""
    _sup, tgt = _setup(session_factory, supplier_pos=(23.75, 121.50))  # ~30km 外
    events = _run(session_factory, 3)
    assert not events  # 無事件（等待中）
    with session_factory() as db:
        o = db.execute(select(Order).where(Order.session_id == _SID)).scalars().first()
        assert o.status == OrderStatus.EXECUTING
        assert load_unit_fuel(db, tgt).remaining == 0.0  # 尚未加到油


def test_refuses_other_faction(session_factory: sessionmaker[Session]) -> None:
    _sup, _tgt = _setup(session_factory, target_faction="RED")
    events = _run(session_factory, 2)
    assert [e.event_type for e in events][:1] == ["RESUPPLY_FAILED"]
    assert events[0].detail["reason"] == "NOT_SAME_FACTION"


def test_non_supply_unit_fails(session_factory: sessionmaker[Session]) -> None:
    """下令單位沒有 LOGISTICS 載油裝備 → NOT_A_SUPPLY_UNIT。"""
    _sup, _tgt = _setup(session_factory, supplier_is_truck=False)
    events = _run(session_factory, 2)
    assert events[0].event_type == "RESUPPLY_FAILED"
    assert events[0].detail["reason"] == "NOT_A_SUPPLY_UNIT"


def test_stranded_unit_can_move_again_after_refuel(
    session_factory: sessionmaker[Session],
) -> None:
    """**headline**：#84 油盡拋錨的單位 → 補給加油 → 重下 MOVE 令即可再動。"""
    _sup, tgt = _setup(session_factory, target_fuel=0.0)
    # 1) 加油前：下 MOVE 令 → 立刻 MOVE_HALTED_FUEL（動不了）。
    with session_factory() as db:
        t = db.get(TacticalUnit, tgt)
        db.add(
            Order(
                session_id=_SID,
                issuer_id="u1",
                unit_id=tgt,
                order_type="MOVE",
                payload={
                    "to_h3": h3.latlng_to_cell(23.75, 121.30, 8),
                    "to_lat": 23.75,
                    "to_lng": 121.30,
                    "mobility_profile": "FOOT",
                },
                status=OrderStatus.VALIDATED,
                issued_at_tick=0,
            )
        )
        db.commit()
        before = (t.current_lat, t.current_lng)
    mover = UnitMovementSystem(
        session_id=_SID,
        session_factory=session_factory,
        hot_state=InMemoryHotState(),
        tick_rate_ms=60_000,
        rng=DeterministicRNG(1, "movement"),
    )
    clock = SimClock(tick_rate_ms=60_000)
    ev1 = asyncio.run(mover.step(clock.now()))
    assert [e.event_type for e in ev1] == ["MOVE_HALTED_FUEL"]
    with session_factory() as db:
        t = db.get(TacticalUnit, tgt)
        assert (t.current_lat, t.current_lng) == before  # 完全沒動

    # 2) 補給加油。
    _run(session_factory, 5)
    with session_factory() as db:
        assert load_unit_fuel(db, tgt).remaining > 0

    # 3) 重下 MOVE 令 → 這次動得了。
    with session_factory() as db:
        db.add(
            Order(
                session_id=_SID,
                issuer_id="u1",
                unit_id=tgt,
                order_type="MOVE",
                payload={
                    "to_h3": h3.latlng_to_cell(23.75, 121.30, 8),
                    "to_lat": 23.75,
                    "to_lng": 121.30,
                    "mobility_profile": "FOOT",
                },
                status=OrderStatus.VALIDATED,
                issued_at_tick=1,
            )
        )
        db.commit()
    for _ in range(3):
        clock.advance()
        asyncio.run(mover.step(clock.now()))
    with session_factory() as db:
        t = db.get(TacticalUnit, tgt)
        assert t.current_lng > before[1]  # 救回來了：確實往東前進


def test_resupplies_ammo_too(session_factory: sessionmaker[Session]) -> None:
    """#87：補給車同時撥交油料與**彈藥**（彈藥補到 basic_load）。"""
    with session_factory() as db:
        db.add(WargameSession(id=_SID, name="x", master_seed=1, current_weather={}))
        db.flush()
        sup = _unit(db, "SUP", "BLUE", _POS)
        tgt = _unit(db, "T1", "BLUE", _POS)
        truck = {**_TRUCK_LOG, "capacity": {"FUEL": 5000, "AMMO": 5000}}
        _equip(db, sup, "TRUCK_AMMO", {"logistics": truck})
        _equip(db, tgt, "TANK_T", {"mobility": _TANK_VEH}, {"fuel": 0.0})
        # 目標的槍打到只剩 10 發（basic_load 預設 100）。
        _equip(
            db,
            tgt,
            "RIFLE_T",
            {
                "max_range_m": 600,
                "ph_by_range_band": [[600, 0.5]],
                "damage_by_armor_class": {},
                "ammo_types": ["A"],
            },
            {"ammo": 10},
        )
        _resupply_order(db, sup, tgt)
        db.commit()
        tgt_id = tgt.id
    events = _run(session_factory, 6)
    assert any(e.detail.get("ammo", 0) > 0 for e in events), "應有彈藥撥交"
    with session_factory() as db:
        rifle = [
            i
            for i in db.execute(
                select(EquipmentInstance).where(EquipmentInstance.owner_id == tgt_id)
            ).scalars()
            if "ammo" in (i.current_state or {})
        ]
        assert rifle and rifle[0].current_state["ammo"] == 100  # 補到 basic_load
        assert load_unit_fuel(db, tgt_id).remaining > 0  # 油也加了
