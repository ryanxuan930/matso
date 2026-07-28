"""單位移動系統（O10.1）：MOVE 指令執行 → 單位朝目標推進 → 到點 COMPLETED，並累積熱狀態 diff。"""

from __future__ import annotations

import asyncio

import h3
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.engine.clock import SimClock
from app.engine.movement import UnitMovementSystem
from app.models import Order, OrderStatus, TacticalUnit, UnitLevel, WargameSession
from app.state.hot_state import InMemoryHotState

_SID = "sess-move"
_START = (23.75, 121.25)


def _seed(factory: sessionmaker[Session], dest: str) -> str:
    with factory() as db:
        db.add(WargameSession(id=_SID, name="移動測試", master_seed=1, current_weather={}))
        db.flush()
        unit = TacticalUnit(
            session_id=_SID,
            designation="B1",
            unit_level=UnitLevel.PLATOON,
            faction="BLUE",
            current_lat=_START[0],
            current_lng=_START[1],
        )
        db.add(unit)
        db.flush()
        db.add(
            Order(
                session_id=_SID,
                issuer_id="u1",
                unit_id=unit.id,
                order_type="MOVE",
                payload={"to_h3": dest, "mobility_profile": "FOOT"},
                status=OrderStatus.VALIDATED,
                issued_at_tick=0,
            )
        )
        db.commit()
        return unit.id


def _dist_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    from app.engine.movement import _haversine_km

    return _haversine_km(a[0], a[1], b[0], b[1])


def test_move_steps_toward_dest_then_completes(session_factory: sessionmaker[Session]) -> None:
    # 目標約 3km 外（同緯度略東）
    dest_lat, dest_lng = 23.75, 121.28
    dest = h3.latlng_to_cell(dest_lat, dest_lng, 8)
    unit_id = _seed(session_factory, dest)
    hot = InMemoryHotState()
    mover = UnitMovementSystem(
        session_id=_SID,
        session_factory=session_factory,
        hot_state=hot,
        tick_rate_ms=60_000,
        speed_kmh=40.0,  # ~0.667 km/tick
    )
    clock = SimClock(tick_rate_ms=60_000)

    # 第一個 tick：單位應朝目標移動（狀態轉 EXECUTING），熱狀態有 lat/lng diff。
    events = asyncio.run(mover.step(clock.now()))
    assert events and events[0].event_type == "UNIT_MOVED"
    diff = hot.drain_diff()
    assert unit_id in diff and "lat" in diff[unit_id] and "lng" in diff[unit_id]
    with session_factory() as db:
        u = db.get(TacticalUnit, unit_id)
        assert u is not None
        moved = (u.current_lat, u.current_lng)
        o = db.execute(select(Order).where(Order.session_id == _SID)).scalars().first()
        assert o is not None and o.status == OrderStatus.EXECUTING
    # 移動後離起點更遠、離終點更近。
    assert _dist_km(_START, moved) > 0
    assert _dist_km(moved, (dest_lat, dest_lng)) < _dist_km(_START, (dest_lat, dest_lng))

    # 多跑幾個 tick → 應到點並 COMPLETED（3km / 0.667km ≈ 5 ticks，給足 20）。
    for _ in range(20):
        clock.advance()
        asyncio.run(mover.step(clock.now()))
    with session_factory() as db:
        o = db.execute(select(Order).where(Order.session_id == _SID)).scalars().first()
        assert o is not None and o.status == OrderStatus.COMPLETED
        u = db.get(TacticalUnit, unit_id)
        assert u is not None
        assert _dist_km((u.current_lat, u.current_lng), (dest_lat, dest_lng)) < 0.7


def test_no_move_order_no_events(session_factory: sessionmaker[Session]) -> None:
    with session_factory() as db:
        db.add(WargameSession(id=_SID, name="空", master_seed=1, current_weather={}))
        db.commit()
    hot = InMemoryHotState()
    mover = UnitMovementSystem(
        session_id=_SID, session_factory=session_factory, hot_state=hot, tick_rate_ms=60_000
    )
    assert asyncio.run(mover.step(SimClock(tick_rate_ms=60_000).now())) == []


def test_step_towards_moves_fraction() -> None:
    from app.engine.movement import _step_towards

    # 由 (0,0) 朝東 (0,0.01)（約 1.11km）前進 0.5km → 落在中途（0 < lng < 0.01）。
    _lat, lng = _step_towards(0.0, 0.0, 0.0, 0.01, step_km=0.5)
    assert 0.0 < lng < 0.01
