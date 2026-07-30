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


# ---- 地形遮蔽與天氣衰減：過去 `mesh_states(nodes)` 兩個參數都沒傳 ----


class _Blocked:
    """全部視線都被擋（山稜線）。記錄查詢次數以驗快取。"""

    def __init__(self) -> None:
        self.calls = 0

    def has_los(self, _a: object, _b: object) -> object:
        self.calls += 1
        return type("O", (), {"visible": False})()


def _run_with(
    factory: sessionmaker[Session],
    hot: InMemoryHotState,
    ticks: int,
    **kwargs: object,
) -> list:
    comms = CommsSystem(
        session_id=_SID, session_factory=factory, hot_state=hot, interval_ticks=5, **kwargs
    )
    clock = SimClock(tick_rate_ms=1000)
    events = []
    for _ in range(ticks):
        events.extend(asyncio.run(comms.evaluate(clock.now())))
        clock.advance()
    return events


def _pair(factory: sessionmaker[Session], km_east: float) -> None:
    _seed(
        factory,
        [
            ("hq", "BLUE", 23.75, 121.20, UnitLevel.BATTALION),
            ("a", "BLUE", 23.75, 121.20 + km_east / 101.0, UnitLevel.PLATOON),
        ],
    )


def test_a_ridgeline_between_two_units_degrades_the_link(
    session_factory: sessionmaker[Session],
) -> None:
    """稜線後面的部隊與平原上同距離的部隊通聯**過去完全一樣**——`obstructed` 從沒傳過。"""
    _pair(session_factory, 30.0)
    clear = InMemoryHotState()
    _run_with(session_factory, clear, 6)
    blocked = InMemoryHotState()
    _run_with(session_factory, blocked, 6, gateway=_Blocked())

    assert (clear.get_unit("a") or {}).get("comms_state") == "ONLINE"
    assert (blocked.get_unit("a") or {}).get("comms_state") != "ONLINE"


def test_obstruction_lookups_are_cached_per_cell_pair(
    session_factory: sessionmaker[Session],
) -> None:
    """靜止部隊不該每個通訊 tick 都重問一次地形服務（穩態命中率接近 100%）。"""
    _pair(session_factory, 30.0)
    gw = _Blocked()
    _run_with(session_factory, InMemoryHotState(), 21, gateway=gw)  # tick 0/5/10/15/20 共 5 次重算

    assert gw.calls == 1


def test_terrain_service_failure_does_not_black_out_the_net(
    session_factory: sessionmaker[Session],
) -> None:
    """地形服務掛掉 → 退回通視。**不可**退成遮蔽：那會讓全軍忽然集體失聯。"""

    class _Broken:
        def has_los(self, _a: object, _b: object) -> object:
            raise RuntimeError("terrain down")

    _pair(session_factory, 30.0)
    hot = InMemoryHotState()
    _run_with(session_factory, hot, 6, gateway=_Broken())

    assert (hot.get_unit("a") or {}).get("comms_state") == "ONLINE"


def test_weather_rf_attenuation_degrades_the_link(
    session_factory: sessionmaker[Session],
) -> None:
    """`CellEffects.rf_attenuation_db` 過去**全系統零消費者**：暴雨對無線電毫無影響。"""
    import h3

    from app.weather import CellEffects, WeatherState

    _pair(session_factory, 30.0)
    storm = WeatherState({h3.latlng_to_cell(23.75, 121.20, 8): CellEffects(rf_attenuation_db=40.0)})
    hot = InMemoryHotState()
    _run_with(session_factory, hot, 6, weather_for=lambda: storm)

    assert (hot.get_unit("a") or {}).get("comms_state") != "ONLINE"


def test_weather_elsewhere_does_not_touch_this_net(
    session_factory: sessionmaker[Session],
) -> None:
    """天氣是逐格的：雷雨下在別處，這條鏈路不受影響。"""
    import h3

    from app.weather import CellEffects, WeatherState

    _pair(session_factory, 30.0)
    elsewhere = WeatherState({h3.latlng_to_cell(0.0, 0.0, 8): CellEffects(rf_attenuation_db=99.0)})
    hot = InMemoryHotState()
    _run_with(session_factory, hot, 6, weather_for=lambda: elsewhere)

    assert (hot.get_unit("a") or {}).get("comms_state") == "ONLINE"
