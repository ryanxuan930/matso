"""#81 Phase B：地形/坡度調速 + 不可通行阻擋（MOVE_BLOCKED）+ terrain 服務中斷退化。"""

from __future__ import annotations

import asyncio

from sqlalchemy import select
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
from app.movement.mobility_matrix import step_cost
from app.state.hot_state import InMemoryHotState

_START = (23.75, 121.20)
_TRUCK = {
    "can_self_move": True,
    "mobility_class": "WHEELED",
    "max_road_speed_kmh": 85,
    "max_cross_country_speed_kmh": 40,
}


# ---- 純函數：mobility_matrix.step_cost ----


def test_step_cost_terrain_and_slope() -> None:
    assert step_cost("FOOT", "GRASSLAND", 0.0) == 1.0
    assert step_cost("FOOT", "FOREST", 0.0) == 1.5  # 森林較慢
    assert step_cost("FOOT", "MOUNTAIN", 0.0) == 3.0  # 山地更慢
    # 坡度加成（FOOT slope_penalty=1.0）：45° → ×2。
    assert step_cost("FOOT", "GRASSLAND", 45.0) == 2.0
    # 不可通行 → None。
    assert step_cost("FOOT", "WATER", 0.0) is None
    assert step_cost("WHEELED", "MOUNTAIN", 0.0) is None
    assert step_cost("WHEELED", "WATER", 0.0) is None
    # 未知 → 不調速（1.0，安全退化）。
    assert step_cost("FOOT", "UNKNOWN_CLASS", 0.0) == 1.0


# ---- 執行器 ----


def _seed(db: Session, sid: str, *, vehicle: dict | None) -> str:
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
        tmpl = EquipmentTemplate(name="veh", category="VEHICLE", base_stats={"mobility": vehicle})
        db.add(tmpl)
        db.flush()
        db.add(EquipmentInstance(template_id=tmpl.id, owner_id=unit.id, current_state={}))
    db.add(
        Order(
            session_id=sid,
            issuer_id="u1",
            unit_id=unit.id,
            order_type="MOVE",
            payload={"to_lat": 23.75, "to_lng": 121.30, "mobility_profile": "FOOT"},
            status=OrderStatus.VALIDATED,
            issued_at_tick=0,
        )
    )
    return unit.id


def _fixed_terrain(tclass: str, slope: float = 0.0):
    def _sample(cells: list[str]) -> dict[str, tuple[str, float]]:
        return dict.fromkeys(cells, (tclass, slope))

    return _sample


def _run(factory: sessionmaker[Session], sid: str, sampler, ticks: int) -> list:
    mover = UnitMovementSystem(
        session_id=sid,
        session_factory=factory,
        hot_state=InMemoryHotState(),
        tick_rate_ms=60_000,
        rng=DeterministicRNG(1, "movement"),
        terrain_sampler=sampler,
    )
    clock = SimClock(tick_rate_ms=60_000)
    events = []
    for _ in range(ticks):
        events.extend(asyncio.run(mover.step(clock.now())))
        clock.advance()
    return events


def _dist_covered(factory: sessionmaker[Session], sid: str, uid: str) -> float:
    with factory() as db:
        u = db.get(TacticalUnit, uid)
        return _haversine_km(_START[0], _START[1], u.current_lat, u.current_lng)


def test_forest_slower_than_grassland(session_factory: sessionmaker[Session]) -> None:
    with session_factory() as db:
        g_id = _seed(db, "grass", vehicle=None)
        db.commit()
    with session_factory() as db:
        f_id = _seed(db, "forest", vehicle=None)
        db.commit()
    _run(session_factory, "grass", _fixed_terrain("GRASSLAND"), 20)
    _run(session_factory, "forest", _fixed_terrain("FOREST"), 20)
    grass = _dist_covered(session_factory, "grass", g_id)
    forest = _dist_covered(session_factory, "forest", f_id)
    # FOREST 成本 1.5 → 森林 20 tick 覆蓋約 grassland 的 1/1.5。
    assert forest < grass
    assert abs(forest - grass / 1.5) < 0.05


def test_uphill_slower_than_flat(session_factory: sessionmaker[Session]) -> None:
    with session_factory() as db:
        flat_id = _seed(db, "flat", vehicle=None)
        db.commit()
    with session_factory() as db:
        hill_id = _seed(db, "hill", vehicle=None)
        db.commit()
    _run(session_factory, "flat", _fixed_terrain("GRASSLAND", 0.0), 20)
    _run(session_factory, "hill", _fixed_terrain("GRASSLAND", 30.0), 20)  # 30° 上坡
    assert _dist_covered(session_factory, "hill", hill_id) < _dist_covered(
        session_factory, "flat", flat_id
    )


def test_impassable_blocks_and_completes(session_factory: sessionmaker[Session]) -> None:
    # 輪型單位進入 WATER（-1 不可通行）→ MOVE_BLOCKED + 訂單 COMPLETED（停在此）。
    with session_factory() as db:
        uid = _seed(db, "block", vehicle=_TRUCK)
        db.commit()
    events = _run(session_factory, "block", _fixed_terrain("WATER"), 5)
    blocked = [e for e in events if e.event_type == "MOVE_BLOCKED"]
    assert len(blocked) == 1
    assert blocked[0].detail["reason"] == "IMPASSABLE_TERRAIN"
    assert blocked[0].detail["profile"] == "WHEELED"
    with session_factory() as db:
        o = db.execute(select(Order).where(Order.session_id == "block")).scalars().first()
        assert o.status == OrderStatus.COMPLETED
        u = db.get(TacticalUnit, uid)
        # 停在起點（未推進到目的地）。
        assert _haversine_km(_START[0], _START[1], u.current_lat, u.current_lng) < 0.2


def test_terrain_service_down_falls_back(session_factory: sessionmaker[Session]) -> None:
    # 取樣器拋錯（terrain 服務中斷）→ 不調速、不阻擋（Phase A 速度），移動照常。
    with session_factory() as db:
        uid = _seed(db, "down", vehicle=None)
        db.commit()

    def _boom(cells: list[str]) -> dict[str, tuple[str, float]]:
        raise RuntimeError("terrain down")

    _run(session_factory, "down", _boom, 10)
    # 徒步基準 5km/h × 10 tick ≈ 0.83km 應有推進（未凍結）。
    assert _dist_covered(session_factory, "down", uid) > 0.5


def test_no_sampler_is_phase_a(session_factory: sessionmaker[Session]) -> None:
    # 無取樣器（None）→ 完全不調速（與 grassland cost=1.0 等價）。
    with session_factory() as db:
        n_id = _seed(db, "none", vehicle=None)
        db.commit()
    with session_factory() as db:
        g_id = _seed(db, "grass2", vehicle=None)
        db.commit()
    _run(session_factory, "none", None, 15)
    _run(session_factory, "grass2", _fixed_terrain("GRASSLAND"), 15)
    assert (
        abs(
            _dist_covered(session_factory, "none", n_id)
            - _dist_covered(session_factory, "grass2", g_id)
        )
        < 1e-6
    )


# ---------------- #83 道路加速 ----------------


def test_road_speed_factor_lookup() -> None:
    from app.movement.mobility_matrix import road_speed_factor

    assert road_speed_factor("WHEELED", "motorway") == 1.0
    assert road_speed_factor("WHEELED", "residential") == 0.55
    assert road_speed_factor("FOOT", "primary") == 0.95
    assert road_speed_factor("WHEELED", "") is None  # 無路
    assert road_speed_factor("BOAT", "motorway") is None  # 舟艇不能走公路
    assert road_speed_factor("WHEELED", "unknown_kind") is None


def test_road_is_faster_than_cross_country(session_factory: sessionmaker[Session]) -> None:
    """#83：同樣地形下，有公路的格明顯快於越野（輪型 road 85 vs xc 40 km/h）。"""
    with session_factory() as db:
        xc_id = _seed(db, "xc", vehicle=_TRUCK)
        db.commit()
    with session_factory() as db:
        road_id = _seed(db, "road", vehicle=_TRUCK)
        db.commit()
    # 同為 GRASSLAND；一個沒路、一個有 motorway。
    _run(session_factory, "xc", _fixed_terrain("GRASSLAND"), 10)
    _run(session_factory, "road", _fixed_terrain("GRASSLAND|motorway"), 10)
    xc = _dist_covered(session_factory, "xc", xc_id)
    road = _dist_covered(session_factory, "road", road_id)
    # 公路顯著較快（road 85 vs xc 40 km/h）。註：若沿路單位已抵達目的地，比值會被抵達截斷，
    # 故只斷言「明顯更遠」而非精確比值。
    assert road > xc * 1.5


def test_road_bypasses_terrain_cost(session_factory: sessionmaker[Session]) -> None:
    """林中公路不按森林算：有路 → 不套地形/坡度成本。"""
    with session_factory() as db:
        forest_id = _seed(db, "fst", vehicle=_TRUCK)
        db.commit()
    with session_factory() as db:
        froad_id = _seed(db, "fstroad", vehicle=_TRUCK)
        db.commit()
    _run(session_factory, "fst", _fixed_terrain("FOREST"), 10)  # WHEELED 森林成本 4.0
    _run(session_factory, "fstroad", _fixed_terrain("FOREST|primary"), 10)
    assert (
        _dist_covered(session_factory, "fstroad", froad_id)
        > _dist_covered(session_factory, "fst", forest_id) * 3
    )


# ---- WP-C4b：天氣機動（逐格） ----


def _run_weather(factory: sessionmaker[Session], sid: str, weather_for, ticks: int) -> None:
    mover = UnitMovementSystem(
        session_id=sid,
        session_factory=factory,
        hot_state=InMemoryHotState(),
        tick_rate_ms=60_000,
        rng=DeterministicRNG(1, "movement"),
        weather_for=weather_for,
    )
    clock = SimClock(tick_rate_ms=60_000)
    for _ in range(ticks):
        asyncio.run(mover.step(clock.now()))
        clock.advance()


def _mud_at(lat: float, lng: float, modifier: float):
    """把起點那一格設成泥濘的天氣快照。"""
    import h3

    from app.weather import CellEffects, WeatherState

    return WeatherState({h3.latlng_to_cell(lat, lng, 8): CellEffects(mobility_modifier=modifier)})


def test_bad_weather_slows_the_march(session_factory: sessionmaker[Session]) -> None:
    """**`weather_mobility` 過去一個呼叫端都沒有**——想定裡下暴雨，部隊照晴天速度行軍。

    天氣對機動的整條路徑（`movement_mobility_modifier`）是死碼。
    """
    with session_factory() as db:
        clear_id = _seed(db, "wx-clear", vehicle=None)
        db.commit()
    with session_factory() as db:
        mud_id = _seed(db, "wx-mud", vehicle=None)
        db.commit()
    _run_weather(session_factory, "wx-clear", None, 10)
    _run_weather(session_factory, "wx-mud", lambda: _mud_at(*_START, 0.5), 10)

    clear = _dist_covered(session_factory, "wx-clear", clear_id)
    mud = _dist_covered(session_factory, "wx-mud", mud_id)
    assert clear > 0
    assert abs(mud - clear * 0.5) < 0.02


def test_weather_only_slows_the_cell_it_covers(session_factory: sessionmaker[Session]) -> None:
    """天氣是**逐格**的：暴雨下在別處，這裡的部隊不該跟著慢下來。

    （全局單一倍率會把整場一起罰——那不是天氣，是規則。）
    """
    with session_factory() as db:
        uid = _seed(db, "wx-elsewhere", vehicle=None)
        db.commit()
    with session_factory() as db:
        base_id = _seed(db, "wx-base", vehicle=None)
        db.commit()
    _run_weather(session_factory, "wx-base", None, 10)
    _run_weather(session_factory, "wx-elsewhere", lambda: _mud_at(0.0, 0.0, 0.1), 10)

    assert _dist_covered(session_factory, "wx-elsewhere", uid) == _dist_covered(
        session_factory, "wx-base", base_id
    )


def test_weather_service_failure_does_not_halt_the_march(
    session_factory: sessionmaker[Session],
) -> None:
    """天氣服務炸了 → 退回中性，不是停止行軍（同 `has_los` 中斷不致盲的紀律）。"""

    def _boom() -> None:
        raise RuntimeError("weather down")

    with session_factory() as db:
        uid = _seed(db, "wx-down", vehicle=None)
        db.commit()
    with session_factory() as db:
        base_id = _seed(db, "wx-ok", vehicle=None)
        db.commit()
    _run_weather(session_factory, "wx-ok", None, 5)
    _run_weather(session_factory, "wx-down", _boom, 5)

    assert _dist_covered(session_factory, "wx-down", uid) == _dist_covered(
        session_factory, "wx-ok", base_id
    )
