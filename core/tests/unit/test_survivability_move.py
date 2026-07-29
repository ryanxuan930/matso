"""陣地變換（WP-C10.5）——砲兵打久了要換位置。

驗收：打滿 N 次火力任務的自走砲會**自動下一道 MOVE 令**（不是瞬移座標）。
"""

from __future__ import annotations

import math

from _order_fakes import FakeGateway, OrderWorld, seed_world
from sqlalchemy.orm import Session, sessionmaker

from app.engine.rng import DeterministicRNG
from app.fires.displacement import due_units, run_due_displacements
from app.fires.survivability import (
    MISSION_COUNT_KEY,
    SurvivabilityConfig,
    parse_survivability_config,
    pick_displacement_point,
)
from app.models.enums import OrderStatus
from app.models.tables import EquipmentInstance, EquipmentTemplate, Order, TacticalUnit
from app.orders.service import OrderService
from app.state.hot_state import InMemoryHotState

_HOWITZER = {
    "max_range_m": 25000,
    "ph_by_range_band": [[25000, 0.5]],
    "damage_by_armor_class": {"INFANTRY": 60},
    "ammo_types": ["HE"],
    "indirect_fire": True,
    "dispersion_cep_m": 150,
    "lethal_radius_m": 60,
    "mobility": {
        "can_self_move": True,
        "mobility_class": "TRACKED",
        "max_road_speed_kmh": 60,
        "max_cross_country_speed_kmh": 30,
    },
}
# 迫砲：人扛的，不能自走。
_MORTAR = {
    **_HOWITZER,
    "mobility": {"can_self_move": False, "mobility_class": "MAN_PORTABLE"},
}

_CFG = SurvivabilityConfig(enabled=True, missions_before_move=3, min_km=1.0, max_km=2.0)


def _rng() -> DeterministicRNG:
    return DeterministicRNG(master_seed=13, stream_id="survivability")


def _svc(db: Session) -> OrderService:
    return OrderService(db, FakeGateway(reachable=True))


def _arm(factory: sessionmaker[Session], unit_id: str, stats: dict = _HOWITZER) -> None:  # type: ignore[type-arg]
    with factory() as db:
        t = EquipmentTemplate(name=f"G-{unit_id[:6]}", category="ARTILLERY", base_stats=stats)
        db.add(t)
        db.flush()
        db.add(EquipmentInstance(template_id=t.id, owner_id=unit_id, current_state={"ammo": 60}))
        db.commit()


def _hot_with(world: OrderWorld, count: int) -> InMemoryHotState:
    hot = InMemoryHotState()
    hot.put_unit(
        world.blue_unit_id,
        {"lat": 23.75, "lng": 121.25, MISSION_COUNT_KEY: count},
    )
    return hot


# ---- 設定解析 ----


def test_absent_config_is_disabled() -> None:
    """整包缺席＝停用＝既有局零行為變更。"""
    assert parse_survivability_config(None).enabled is False
    assert parse_survivability_config({}).enabled is False
    assert parse_survivability_config({"enabled": False}).enabled is False


def test_swapped_bounds_are_repaired_not_rejected() -> None:
    """想定作者把 min/max 填反不該讓整局起不來——對調後的意圖是明確的。"""
    cfg = parse_survivability_config({"enabled": True, "min_km": 5.0, "max_km": 2.0})
    assert (cfg.min_km, cfg.max_km) == (2.0, 5.0)


# ---- 幾何 ----


def test_displacement_lands_in_the_declared_band() -> None:
    cfg = SurvivabilityConfig(enabled=True, min_km=1.0, max_km=2.0)
    rng = _rng()
    for _ in range(30):
        lat, lng = pick_displacement_point(23.75, 121.25, rng, cfg)
        dx = (lng - 121.25) * 111_320 * math.cos(math.radians(23.75))
        dy = (lat - 23.75) * 111_320
        km = math.hypot(dx, dy) / 1000
        assert 0.95 <= km <= 2.05, km


def test_displacement_is_deterministic() -> None:
    cfg = SurvivabilityConfig(enabled=True)
    assert pick_displacement_point(23.75, 121.25, _rng(), cfg) == pick_displacement_point(
        23.75, 121.25, _rng(), cfg
    )


def test_bearings_actually_vary() -> None:
    """每次都往同一個方向跑就不是「換陣地」，是排隊。"""
    cfg = SurvivabilityConfig(enabled=True)
    rng = _rng()
    pts = {pick_displacement_point(23.75, 121.25, rng, cfg) for _ in range(10)}
    assert len(pts) == 10


# ---- 誰該換陣地 ----


def test_a_gun_under_the_threshold_stays_put(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    _arm(session_factory, world.blue_unit_id)
    with session_factory() as db:
        assert due_units(db, _hot_with(world, 2), world.session_id, _CFG) == []


def test_a_gun_at_the_threshold_is_due(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    _arm(session_factory, world.blue_unit_id)
    with session_factory() as db:
        assert due_units(db, _hot_with(world, 3), world.session_id, _CFG) == [world.blue_unit_id]


def test_disabled_config_schedules_nothing(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    _arm(session_factory, world.blue_unit_id)
    with session_factory() as db:
        assert due_units(db, _hot_with(world, 99), world.session_id, SurvivabilityConfig()) == []


def test_a_towed_piece_is_not_scheduled(session_factory: sessionmaker[Session]) -> None:
    """**牽引砲要牽引車。**

    用 FOOT 側寫「走」1.5 km 不是模型化人力搬砲——那只是機動解析的 fallback 在替我們亂編
    （5 km/h 走 18 個 tick、不耗油）。沒有 TOWED_GUN 範本、也沒有程式讀 can_tow，
    為它寫的分支會是沒有測試資料的虛構。
    """
    world = seed_world(session_factory)
    _arm(session_factory, world.blue_unit_id, _MORTAR)
    with session_factory() as db:
        assert due_units(db, _hot_with(world, 9), world.session_id, _CFG) == []


def test_a_fixed_unit_is_not_scheduled(session_factory: sessionmaker[Session]) -> None:
    """想定作者標了 `is_fixed` 就是說這門砲不動——想定開關不該蓋過想定作者的明確宣告。"""
    world = seed_world(session_factory)
    _arm(session_factory, world.blue_unit_id)
    with session_factory() as db:
        unit = db.get(TacticalUnit, world.blue_unit_id)
        assert unit is not None
        unit.is_fixed = True
        db.commit()
    with session_factory() as db:
        assert due_units(db, _hot_with(world, 9), world.session_id, _CFG) == []


# ---- 執行 ----


def _run(factory: sessionmaker[Session], world: OrderWorld, hot: InMemoryHotState, tick: int = 50):  # type: ignore[no-untyped-def]
    with factory() as db:
        return run_due_displacements(db, hot, world.session_id, tick, _CFG, _rng(), _svc)


def _orders(factory: sessionmaker[Session], world: OrderWorld) -> list[Order]:
    with factory() as db:
        return list(
            db.query(Order)
            .filter(Order.session_id == world.session_id, Order.order_type == "MOVE")
            .all()
        )


def test_displacement_issues_a_move_order_not_a_teleport(
    session_factory: sessionmaker[Session],
) -> None:
    """**這條是本卡的核心設計**。

    直接改座標看起來省事，但：重啟會被 `seed_combat_state` 以 DB 座標蓋回去、
    畫面上是瞬移、同 tick 內移動子系統會把它走回來、而且繞過唯一的可達性閘門。
    下 MOVE 令則四件事全部免費解決。
    """
    world = seed_world(session_factory)
    _arm(session_factory, world.blue_unit_id)
    hot = _hot_with(world, 3)
    events = _run(session_factory, world, hot)
    assert [e.event_type for e in events] == ["SURVIVABILITY_MOVE"]
    orders = _orders(session_factory, world)
    assert len(orders) == 1
    assert orders[0].unit_id == world.blue_unit_id
    assert orders[0].status is OrderStatus.VALIDATED
    # 座標沒有被就地改掉——是令去改。
    assert (hot.get_unit(world.blue_unit_id) or {})["lat"] == 23.75


def test_the_counter_resets_after_displacing(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    _arm(session_factory, world.blue_unit_id)
    hot = _hot_with(world, 5)
    _run(session_factory, world, hot)
    assert (hot.get_unit(world.blue_unit_id) or {})[MISSION_COUNT_KEY] == 0


def test_it_does_not_displace_twice_in_a_row(session_factory: sessionmaker[Session]) -> None:
    """歸零之後就不該再排——否則每個 tick 都下一道 MOVE 令。"""
    world = seed_world(session_factory)
    _arm(session_factory, world.blue_unit_id)
    hot = _hot_with(world, 3)
    _run(session_factory, world, hot)
    assert _run(session_factory, world, hot) == []
    assert len(_orders(session_factory, world)) == 1


def test_a_unit_already_moving_is_left_alone(session_factory: sessionmaker[Session]) -> None:
    """已經在移動的單位不要再塞一個相衝的目的地。"""
    world = seed_world(session_factory)
    _arm(session_factory, world.blue_unit_id)
    with session_factory() as db:
        db.add(
            Order(
                session_id=world.session_id,
                issuer_id="u",
                unit_id=world.blue_unit_id,
                order_type="MOVE",
                payload={"to_h3": "x", "mobility_profile": "TRACKED"},
                status=OrderStatus.EXECUTING,
                issued_at_tick=1,
            )
        )
        db.commit()
    hot = _hot_with(world, 5)
    assert _run(session_factory, world, hot) == []
    # 計數**不歸零**：它沒有換陣地，暴露度沒有降下來。
    assert (hot.get_unit(world.blue_unit_id) or {})[MISSION_COUNT_KEY] == 5


def test_an_unreachable_position_is_recorded_not_swallowed(
    session_factory: sessionmaker[Session],
) -> None:
    """位移不出去要留痕，而且**計數照樣歸零**。

    不歸零的話，一門被困住的砲會在每個 tick 重試一次、每次一趟 gRPC——
    永久的負載，而且畫面上什麼都不會發生。
    """
    world = seed_world(session_factory)
    _arm(session_factory, world.blue_unit_id)
    hot = _hot_with(world, 3)
    with session_factory() as db:
        events = run_due_displacements(
            db,
            hot,
            world.session_id,
            50,
            _CFG,
            _rng(),
            lambda s: OrderService(s, FakeGateway(reachable=False)),
        )
    assert [e.event_type for e in events] == ["SURVIVABILITY_MOVE_BLOCKED"]
    assert events[0].ai_decision["attempts"] == 3  # 換了三個方位才放棄
    assert (hot.get_unit(world.blue_unit_id) or {})[MISSION_COUNT_KEY] == 0
    # 三次嘗試各留下一筆 REJECTED——`OrderService.submit` 的既有語義是「不可行也要落庫」。
    # 令列上會多三筆，那是**真實發生過的事**：這門砲試了三個方向都出不去。
    # 靜靜地探測反而會讓「砲被困住」這件事沒有任何痕跡。
    assert {o.status for o in _orders(session_factory, world)} == {OrderStatus.REJECTED}


def test_the_move_order_is_issued_by_a_system_account(
    session_factory: sessionmaker[Session],
) -> None:
    """自動位移沒有人的意圖——掛在某個玩家頭上是假的。AAR 要看得出這道令不是人下的。"""
    from app.models.tables import SessionParticipant, User

    world = seed_world(session_factory)
    _arm(session_factory, world.blue_unit_id)
    _run(session_factory, world, _hot_with(world, 3))
    order = _orders(session_factory, world)[0]
    with session_factory() as db:
        part = db.get(SessionParticipant, order.issuer_id)
        assert part is not None
        user = db.get(User, part.user_id)
        assert user is not None and user.username == "system-BLUE"
        assert user.password_hash.startswith("!")  # 不可登入
