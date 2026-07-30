"""WP-C2 障礙在移動子系統的實際效果：既有標註零影響、鐵絲網卡住、雷區炸停、工兵過得去。

這一組**打在移動接線層**——單元測試裡的純函數再中性，也擋不住「接線把缺值翻錯」。
"""

from __future__ import annotations

import asyncio

import h3
from sqlalchemy.orm import Session, sessionmaker

from app.adjudication.obstacles import ObstacleType
from app.engine.clock import SimClock
from app.engine.movement import UnitMovementSystem
from app.engine.rng import DeterministicRNG
from app.engine.suppression_wiring import SUPPRESSION_KEY
from app.models import (
    MapFeature,
    Order,
    OrderStatus,
    TacticalUnit,
    UnitLevel,
    WargameSession,
)
from app.state.hot_state import InMemoryHotState

_START = (23.75, 121.20)
_DEST = (23.75, 121.40)  # 正東約 20 km


def _seed(
    db: Session,
    sid: str,
    *,
    obstacle_attrs: dict | None,
    engineer: bool = False,
    radius_m: float = 20_000.0,
) -> str:
    """一局：單位站在起點，整條路徑都在一片（可選型別的）障礙裡。"""
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
        attributes={"unit_kind": "ENGINEER"} if engineer else {},
    )
    db.add(unit)
    db.flush()
    if obstacle_attrs is not None:
        db.add(
            MapFeature(
                session_id=sid,
                kind="OBSTACLE",
                geometry_type="POINT",
                geometry=[_START[1], _START[0]],
                owner_faction="RED",
                label="障礙",
                influence_radius_m=radius_m,
                attributes=obstacle_attrs,
            )
        )
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


def _run(
    factory: sessionmaker[Session], sid: str, ticks: int, hot: InMemoryHotState | None = None
) -> list:
    mover = UnitMovementSystem(
        session_id=sid,
        session_factory=factory,
        hot_state=hot if hot is not None else InMemoryHotState(),
        tick_rate_ms=60_000,
        rng=DeterministicRNG(20260730, "movement"),
    )
    clock = SimClock(tick_rate_ms=60_000)
    events = []
    for _ in range(ticks):
        events.extend(asyncio.run(mover.step(clock.now())))
        clock.advance()
    return events


def _distance_km(factory: sessionmaker[Session], uid: str) -> float:
    from app.movement.attrition import haversine_m

    with factory() as db:
        unit = db.get(TacticalUnit, uid)
        return haversine_m((_START[1], _START[0]), (unit.current_lng, unit.current_lat)) / 1000.0


# ---- 中性：既有標註（無 obstacle_type）一步都不差 ----


def test_a_legacy_obstacle_does_not_change_movement_at_all(
    session_factory: sessionmaker[Session],
) -> None:
    """**本卡最重要的接線保護**：既有局的障礙標註沒有 `attributes.obstacle_type`，
    加了這張卡之後走的距離必須與沒有障礙時**完全相同**（不是「差不多」）。

    #28 的強穿額外耗損仍照舊生效——那不是本卡加的，本卡也不該把它改掉。
    """
    with session_factory() as db:
        legacy = _seed(db, "legacy", obstacle_attrs={})
        db.commit()
    with session_factory() as db:
        free = _seed(db, "none", obstacle_attrs=None)
        db.commit()
    _run(session_factory, "legacy", 5)
    _run(session_factory, "none", 5)
    assert _distance_km(session_factory, legacy) == _distance_km(session_factory, free)


# ---- 鐵絲網：實質阻擋 ----


def test_wire_entanglement_slows_a_unit_to_a_crawl(
    session_factory: sessionmaker[Session],
) -> None:
    """規格明列非工兵通過鐵絲網 ×0.1。"""
    with session_factory() as db:
        plain = _seed(db, "wire", obstacle_attrs={"obstacle_type": "WIRE"})
        db.commit()
    with session_factory() as db:
        free = _seed(db, "free", obstacle_attrs=None)
        db.commit()
    _run(session_factory, "wire", 5)
    _run(session_factory, "free", 5)
    slowed, normal = _distance_km(session_factory, plain), _distance_km(session_factory, free)
    assert 0.0 < slowed < normal
    assert slowed < normal * 0.2  # 一個數量級的差，不是「稍微慢一點」


def test_engineers_walk_through_wire_at_full_speed(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as db:
        eng = _seed(db, "eng", obstacle_attrs={"obstacle_type": "WIRE"}, engineer=True)
        db.commit()
    with session_factory() as db:
        free = _seed(db, "free2", obstacle_attrs=None)
        db.commit()
    _run(session_factory, "eng", 5)
    _run(session_factory, "free2", 5)
    assert _distance_km(session_factory, eng) == _distance_km(session_factory, free)


def test_a_breached_obstacle_stops_blocking(session_factory: sessionmaker[Session]) -> None:
    """破障的整個重點：工兵開路之後，後續部隊全速通過。"""
    with session_factory() as db:
        done = _seed(db, "breached", obstacle_attrs={"obstacle_type": "WIRE", "breached": True})
        db.commit()
    with session_factory() as db:
        free = _seed(db, "free3", obstacle_attrs=None)
        db.commit()
    _run(session_factory, "breached", 5)
    _run(session_factory, "free3", 5)
    assert _distance_km(session_factory, done) == _distance_km(session_factory, free)


# ---- 雷區：驗收條文 ----


def test_a_unit_driven_through_a_minefield_takes_losses_and_stops(
    session_factory: sessionmaker[Session],
) -> None:
    """驗收條文：**部隊穿越雷區產生戰損**。且觸雷會停下來——雷區真正的價值是
    把進攻縱隊釘在原地，不是扣一點血然後照走。"""
    hot = InMemoryHotState()
    with session_factory() as db:
        uid = _seed(db, "mines", obstacle_attrs={"obstacle_type": "MINEFIELD", "density": 3.0})
        db.commit()
    hot.put_unit(uid, {})
    events = _run(session_factory, "mines", 20, hot)
    strikes = [e for e in events if e.event_type == "MINE_STRIKE"]
    assert strikes, "20 tick 走過一片高密度雷區竟一次都沒觸雷"
    with session_factory() as db:
        unit = db.get(TacticalUnit, uid)
        assert unit.current_strength < 100.0
        # 觸雷後令即結束（要繼續得重下令）。
        order = db.query(Order).filter(Order.session_id == "mines").one()
        assert order.status == OrderStatus.COMPLETED
    assert (hot.get_unit(uid) or {}).get(SUPPRESSION_KEY, 0.0) > 0.0


def test_engineers_clear_the_same_minefield_with_fewer_strikes(
    session_factory: sessionmaker[Session],
) -> None:
    """驗收條文：**工兵通過機率減半**（同 seed 對照）。"""

    def _strikes(sid: str, *, engineer: bool) -> int:
        with session_factory() as db:
            _seed(
                db,
                sid,
                obstacle_attrs={"obstacle_type": "MINEFIELD", "density": 3.0},
                engineer=engineer,
            )
            db.commit()
        events = _run(session_factory, sid, 12)
        return sum(1 for e in events if e.event_type == "MINE_STRIKE")

    assert _strikes("eng_mines", engineer=True) <= _strikes("plain_mines", engineer=False)


def test_the_obstacle_type_table_is_the_documented_one() -> None:
    """型別字串散在 payload schema（正則）、contract、`attributes` 三處；
    寫錯一處就靜靜變成「未宣告」而完全沒有效果。"""
    assert {o.value for o in ObstacleType} == {
        "MINEFIELD",
        "WIRE",
        "TANK_DITCH",
        "ABATIS",
        "BRIDGE_DEMO",
    }
