"""任務執行期（WP-A2 卡 2）：階段事件、韌性、記憶往返、決定性。"""

from __future__ import annotations

from typing import Any

from app.engine.clock import SimTime
from app.orders.mission import MissionPayload, MissionPhase, MissionState, MissionType
from app.orders.mission_runtime import (
    EVENT_ERROR,
    EVENT_FAILED,
    EVENT_PHASE_CHANGED,
    ActiveMission,
    MissionMemory,
    evaluate,
)

_OBJ = {"lat": 24.0, "lng": 121.0}


def _now(tick: int = 1) -> SimTime:
    return SimTime(tick=tick, sim_time_ms=tick * 60_000)


def _mission(order_id: str = "m1", unit_id: str = "b1") -> ActiveMission:
    return ActiveMission(
        order_id=order_id,
        unit_id=unit_id,
        faction="BLUE",
        payload=MissionPayload(mission_type=MissionType.SEIZE, params={"objective": _OBJ}),
    )


def _wv_for(units: list[dict[str, Any]]):  # type: ignore[no-untyped-def]
    return lambda faction: {"own_units": units, "known_enemies": []}


def test_phase_change_emits_a_ledger_event() -> None:
    mem = MissionMemory()
    orders, events = evaluate(
        [_mission()], mem, _wv_for([{"unit_id": "b1", "lat": 24.05, "lng": 121.0}]), _now()
    )
    assert [e.event_type for e in events] == [EVENT_PHASE_CHANGED]
    assert events[0].ai_decision["from_phase"] == "PLANNED"
    assert events[0].ai_decision["to_phase"] == "MOVING"
    assert events[0].ai_decision["mission_type"] == "SEIZE"
    assert len(orders) == 1 and orders[0][1][0].order_type == "MOVE"


def test_phase_data_goes_into_the_hash_chain_not_detail() -> None:
    """`detail` 刻意不入 hash chain（非證據性診斷欄）。任務階段是 AAR 要用的事實。"""
    mem = MissionMemory()
    _, events = evaluate(
        [_mission()], mem, _wv_for([{"unit_id": "b1", "lat": 24.05, "lng": 121.0}]), _now()
    )
    assert events[0].ai_decision  # 有內容
    assert not events[0].detail  # 不放這裡


def test_no_event_when_the_phase_does_not_change() -> None:
    """還在路上的每一 tick 都落一則事件的話，帳本會被階段噪音淹掉。"""
    mem = MissionMemory(states={"m1": MissionState(MissionPhase.MOVING)})
    _, events = evaluate(
        [_mission()], mem, _wv_for([{"unit_id": "b1", "lat": 24.05, "lng": 121.0}]), _now()
    )
    assert events == []


def test_destroyed_unit_emits_mission_failed() -> None:
    mem = MissionMemory(states={"m1": MissionState(MissionPhase.MOVING)})
    dead = [{"unit_id": "b1", "lat": 24.05, "lng": 121.0, "status": "DESTROYED"}]
    _, events = evaluate([_mission()], mem, _wv_for(dead), _now())
    assert [e.event_type for e in events] == [EVENT_FAILED]


def test_a_broken_mission_does_not_take_down_the_tick() -> None:
    """**`kernel.run_tick` 對子系統的例外沒有任何防護**——一個 raise 會讓 runner 崩潰，
    然後 SimManager 每 3 秒把它重建一次，形成無限重啟迴圈。"""

    def exploding(faction: str) -> dict[str, Any]:
        raise RuntimeError("world view 壞了")

    mem = MissionMemory()
    good = _mission("m2", "b1")
    orders, events = evaluate([_mission("m1"), good], mem, exploding, _now())
    # 兩道都掛（world_view 對兩者都炸）→ 各落一則錯誤事件，但沒有例外逃出去。
    assert [e.event_type for e in events] == [EVENT_ERROR, EVENT_ERROR]
    assert orders == []


def test_one_broken_mission_leaves_the_others_running() -> None:
    calls: list[str] = []

    def selective(faction: str) -> dict[str, Any]:
        calls.append(faction)
        if len(calls) == 1:
            raise RuntimeError("第一道炸了")
        return {"own_units": [{"unit_id": "b1", "lat": 24.05, "lng": 121.0}], "known_enemies": []}

    mem = MissionMemory()
    orders, events = evaluate([_mission("m1"), _mission("m2")], mem, selective, _now())
    assert events[0].event_type == EVENT_ERROR
    assert events[1].event_type == EVENT_PHASE_CHANGED
    assert len(orders) == 1


def test_missions_are_evaluated_in_a_stable_order() -> None:
    """同一份輸入必得同一串子令（紅線 1）——評估序不可依賴 DB 回傳順序。"""
    units = [{"unit_id": "b1", "lat": 24.05, "lng": 121.0}]
    a = evaluate(
        [_mission("m3"), _mission("m1"), _mission("m2")], MissionMemory(), _wv_for(units), _now()
    )
    b = evaluate(
        [_mission("m1"), _mission("m2"), _mission("m3")], MissionMemory(), _wv_for(units), _now()
    )
    assert [m.order_id for m, _ in a[0]] == [m.order_id for m, _ in b[0]] == ["m1", "m2", "m3"]


# ---- 記憶進 checkpoint ----


def test_memory_round_trips() -> None:
    mem = MissionMemory(states={"m1": MissionState(MissionPhase.ENGAGING, 2, 41)})
    back = MissionMemory.from_dict(mem.to_dict())
    assert back.states["m1"] == mem.states["m1"]


def test_old_checkpoints_without_mission_memory_restore_cleanly() -> None:
    """**舊快照根本沒有這個鍵**——不能因此拋例外，否則加了子系統後所有既有快照都還原不了。"""
    for raw in (None, {}, "garbage", [1, 2, 3]):
        assert MissionMemory.from_dict(raw).states == {}


def test_unrecognised_phase_is_skipped_not_fatal() -> None:
    """認不得的階段字串 → 略過該道任務，不讓整份記憶還原不了。"""
    mem = MissionMemory.from_dict(
        {"m1": {"phase": "TELEPORTING"}, "m2": {"phase": "MOVING", "waypoint_index": 1}}
    )
    assert "m1" not in mem.states
    assert mem.states["m2"].phase is MissionPhase.MOVING


def test_unit_missing_from_the_fog_view_fails_the_mission_rather_than_querying_db() -> None:
    """單位看不見自己就是看不見（例如已被殲滅移出視圖）——**不查 DB 補位置**，那正是迷霧陷阱。"""
    mem = MissionMemory(states={"m1": MissionState(MissionPhase.MOVING)})
    _, events = evaluate([_mission()], mem, _wv_for([]), _now())
    assert [e.event_type for e in events] == [EVENT_FAILED]
