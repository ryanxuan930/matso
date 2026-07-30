"""面目標射擊的活執行期接線（WP-C10.2）：令 → 蒐集目標 → 裁決 → 落戰損。

裁決本身的數學在 `test_area_fire.py`（純函數）。這裡釘的是**接線**：
撈對令、選對武器、蒐集目標時**敵我都收**、彈藥扣得掉、戰損落得進 DB。
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.adjudication.adjudicator import EngageCommand, EngageOrderSource
from app.engine.clock import SimTime
from app.engine.engage_wiring import WeaponResolver, seed_combat_state
from app.engine.fire_wiring import AreaFireAdjudicator, FireMissionCommand, FireMissionOrderSource
from app.engine.rng import DeterministicRNG
from app.engine.subsystems import ChainedOrderSource, DispatchingAdjudicator
from app.models import Base
from app.models.enums import OrderStatus, UnitLevel
from app.models.tables import (
    EquipmentInstance,
    EquipmentTemplate,
    Order,
    TacticalUnit,
    WargameSession,
)
from app.state.hot_state import InMemoryHotState
from app.state.ledger import LedgerEvent

# 落點：藍方砲兵在 (23.75, 121.25)，瞄準點在其東方約 1 km。
_AIM_LAT = 23.75
_AIM_LNG = 121.26

_HOWITZER = {
    "max_range_m": 15000,
    "ph_by_range_band": [[15000, 0.5]],
    "damage_by_armor_class": {"INFANTRY": 60},
    "pk_by_armor_class": {"INFANTRY": 0.6},
    "ammo_types": ["HE"],
    "indirect_fire": True,
    "dispersion_cep_m": 0,  # 測試要可預期落點：關掉散布，落點＝瞄準點
    "lethal_radius_m": 100,
}
_RIFLE = {
    "max_range_m": 600,
    "ph_by_range_band": [[600, 0.3]],
    "damage_by_armor_class": {"INFANTRY": 30},
    "ammo_types": ["AMMO_556"],
}

_NOW = SimTime(tick=42, sim_time_ms=42_000)


def _factory() -> sessionmaker[Session]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


class World:
    """砲兵（藍）+ 落點上的紅軍 + 落點上的**友軍** + 遠方紅軍。"""

    def __init__(self, db: Session, *, ammo: int = 20, howitzer: bool = True) -> None:
        s = WargameSession(name="w", master_seed=7, current_weather={})
        db.add(s)
        db.flush()
        self.session_id = s.id

        def unit(desig: str, faction: str, lat: float, lng: float) -> TacticalUnit:
            u = TacticalUnit(
                session_id=s.id,
                designation=desig,
                unit_level=UnitLevel.PLATOON,
                faction=faction,
                current_lat=lat,
                current_lng=lng,
                current_strength=100.0,
                authorized_strength=100.0,
                health_status=100.0,
                attributes={"armor_class": "INFANTRY", "platform_count": 1},
            )
            db.add(u)
            return u

        gun = unit("ARTY", "BLUE", 23.75, 121.25)
        red_on_target = unit("R1", "RED", _AIM_LAT, _AIM_LNG)
        friendly_on_target = unit("B9", "BLUE", _AIM_LAT, _AIM_LNG)
        red_far = unit("R2", "RED", 23.90, 121.40)
        db.flush()
        self.gun_id, self.red_id = gun.id, red_on_target.id
        self.friendly_id, self.far_id = friendly_on_target.id, red_far.id
        self.factions = {
            gun.id: "BLUE",
            red_on_target.id: "RED",
            friendly_on_target.id: "BLUE",
            red_far.id: "RED",
        }

        tmpl = EquipmentTemplate(
            name="M109" if howitzer else "RIFLE",
            category="ARTILLERY" if howitzer else "KINETIC",
            base_stats=_HOWITZER if howitzer else _RIFLE,
        )
        db.add(tmpl)
        db.flush()
        inst = EquipmentInstance(template_id=tmpl.id, owner_id=gun.id, current_state={"ammo": ammo})
        db.add(inst)
        db.commit()
        self.weapon_id = inst.id


def _order(db: Session, world: World, payload: dict[str, object]) -> str:
    o = Order(
        session_id=world.session_id,
        issuer_id="u",
        unit_id=world.gun_id,
        order_type="FIRE_MISSION",
        payload=payload,
        status=OrderStatus.VALIDATED,
        issued_at_tick=1,
    )
    db.add(o)
    db.commit()
    return o.id


def _setup(db: Session, world: World) -> tuple[InMemoryHotState, AreaFireAdjudicator]:
    hot = InMemoryHotState()
    resolver = WeaponResolver(db, world.session_id)
    seed_combat_state(db, hot, world.session_id, resolver)
    adj = AreaFireAdjudicator(
        db,
        hot,
        DeterministicRNG(master_seed=7, stream_id="area_fire"),
        resolver.weapons_for,
        faction_for=lambda uid: world.factions.get(uid, ""),
    )
    return hot, adj


def _strength(db: Session, unit_id: str) -> float:
    unit = db.get(TacticalUnit, unit_id)
    assert unit is not None
    db.refresh(unit)
    return float(unit.current_strength)


# ---- OrderSource ----


def test_drain_picks_up_fire_missions_and_marks_executing() -> None:
    with _factory()() as db:
        world = World(db)
        oid = _order(db, world, {"target_lat": _AIM_LAT, "target_lng": _AIM_LNG, "rounds": 3})
        cmds = asyncio.run(FireMissionOrderSource(db, world.session_id).drain())
        assert [c.order_id for c in cmds] == [oid]
        assert cmds[0].rounds == 3
        order = db.get(Order, oid)
        assert order is not None and order.status is OrderStatus.EXECUTING


def test_the_two_sources_do_not_steal_each_others_orders() -> None:
    """ENGAGE 與 FIRE_MISSION 各撈各的。

    兩邊都用 `Order.status == VALIDATED` 撈，型別條件寫錯就會**同一道令被裁決兩次**
    ——一次當交戰、一次當面射擊。這條就是釘住那件事。
    """
    with _factory()() as db:
        world = World(db)
        _order(db, world, {"target_lat": _AIM_LAT, "target_lng": _AIM_LNG})
        db.add(
            Order(
                session_id=world.session_id,
                issuer_id="u",
                unit_id=world.gun_id,
                order_type="ENGAGE",
                payload={"target_unit_id": world.red_id},
                status=OrderStatus.VALIDATED,
                issued_at_tick=1,
            )
        )
        db.commit()
        fires = asyncio.run(FireMissionOrderSource(db, world.session_id).drain())
        engages = asyncio.run(EngageOrderSource(db, world.session_id).drain())
        assert len(fires) == 1 and len(engages) == 1
        assert {type(c) for c in fires} == {FireMissionCommand}
        assert {type(c) for c in engages} == {EngageCommand}


def test_broken_payload_is_rejected_not_left_looping() -> None:
    """缺座標的令若留在 VALIDATED，會每個 tick 被撈出來一次——必須就地終結。"""
    with _factory()() as db:
        world = World(db)
        oid = _order(db, world, {"rounds": 2})  # 沒有 target_lat/lng
        cmds = asyncio.run(FireMissionOrderSource(db, world.session_id).drain())
        assert cmds == []
        order = db.get(Order, oid)
        assert order is not None and order.status is OrderStatus.REJECTED


# ---- 裁決接線 ----


def _fire(
    db: Session, world: World, rounds: int = 1, weapon_id: str | None = None
) -> tuple[InMemoryHotState, list[LedgerEvent], str]:
    payload: dict[str, object] = {
        "target_lat": _AIM_LAT,
        "target_lng": _AIM_LNG,
        "rounds": rounds,
    }
    if weapon_id:
        payload["weapon_id"] = weapon_id
    oid = _order(db, world, payload)
    hot, adj = _setup(db, world)
    cmds = asyncio.run(FireMissionOrderSource(db, world.session_id).drain())
    events = adj.resolve(cmds[0], _NOW)
    return hot, events, oid


def test_enemy_on_the_aim_point_takes_losses() -> None:
    with _factory()() as db:
        world = World(db)
        hot, events, oid = _fire(db, world)
        assert _strength(db, world.red_id) < 100.0
        assert float((hot.get_unit(world.red_id) or {})["strength"]) < 100.0
        assert events and events[0].event_type == "AREA_FIRE_RESOLVED"
        order = db.get(Order, oid)
        assert order is not None and order.status is OrderStatus.COMPLETED


def test_friendly_on_the_aim_point_takes_losses_too() -> None:
    """**本卡最要緊的一條。**

    蒐集「落點附近單位」只收敵軍的話，友軍傷害會被悄悄關掉，而且不會有任何徵兆——
    純函數那邊的 `test_friendly_units_are_also_hit` 照樣綠燈。
    """
    with _factory()() as db:
        world = World(db)
        _fire(db, world)
        assert _strength(db, world.friendly_id) < 100.0, "友軍站在落點上卻毫髮無傷"


def test_friendly_losses_are_named_in_the_ledger() -> None:
    """誤傷要能事後追究——「有沒有傷到自己人」是檢討火力協調的第一個問題。"""
    with _factory()() as db:
        world = World(db)
        _, events, _ = _fire(db, world)
        assert events[0].ai_decision["friendly_losses"] == [world.friendly_id]


def test_units_far_from_the_aim_point_are_untouched() -> None:
    with _factory()() as db:
        world = World(db)
        _fire(db, world)
        assert _strength(db, world.far_id) == 100.0


def test_ammo_is_spent_in_hot_state_and_db() -> None:
    with _factory()() as db:
        world = World(db, ammo=20)
        hot, _, _ = _fire(db, world, rounds=4)
        state = hot.get_unit(world.gun_id) or {}
        assert state["ammo_by_weapon"][world.weapon_id] == 16
        inst = db.get(EquipmentInstance, world.weapon_id)
        assert inst is not None
        db.refresh(inst)
        assert inst.current_state["ammo"] == 16


def test_short_of_ammo_fires_what_is_left_and_says_so() -> None:
    """有幾發打幾發是砲兵的真實行為；但「要 10 發只打了 2 發」必須看得見。"""
    with _factory()() as db:
        world = World(db, ammo=2)
        hot, events, _ = _fire(db, world, rounds=10)
        assert events[0].ai_decision["rounds"] == 2
        assert events[0].ai_decision["rounds_requested"] == 10
        assert (hot.get_unit(world.gun_id) or {})["ammo_by_weapon"][world.weapon_id] == 0


def test_no_ammo_is_rejected_without_losses() -> None:
    with _factory()() as db:
        world = World(db, ammo=0)
        _, events, _ = _fire(db, world)
        assert events[0].ai_decision["status"] == "REJECTED"
        assert events[0].ai_decision["reason"] == "NO_AMMO"
        assert _strength(db, world.red_id) == 100.0


def test_shooter_moved_out_of_range_is_rejected() -> None:
    """下令當下在射程內，執行時不一定還在——射手會移動，所以執行時要重查。"""
    with _factory()() as db:
        world = World(db)
        oid = _order(db, world, {"target_lat": _AIM_LAT, "target_lng": _AIM_LNG})
        hot, adj = _setup(db, world)
        hot.update_unit(world.gun_id, {"lat": 25.5, "lng": 123.5})  # 跑到幾百公里外
        cmds = asyncio.run(FireMissionOrderSource(db, world.session_id).drain())
        events = adj.resolve(cmds[0], _NOW)
        assert events[0].ai_decision["reason"] == "OUT_OF_RANGE"
        assert _strength(db, world.red_id) == 100.0
        assert db.get(Order, oid).status is OrderStatus.COMPLETED  # type: ignore[union-attr]


def test_unit_without_indirect_weapon_is_rejected() -> None:
    """只有步槍的單位不該因為 payload 寫了座標就變成砲兵。"""
    with _factory()() as db:
        world = World(db, howitzer=False)
        _, events, _ = _fire(db, world)
        assert events[0].ai_decision["reason"] == "NO_INDIRECT_WEAPON"
        assert _strength(db, world.red_id) == 100.0


def test_losses_are_deterministic_for_the_same_seed() -> None:
    """同一顆種子跑兩次必得同樣的戰損（紅線 1）。"""
    results = []
    for _ in range(2):
        with _factory()() as db:
            world = World(db)
            _fire(db, world, rounds=3)
            results.append(round(_strength(db, world.red_id), 6))
    assert results[0] == results[1]


def test_losses_never_drive_strength_below_zero() -> None:
    with _factory()() as db:
        world = World(db, ammo=200)
        _fire(db, world, rounds=60)
        assert _strength(db, world.red_id) == pytest.approx(0.0)


# ---- Kernel 組合器 ----


def test_chained_source_concatenates_in_order() -> None:
    class Src:
        def __init__(self, items: list[object]) -> None:
            self.items = items

        async def drain(self) -> list[object]:
            return list(self.items)

    chained = ChainedOrderSource(Src(["a", "b"]), Src(["c"]))
    assert asyncio.run(chained.drain()) == ["a", "b", "c"]


def test_dispatching_adjudicator_routes_by_command_type() -> None:
    class Recorder:
        def __init__(self, tag: str) -> None:
            self.tag, self.seen = tag, 0

        def resolve(self, order: object, now: SimTime) -> list[LedgerEvent]:
            self.seen += 1
            return [LedgerEvent(event_type=self.tag, tick=now.tick)]

    engage, fire = Recorder("E"), Recorder("F")
    dispatcher = DispatchingAdjudicator({EngageCommand: engage, FireMissionCommand: fire})
    out = dispatcher.resolve(FireMissionCommand("o", "u", 23.0, 121.0), _NOW)
    assert [e.event_type for e in out] == ["F"]
    assert engage.seen == 0
    assert dispatcher.resolve(object(), _NOW) == []  # 未登錄型別不炸鍋


# ---- WP-B6 ROE：本檔過去一個 roe/forbidden 都沒有 ----


def _fire_with_roe(forbidden: frozenset[str]) -> tuple[list[LedgerEvent], float]:
    """在 ROE 底下打一次面射擊。回 (events, 目標剩餘戰力)。"""
    with _factory()() as db:
        world = World(db)
        _order(db, world, {"target_lat": _AIM_LAT, "target_lng": _AIM_LNG, "rounds": 3})
        hot = InMemoryHotState()
        resolver = WeaponResolver(db, world.session_id)
        seed_combat_state(db, hot, world.session_id, resolver)
        adj = AreaFireAdjudicator(
            db,
            hot,
            DeterministicRNG(master_seed=7, stream_id="area_fire"),
            resolver.weapons_for,
            faction_for=lambda uid: world.factions.get(uid, ""),
            roe_for=lambda _uid: (None, forbidden),
        )
        cmds = asyncio.run(FireMissionOrderSource(db, world.session_id).drain())
        events = adj.resolve(cmds[0], _NOW)
        return events, _strength(db, world.red_id)


def test_a_forbidden_gun_may_not_fire_a_mission() -> None:
    """想定宣告「禁用火砲」，面射擊過去照打不誤——`fire_wiring` 全檔沒有任何 ROE。"""
    events, red_strength = _fire_with_roe(frozenset({"ARTILLERY"}))

    assert events and events[0].ai_decision["reason"] == "ROE"  # type: ignore[index]
    assert red_strength == 100.0


def test_an_unrestricted_mission_still_fires() -> None:
    """守門不可過寬：沒被禁就照打（否則這條修正會把砲兵整個鎖死）。"""
    events, red_strength = _fire_with_roe(frozenset({"LASER"}))

    assert events and events[0].event_type == "AREA_FIRE_RESOLVED"
    assert red_strength < 100.0
