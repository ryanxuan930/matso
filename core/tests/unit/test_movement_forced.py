"""移動系統 #28 執行面：自訂 waypoints 逐段前進 + 強穿阻礙的隨機加成耗損（決定性）。"""

from __future__ import annotations

import asyncio

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.engine.clock import SimClock
from app.engine.movement import UnitMovementSystem
from app.engine.rng import DeterministicRNG
from app.models import MapFeature, Order, OrderStatus, TacticalUnit, UnitLevel, WargameSession
from app.state.hot_state import InMemoryHotState

_SID = "sess-forced"
_START = (23.75, 121.20)  # (lat, lng)


def _seed(factory: sessionmaker[Session], payload: dict, *, obstacle: bool) -> str:
    with factory() as db:
        db.add(WargameSession(id=_SID, name="強穿測試", master_seed=7, current_weather={}))
        db.flush()
        unit = TacticalUnit(
            session_id=_SID,
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
        if obstacle:
            # 障礙方塊擋在 121.20→121.30 東向路徑中段。
            db.add(
                MapFeature(
                    session_id=_SID,
                    kind="OBSTACLE",
                    geometry_type="POLYGON",
                    geometry=[
                        [121.245, 23.745],
                        [121.255, 23.745],
                        [121.255, 23.755],
                        [121.245, 23.755],
                    ],
                    owner_faction="WHITE_CELL",
                    label="雷區",
                )
            )
        db.add(
            Order(
                session_id=_SID,
                issuer_id="u1",
                unit_id=unit.id,
                order_type="MOVE",
                payload=payload,
                status=OrderStatus.VALIDATED,
                issued_at_tick=0,
            )
        )
        db.commit()
        return unit.id


def _run(factory: sessionmaker[Session], hot: InMemoryHotState, ticks: int) -> list:
    rng = DeterministicRNG(7, "movement")
    mover = UnitMovementSystem(
        session_id=_SID,
        session_factory=factory,
        hot_state=hot,
        tick_rate_ms=60_000,
        speed_kmh=40.0,
        rng=rng,
    )
    clock = SimClock(tick_rate_ms=60_000)
    events = []
    for _ in range(ticks):
        events.extend(asyncio.run(mover.step(clock.now())))
        clock.advance()
    return events


def test_forced_crossing_applies_attrition(session_factory: sessionmaker[Session]) -> None:
    uid = _seed(
        session_factory,
        {"to_lat": 23.75, "to_lng": 121.30, "mobility_profile": "FOOT"},
        obstacle=True,
    )
    hot = InMemoryHotState()
    events = _run(session_factory, hot, 40)
    attr = [e for e in events if e.event_type == "MOVE_ATTRITION"]
    assert len(attr) == 1  # 只在 admit 擲一次
    assert attr[0].detail["reason"] == "FORCED_CROSSING"
    with session_factory() as db:
        u = db.get(TacticalUnit, uid)
        assert u is not None and u.current_strength < 100.0  # 戰力被扣


def test_no_obstacle_no_attrition(session_factory: sessionmaker[Session]) -> None:
    uid = _seed(
        session_factory,
        {"to_lat": 23.75, "to_lng": 121.30, "mobility_profile": "FOOT"},
        obstacle=False,
    )
    hot = InMemoryHotState()
    events = _run(session_factory, hot, 40)
    assert not [e for e in events if e.event_type == "MOVE_ATTRITION"]
    with session_factory() as db:
        u = db.get(TacticalUnit, uid)
        assert u is not None and u.current_strength == 100.0


def test_custom_waypoints_follow_and_complete(session_factory: sessionmaker[Session]) -> None:
    # 兩段折線：先往東北一點，再往正東；無阻礙。
    uid = _seed(
        session_factory,
        {"waypoints": [[121.24, 23.76], [121.28, 23.76]], "mobility_profile": "FOOT"},
        obstacle=False,
    )
    hot = InMemoryHotState()
    _run(session_factory, hot, 60)
    with session_factory() as db:
        o = db.execute(select(Order).where(Order.session_id == _SID)).scalars().first()
        assert o is not None and o.status == OrderStatus.COMPLETED
        u = db.get(TacticalUnit, uid)
        assert u is not None
        # 抵達最後一個 waypoint 附近。
        assert abs(u.current_lng - 121.28) < 0.01 and abs(u.current_lat - 23.76) < 0.01


def test_deterministic_replay_same_seed(session_factory: sessionmaker[Session]) -> None:
    payload = {"to_lat": 23.75, "to_lng": 121.30, "mobility_profile": "FOOT"}
    uid = _seed(session_factory, payload, obstacle=True)
    hot = InMemoryHotState()
    events = _run(session_factory, hot, 5)
    loss1 = next(e.damage_calc for e in events if e.event_type == "MOVE_ATTRITION")
    # 同 seed 重跑（重建同一世界）→ 相同耗損。
    with session_factory() as db:
        db.query(Order).delete()
        db.query(TacticalUnit).delete()
        db.query(MapFeature).delete()
        db.query(WargameSession).delete()
        db.commit()
    uid2 = _seed(session_factory, payload, obstacle=True)
    events2 = _run(session_factory, InMemoryHotState(), 5)
    loss2 = next(e.damage_calc for e in events2 if e.event_type == "MOVE_ATTRITION")
    assert loss1 == loss2 and uid != uid2
