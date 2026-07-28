"""通信戰術後果（#33b §6.2）——新指令 admit 閘門：OFFLINE 收不到、DEGRADED 延遲、ONLINE 即時。"""

from __future__ import annotations

import asyncio

from sqlalchemy.orm import Session, sessionmaker

from app.adjudication.adjudicator import EngageOrderSource
from app.comms import LinkState, order_admissible
from app.engine.clock import SimClock
from app.engine.movement import UnitMovementSystem
from app.models import Order, OrderStatus, TacticalUnit, UnitLevel, WargameSession
from app.state.hot_state import InMemoryHotState

_SID = "sess-comms-gate"


def test_order_admissible_by_state() -> None:
    # ONLINE 即時；OFFLINE 永不；DEGRADED 延遲 3 ticks（預設）。
    assert order_admissible(LinkState.ONLINE, issued_tick=5, now_tick=5)
    assert not order_admissible(LinkState.OFFLINE, issued_tick=5, now_tick=99)
    assert not order_admissible(LinkState.DEGRADED, issued_tick=5, now_tick=6)  # 才 1 tick
    assert order_admissible(LinkState.DEGRADED, issued_tick=5, now_tick=8)  # 3 ticks 到


def _seed(factory: sessionmaker[Session], order_type: str, payload: dict) -> str:  # type: ignore[type-arg]
    with factory() as db:
        db.add(WargameSession(id=_SID, name="通信閘門", master_seed=1, current_weather={}))
        db.flush()
        u = TacticalUnit(
            id="u1",
            session_id=_SID,
            designation="U1",
            unit_level=UnitLevel.PLATOON,
            faction="BLUE",
            current_lat=23.75,
            current_lng=121.20,
        )
        db.add(u)
        db.flush()
        db.add(
            Order(
                session_id=_SID,
                issuer_id="cmd",
                unit_id="u1",
                order_type=order_type,
                payload=payload,
                status=OrderStatus.VALIDATED,
                issued_at_tick=0,
            )
        )
        db.commit()
        return u.id


def test_offline_holds_move_order(session_factory: sessionmaker[Session]) -> None:
    _seed(session_factory, "MOVE", {"to_lat": 23.75, "to_lng": 121.30, "mobility_profile": "FOOT"})
    hot = InMemoryHotState()
    hot.update_unit("u1", {"comms_state": "OFFLINE"})
    mover = UnitMovementSystem(
        session_id=_SID, session_factory=session_factory, hot_state=hot, tick_rate_ms=60_000
    )
    clock = SimClock(tick_rate_ms=60_000)
    for _ in range(3):
        asyncio.run(mover.step(clock.now()))
        clock.advance()
    # OFFLINE → 指令留 VALIDATED（未執行）。
    with session_factory() as db:
        o = db.query(Order).filter(Order.session_id == _SID).first()
        assert o is not None and o.status == OrderStatus.VALIDATED


def test_online_admits_move_order(session_factory: sessionmaker[Session]) -> None:
    _seed(session_factory, "MOVE", {"to_lat": 23.75, "to_lng": 121.30, "mobility_profile": "FOOT"})
    hot = InMemoryHotState()
    hot.update_unit("u1", {"comms_state": "ONLINE"})
    mover = UnitMovementSystem(
        session_id=_SID, session_factory=session_factory, hot_state=hot, tick_rate_ms=60_000
    )
    asyncio.run(mover.step(SimClock(tick_rate_ms=60_000).now()))
    with session_factory() as db:
        o = db.query(Order).filter(Order.session_id == _SID).first()
        assert o is not None and o.status == OrderStatus.EXECUTING  # 已 admit 執行


def test_offline_holds_engage_order(session_factory: sessionmaker[Session]) -> None:
    _seed(session_factory, "ENGAGE", {"target_unit_id": "enemy"})
    hot = InMemoryHotState()
    hot.update_unit("u1", {"comms_state": "OFFLINE"})
    with session_factory() as db:
        src = EngageOrderSource(db, _SID, hot, SimClock(tick_rate_ms=60_000))
        cmds = asyncio.run(src.drain())
        assert cmds == []  # OFFLINE → 不 drain
        o = db.query(Order).filter(Order.session_id == _SID).first()
        assert o is not None and o.status == OrderStatus.VALIDATED


def test_online_drains_engage_order(session_factory: sessionmaker[Session]) -> None:
    _seed(session_factory, "ENGAGE", {"target_unit_id": "enemy"})
    hot = InMemoryHotState()
    hot.update_unit("u1", {"comms_state": "ONLINE"})
    with session_factory() as db:
        src = EngageOrderSource(db, _SID, hot, SimClock(tick_rate_ms=60_000))
        cmds = asyncio.run(src.drain())
        assert len(cmds) == 1 and cmds[0].shooter_id == "u1"
