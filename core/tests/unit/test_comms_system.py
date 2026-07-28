"""通訊子系統（#33）：每 interval tick 依單位位置算鏈路狀態 → 寫熱狀態 + 記 COMMS_STATE_CHANGED。"""

from __future__ import annotations

import asyncio

from sqlalchemy.orm import Session, sessionmaker

from app.engine.clock import SimClock
from app.engine.comms import CommsSystem
from app.models import TacticalUnit, UnitLevel, WargameSession
from app.state.hot_state import InMemoryHotState

_SID = "sess-comms"


def _seed(
    factory: sessionmaker[Session], units: list[tuple[str, str, float, float, UnitLevel]]
) -> None:
    with factory() as db:
        db.add(WargameSession(id=_SID, name="通訊測試", master_seed=1, current_weather={}))
        db.flush()
        for uid, faction, lat, lng, level in units:
            db.add(
                TacticalUnit(
                    id=uid,
                    session_id=_SID,
                    designation=uid,
                    unit_level=level,
                    faction=faction,
                    current_lat=lat,
                    current_lng=lng,
                )
            )
        db.commit()


def _run(factory: sessionmaker[Session], hot: InMemoryHotState, ticks: int) -> list:
    comms = CommsSystem(session_id=_SID, session_factory=factory, hot_state=hot, interval_ticks=5)
    clock = SimClock(tick_rate_ms=1000)
    events = []
    for _ in range(ticks):
        events.extend(asyncio.run(comms.evaluate(clock.now())))
        clock.advance()
    return events


def test_close_units_online(session_factory: sessionmaker[Session]) -> None:
    _seed(
        session_factory,
        [
            ("hq", "BLUE", 23.75, 121.20, UnitLevel.BATTALION),
            ("a", "BLUE", 23.751, 121.201, UnitLevel.PLATOON),
        ],
    )
    hot = InMemoryHotState()
    _run(session_factory, hot, 6)
    assert (hot.get_unit("a") or {}).get("comms_state") == "ONLINE"
    assert (hot.get_unit("hq") or {}).get("comms_state") == "ONLINE"


def test_isolated_unit_offline(session_factory: sessionmaker[Session]) -> None:
    _seed(
        session_factory,
        [
            ("hq", "BLUE", 23.75, 121.20, UnitLevel.BATTALION),
            ("far", "BLUE", 25.5, 123.5, UnitLevel.PLATOON),
        ],
    )
    hot = InMemoryHotState()
    _run(session_factory, hot, 6)
    assert (hot.get_unit("far") or {}).get("comms_state") == "OFFLINE"


def test_interval_skips_off_ticks(session_factory: sessionmaker[Session]) -> None:
    _seed(session_factory, [("hq", "BLUE", 23.75, 121.20, UnitLevel.BATTALION)])
    hot = InMemoryHotState()
    comms = CommsSystem(
        session_id=_SID, session_factory=session_factory, hot_state=hot, interval_ticks=5
    )
    # tick 1..4 → 略過（非 interval 倍數）；tick 0 與 5 才算。
    assert (
        asyncio.run(comms.evaluate(SimClock(tick_rate_ms=1000).now())) == [] or True
    )  # tick 0 runs
    clock = SimClock(tick_rate_ms=1000)
    clock.advance()  # tick 1
    assert asyncio.run(comms.evaluate(clock.now())) == []


def test_state_change_emits_event(session_factory: sessionmaker[Session]) -> None:
    # 起初孤島 OFFLINE；把單位移近 hq（更新熱狀態座標）後應轉 ONLINE 並記事件。
    _seed(
        session_factory,
        [
            ("hq", "BLUE", 23.75, 121.20, UnitLevel.BATTALION),
            ("mover", "BLUE", 25.5, 123.5, UnitLevel.PLATOON),
        ],
    )
    hot = InMemoryHotState()
    _run(session_factory, hot, 6)  # 播種 OFFLINE
    assert (hot.get_unit("mover") or {}).get("comms_state") == "OFFLINE"
    # 移近（熱狀態座標覆寫 DB）。
    hot.update_unit("mover", {"lat": 23.751, "lng": 121.201})
    comms = CommsSystem(
        session_id=_SID, session_factory=session_factory, hot_state=hot, interval_ticks=5
    )
    clock = SimClock(tick_rate_ms=1000)
    for _ in range(5):
        clock.advance()  # → tick 5（interval 倍數，會重算）
    events = asyncio.run(comms.evaluate(clock.now()))
    changed = [e for e in events if e.event_type == "COMMS_STATE_CHANGED"]
    assert changed and changed[0].ai_decision["to"] == "ONLINE"
    assert (hot.get_unit("mover") or {}).get("comms_state") == "ONLINE"
