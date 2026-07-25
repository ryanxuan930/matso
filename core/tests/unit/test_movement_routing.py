"""#82 Phase C（執行器）：沿地形 A* 路徑前進（繞開不可通行）+ 任意點位精確終點 + 退回直線。"""

from __future__ import annotations

import asyncio

import h3
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.engine.clock import SimClock
from app.engine.movement import UnitMovementSystem, _haversine_km
from app.engine.rng import DeterministicRNG
from app.models import Order, OrderStatus, TacticalUnit, UnitLevel, WargameSession
from app.movement.router import ROUTE_RES
from app.state.hot_state import InMemoryHotState

_START = (23.75, 121.20)  # (lat, lng)
_DEST = (23.7512345, 121.2212345)  # 任意點位（非格心）


def _seed(db: Session, sid: str, payload: dict) -> str:
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
    db.add(
        Order(
            session_id=sid,
            issuer_id="u1",
            unit_id=unit.id,
            order_type="MOVE",
            payload=payload,
            status=OrderStatus.VALIDATED,
            issued_at_tick=0,
        )
    )
    return unit.id


def _detour_path(from_h3: str, to_h3: str, profile: str) -> tuple[list[str], bool]:
    """假 A*：經「北方繞行」的中間格（模擬繞開河流）→ 路徑明顯偏離直線。"""
    mid_lat = _START[0] + 0.02  # 往北繞
    mids = []
    for i in range(1, 4):
        lng = _START[1] + (_DEST[1] - _START[1]) * (i / 4)
        c = h3.latlng_to_cell(mid_lat, lng, ROUTE_RES)
        if c not in mids:
            mids.append(c)
    return [from_h3, *mids, to_h3], True


def _run(factory: sessionmaker[Session], sid: str, path_fn, ticks: int) -> list:
    mover = UnitMovementSystem(
        session_id=sid,
        session_factory=factory,
        hot_state=InMemoryHotState(),
        tick_rate_ms=60_000,
        speed_kmh=40.0,
        rng=DeterministicRNG(1, "movement"),
        path_fn=path_fn,
    )
    clock = SimClock(tick_rate_ms=60_000)
    events = []
    for _ in range(ticks):
        events.extend(asyncio.run(mover.step(clock.now())))
        clock.advance()
    return events


def _payload() -> dict:
    return {
        "to_h3": h3.latlng_to_cell(_DEST[0], _DEST[1], ROUTE_RES),
        "to_lat": _DEST[0],
        "to_lng": _DEST[1],
        "mobility_profile": "FOOT",
    }


def test_route_planned_and_followed(session_factory: sessionmaker[Session]) -> None:
    """規劃出地形路徑 → 記 MOVE_ROUTE_PLANNED，且單位確實往繞行方向（北）偏離直線。"""
    with session_factory() as db:
        uid = _seed(db, "route", _payload())
        db.commit()
    events = _run(session_factory, "route", _detour_path, 3)
    planned = [e for e in events if e.event_type == "MOVE_ROUTE_PLANNED"]
    assert len(planned) == 1 and planned[0].detail["legs"] >= 2
    with session_factory() as db:
        u = db.get(TacticalUnit, uid)
        # 直線移動會維持 lat≈起點；繞行路徑會把單位往北帶。
        assert u.current_lat > _START[0] + 0.001


def test_arrives_at_exact_destination_not_hex_center(
    session_factory: sessionmaker[Session],
) -> None:
    """任意點位 MUST：最終停在**精確目的地**，而非其所在 hex 的中心。"""
    with session_factory() as db:
        uid = _seed(db, "exact", _payload())
        db.commit()
    _run(session_factory, "exact", _detour_path, 400)  # 徒步慢，給足 tick
    with session_factory() as db:
        o = db.execute(select(Order).where(Order.session_id == "exact")).scalars().first()
        assert o.status == OrderStatus.COMPLETED
        u = db.get(TacticalUnit, uid)
        assert (u.current_lat, u.current_lng) == (_DEST[0], _DEST[1])  # 精確落點
        clat, clng = h3.cell_to_latlng(h3.latlng_to_cell(_DEST[0], _DEST[1], ROUTE_RES))
        assert (u.current_lat, u.current_lng) != (clat, clng)  # 未被吸附到格心


def test_unreachable_falls_back_and_records(session_factory: sessionmaker[Session]) -> None:
    """A* 不可達（如超出 hex 快取範圍）→ 退回直線 + MOVE_ROUTE_FALLBACK，移動不被否決。"""

    def _unreachable(from_h3: str, to_h3: str, profile: str) -> tuple[list[str], bool]:
        return [], False

    with session_factory() as db:
        uid = _seed(db, "fallback", _payload())
        db.commit()
    events = _run(session_factory, "fallback", _unreachable, 5)
    fb = [e for e in events if e.event_type == "MOVE_ROUTE_FALLBACK"]
    assert len(fb) == 1 and fb[0].detail["reason"] == "unreachable_fallback"
    with session_factory() as db:
        u = db.get(TacticalUnit, uid)
        assert _haversine_km(_START[0], _START[1], u.current_lat, u.current_lng) > 0  # 仍前進


def test_user_waypoints_are_not_overwritten(session_factory: sessionmaker[Session]) -> None:
    """使用者自訂 waypoints（刻意畫的路線）優先於自動規劃，不被覆寫。"""
    payload = {**_payload(), "waypoints": [[121.205, 23.75], [121.21, 23.75]]}
    with session_factory() as db:
        _seed(db, "userwp", payload)
        db.commit()
    events = _run(session_factory, "userwp", _detour_path, 2)
    assert not [e for e in events if e.event_type == "MOVE_ROUTE_PLANNED"]
    with session_factory() as db:
        o = db.execute(select(Order).where(Order.session_id == "userwp")).scalars().first()
        assert "_route_wp" not in (o.payload or {})


def test_no_path_fn_is_phase_b(session_factory: sessionmaker[Session]) -> None:
    """未注入 path_fn（無 terrain）→ 不規劃、維持既有直線（Phase A/B 行為）。"""
    with session_factory() as db:
        _seed(db, "nopath", _payload())
        db.commit()
    events = _run(session_factory, "nopath", None, 2)
    assert not [e for e in events if e.event_type.startswith("MOVE_ROUTE")]
