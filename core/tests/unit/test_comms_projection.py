"""WP-C5 通聯後果的**投影層**（SPEC_FULL §6.2）：位置凍結、每陣營 STATE_DIFF、陣營姿態。

本檔只測純函數與廣播器；REST 端點的一致性在 `test_comms_api_projection.py`。

核心不變量：**真實位置永遠照常演進**，改變的只有「誰看得到什麼」。任何一條測試若能靠
「把熱狀態的 lat/lng 改掉」通過，那就是實作錯了——那會連裁決（射程/LOS）一起騙到。
"""

from __future__ import annotations

import asyncio
import json

import pytest
from fakeredis import FakeStrictRedis
from sqlalchemy.orm import Session, sessionmaker

from app.comms import (
    REPORT_LAT_KEY,
    REPORT_LNG_KEY,
    REPORT_TICK_KEY,
    IntelGranularity,
    LinkState,
    faction_link_state,
    intel_granularity,
    last_position_report,
    position_report_due,
    project_position,
)
from app.engine.clock import SimClock
from app.engine.comms import CommsSystem
from app.models import TacticalUnit, UnitLevel, WargameSession
from app.state.broadcaster import RedisBroadcaster, project_diff, public_diff
from app.state.hot_state import InMemoryHotState
from app.state.redis_stream import ring_key
from app.stream.faction_filter import is_visible

# --- 位置回報：純函數 ---


def _state(link: str, **extra: object) -> dict[str, object]:
    return {"comms_state": link, "lat": 24.0, "lng": 121.0, **extra}


def _reported(lat: float, lng: float, tick: int) -> dict[str, object]:
    return {REPORT_LAT_KEY: lat, REPORT_LNG_KEY: lng, REPORT_TICK_KEY: tick}


def test_online_needs_no_projection() -> None:
    """ONLINE ＝ 即時回報 → 投影器回 None，呼叫端照用真實座標。"""
    assert project_position(_state("ONLINE", **_reported(1.0, 2.0, 5))) is None


@pytest.mark.parametrize("link", ["OFFLINE", "DEGRADED"])
def test_non_online_uses_last_report(link: str) -> None:
    projected = project_position(_state(link, **_reported(23.5, 120.5, 40)))
    assert projected is not None
    assert (projected.lat, projected.lng, projected.stale_since_tick) == (23.5, 120.5, 40)


def test_offline_without_report_never_falls_back_to_truth() -> None:
    """沒有回報就是位置不明——**不得**拿真實座標回填（那就等於沒有凍結）。"""
    projected = project_position(_state("OFFLINE"))
    assert projected is not None
    assert (projected.lat, projected.lng, projected.stale_since_tick) == (None, None, None)


def test_missing_comms_state_is_optimistically_online() -> None:
    """該局沒在跑（熱狀態沒有 comms_state）→ 不投影，維持既有行為。"""
    assert project_position({"lat": 1.0, "lng": 2.0}) is None


def test_partial_report_is_not_a_report() -> None:
    """三欄不齊（例如只有座標沒有 tick）不算一筆回報。

    否則 stale_since_tick 會是 None 卻宣稱凍結。"""
    assert last_position_report({REPORT_LAT_KEY: 1.0, REPORT_LNG_KEY: 2.0}) is None
    assert last_position_report({**_reported(1.0, 2.0, 3), REPORT_TICK_KEY: "x"}) is None


def test_report_cadence_follows_link_state() -> None:
    """ONLINE 每次都回報、DEGRADED ×3 降頻、OFFLINE 永不。"""
    assert position_report_due(LinkState.ONLINE, 5, 5) is True
    assert position_report_due(LinkState.DEGRADED, 5, 5) is False  # 15 才到
    assert position_report_due(LinkState.DEGRADED, 15, 5) is True
    assert all(not position_report_due(LinkState.OFFLINE, t, 5) for t in (0, 5, 15, 100))


# --- 陣營整體姿態 ---


@pytest.mark.parametrize(
    ("links", "expected"),
    [
        ([], LinkState.ONLINE),  # 無單位不懲罰
        (["ONLINE", "ONLINE"], LinkState.ONLINE),
        (["ONLINE", "ONLINE", "OFFLINE"], LinkState.ONLINE),  # 2/3 仍過半
        (["ONLINE", "OFFLINE", "OFFLINE"], LinkState.DEGRADED),  # 1/3 未過半
        (["DEGRADED", "DEGRADED"], LinkState.DEGRADED),  # 一個 ONLINE 都沒有但非全斷
        (["OFFLINE", "OFFLINE"], LinkState.OFFLINE),  # 全斷 → 圖凍結
    ],
)
def test_faction_posture(links: list[str], expected: LinkState) -> None:
    assert faction_link_state(LinkState(link) for link in links) is expected


def test_posture_maps_to_granularity() -> None:
    assert intel_granularity(LinkState.ONLINE) is IntelGranularity.FULL
    assert intel_granularity(LinkState.DEGRADED) is IntelGranularity.COARSE
    assert intel_granularity(LinkState.OFFLINE) is IntelGranularity.FROZEN


# --- STATE_DIFF 每陣營投影 ---

_FACTIONS = {"b1": "BLUE", "b2": "BLUE", "r1": "RED", "g1": "GREEN"}


def _faction_for(uid: str) -> str:
    return _FACTIONS.get(uid, "")


def test_projected_diff_drops_units_of_invisible_factions() -> None:
    """WP-C5 之前 STATE_DIFF 沒有任何受眾標籤——敵軍即時座標是廣播給所有 client 的。"""
    diff = {"b1": {"lat": 1.0}, "r1": {"lat": 2.0}, "g1": {"lat": 3.0}}
    out = project_diff(
        diff,
        visible=frozenset({"BLUE", "GREEN"}),  # BLUE 與 GREEN 結盟
        faction_for=_faction_for,
        state_for=lambda _uid: {"comms_state": "ONLINE"},
    )
    assert set(out) == {"b1", "g1"}


def test_projected_diff_freezes_offline_units() -> None:
    states = {
        "b1": {"comms_state": "OFFLINE", **_reported(23.0, 120.0, 30)},
        "b2": {"comms_state": "ONLINE"},
    }
    out = project_diff(
        {"b1": {"lat": 99.0, "lng": 99.0}, "b2": {"lat": 5.0}},
        visible=frozenset({"BLUE"}),
        faction_for=_faction_for,
        state_for=states.get,
    )
    assert out["b1"] == {"lat": 23.0, "lng": 120.0}  # 真實的 99 沒有外洩
    assert out["b2"] == {"lat": 5.0}  # ONLINE 照送真實值


def test_frozen_unit_without_report_omits_coordinates_rather_than_nulling() -> None:
    """送 null 會把 client 上最後已知位置清掉＝單位憑空消失；移除欄位才是「凍結」。"""
    out = project_diff(
        {"b1": {"lat": 99.0, "lng": 99.0, "health": 80.0}},
        visible=frozenset({"BLUE"}),
        faction_for=_faction_for,
        state_for=lambda _uid: {"comms_state": "OFFLINE"},
    )
    assert out["b1"] == {"health": 80.0}


def test_stale_marker_only_on_transition() -> None:
    """狀態轉移那一 tick 才附 stale_since_tick；恢復 ONLINE 則送 None 清掉 client 的標記。"""
    lost = project_diff(
        {"b1": {"comms_state": "OFFLINE", "lat": 9.0}},
        visible=frozenset({"BLUE"}),
        faction_for=_faction_for,
        state_for=lambda _uid: {"comms_state": "OFFLINE", **_reported(23.0, 120.0, 30)},
    )
    assert lost["b1"]["stale_since_tick"] == 30
    regained = project_diff(
        {"b1": {"comms_state": "ONLINE", "lat": 9.0}},
        visible=frozenset({"BLUE"}),
        faction_for=_faction_for,
        state_for=lambda _uid: {"comms_state": "ONLINE"},
    )
    assert regained["b1"]["stale_since_tick"] is None
    # 沒有轉移的一般 tick 不重複附標記（否則每 tick 對每個斷聯單位送同一個值）。
    quiet = project_diff(
        {"b1": {"lat": 9.0}},
        visible=frozenset({"BLUE"}),
        faction_for=_faction_for,
        state_for=lambda _uid: {"comms_state": "OFFLINE", **_reported(23.0, 120.0, 30)},
    )
    assert "stale_since_tick" not in quiet["b1"]


def test_report_fields_never_reach_clients() -> None:
    """`report_*` 是投影的輸入，不是要下發的狀態——對陣營副本而言更是「凍結前的真實位置」。"""
    diff = {"b1": {**_reported(23.0, 120.0, 30)}}
    assert public_diff(diff) == {}  # 全知的真實副本也剝掉（契約沒有這些欄位）
    projected = project_diff(
        diff,
        visible=frozenset({"BLUE"}),
        faction_for=_faction_for,
        state_for=lambda _uid: {"comms_state": "ONLINE"},
    )
    assert projected == {}  # 整筆變空 → 該單位不進 STATE_DIFF（回報不製造廣播流量）


# --- 受眾標籤：exclusive 關掉全知旁通 ---


def test_exclusive_envelope_is_not_delivered_to_omniscient() -> None:
    faction_copy = {"factions": ["BLUE"], "exclusive": True}
    assert is_visible(faction_copy, "BLUE", omniscient=False) is True
    assert is_visible(faction_copy, "RED", omniscient=False) is False
    # 全知若也收得到，就會同時拿到「凍結版」與「真實版」兩份，先到先贏。
    assert is_visible(faction_copy, "WHITE_CELL", omniscient=True) is False


def test_truth_copy_reaches_only_omniscient() -> None:
    truth = {"factions": []}
    assert is_visible(truth, "WHITE_CELL", omniscient=True) is True
    assert is_visible(truth, "BLUE", omniscient=False) is False


def test_event_audience_still_bypassed_by_omniscient() -> None:
    """既有事件受眾（非 exclusive）的全知旁通不能被改壞。"""
    assert is_visible({"factions": ["BLUE", "RED"]}, "WHITE_CELL", omniscient=True) is True
    assert is_visible({"faction": "BLUE"}, "WHITE_CELL", omniscient=True) is True
    assert is_visible({}, "BLUE", omniscient=False) is True  # 全域訊息


# --- 廣播器：N+1 份信封 ---


def _ring(redis: FakeStrictRedis) -> list:  # type: ignore[type-arg]
    """讀出 ring buffer 內的信封（publish 與 ring 走同一條原子路徑，讀哪個都一樣）。"""
    return [json.loads(item) for item in redis.lrange(ring_key("s1"), 0, -1)]


def _broadcast(states: dict[str, dict[str, object]], diff: dict[str, dict[str, object]]) -> list:
    redis = FakeStrictRedis(decode_responses=True)
    caster = RedisBroadcaster(
        redis,
        "s1",
        _faction_for,
        observers=["BLUE", "RED"],
        visible_for=lambda f: frozenset({f}),
        state_for=states.get,
    )
    asyncio.run(caster.publish(3, diff))
    return _ring(redis)


def test_broadcaster_emits_truth_plus_one_copy_per_faction() -> None:
    envelopes = _broadcast(
        {"b1": {"comms_state": "OFFLINE", **_reported(23.0, 120.0, 30)}, "r1": {}},
        {"b1": {"lat": 99.0, "lng": 99.0}, "r1": {"lat": 5.0, "lng": 5.0}},
    )
    by_audience = {tuple(e.get("factions", ["?"])): e for e in envelopes}
    assert set(by_audience) == {(), ("BLUE",), ("RED",)}

    truth = by_audience[()]["payload"]["units"]
    assert {u["id"] for u in truth} == {"b1", "r1"}
    assert next(u for u in truth if u["id"] == "b1")["lat"] == 99.0  # 統裁看真實位置

    blue = by_audience[("BLUE",)]["payload"]["units"]
    assert [u["id"] for u in blue] == ["b1"]  # 看不到 RED
    assert blue[0]["lat"] == 23.0  # 自己的斷聯單位凍結在最後回報
    assert by_audience[("BLUE",)]["exclusive"] is True


def test_broadcaster_without_projection_wiring_keeps_single_envelope() -> None:
    """測試/合成想定沒接投影參數 → 維持單一全廣播信封（既有行為不被此卡改掉）。"""
    redis = FakeStrictRedis(decode_responses=True)
    asyncio.run(RedisBroadcaster(redis, "s1").publish(3, {"b1": {"lat": 1.0}}))
    envelopes = _ring(redis)
    assert len(envelopes) == 1
    assert "factions" not in envelopes[0]


# --- 產出端：CommsSystem 落位置回報 ---

_SID = "sess-c5"


def _seed(factory: sessionmaker[Session], units: list[tuple[str, str, float, float, UnitLevel]]):  # type: ignore[no-untyped-def]
    with factory() as db:
        db.add(WargameSession(id=_SID, name="C5", master_seed=1, current_weather={}))
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


def _run(factory: sessionmaker[Session], hot: InMemoryHotState, ticks: int) -> None:
    comms = CommsSystem(session_id=_SID, session_factory=factory, hot_state=hot, interval_ticks=5)
    clock = SimClock(tick_rate_ms=1000)
    for _ in range(ticks):
        asyncio.run(comms.evaluate(clock.now()))
        clock.advance()


def test_online_unit_reports_and_offline_unit_freezes(
    session_factory: sessionmaker[Session],
) -> None:
    """斷聯單位繼續移動，但它的回報停在失聯那一刻——真實 lat/lng 照常演進。"""
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
    frozen_at = last_position_report(hot.get_unit("far") or {})
    assert frozen_at is not None  # 開局即失聯也要有一筆（部署位置指揮所本來就知道）

    # 讓它繼續跑：真實座標變、回報不變。
    hot.update_unit("far", {"lat": 26.0, "lng": 124.0})
    _run(session_factory, hot, 11)
    state = hot.get_unit("far") or {}
    assert state["lat"] == 26.0  # 熱狀態的真實位置照常演進（裁決要用它）
    assert last_position_report(state) == frozen_at  # 但回報凍結

    projected = project_position(state)
    assert projected is not None
    assert (projected.lat, projected.lng) == (frozen_at.lat, frozen_at.lng)


def test_online_unit_report_tracks_truth(session_factory: sessionmaker[Session]) -> None:
    _seed(
        session_factory,
        [
            ("hq", "BLUE", 23.75, 121.20, UnitLevel.BATTALION),
            ("a", "BLUE", 23.751, 121.201, UnitLevel.PLATOON),
        ],
    )
    hot = InMemoryHotState()
    _run(session_factory, hot, 6)
    hot.update_unit("a", {"lat": 23.752, "lng": 121.202})
    _run(session_factory, hot, 11)
    report = last_position_report(hot.get_unit("a") or {})
    assert report is not None and (report.lat, report.lng) == (23.752, 121.202)
    assert project_position(hot.get_unit("a") or {}) is None  # ONLINE 不必投影
