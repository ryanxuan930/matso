"""AAR 任務時間軸（WP-A2 卡 4）——從帳本重建「怎麼走到這裡的」。"""

from __future__ import annotations

from app.aar.events import AarEvent
from app.aar.missions import build_timelines


def _phase(tick: int, order_id: str, frm: str, to: str, **kw: object) -> AarEvent:
    return AarEvent(
        seq=tick,
        tick=tick,
        event_type=str(kw.pop("event_type", "MISSION_PHASE_CHANGED")),
        initiator_id="b1",
        target_id=None,
        ai_decision={
            "order_id": order_id,
            "mission_type": "SEIZE",
            "from_phase": frm,
            "to_phase": to,
            **kw,
        },
    )


def test_timeline_reconstructs_the_phases_and_their_durations() -> None:
    """AAR 要回答的是「它**怎麼**走到這裡的」——機動幾 tick、哪一 tick 接敵。"""
    events = [
        _phase(3, "m1", "PLANNED", "MOVING"),
        _phase(11, "m1", "MOVING", "ENGAGING"),
        _phase(14, "m1", "ENGAGING", "CONSOLIDATING"),
        _phase(15, "m1", "CONSOLIDATING", "HOLDING", note="已佔領目標"),
    ]
    (t,) = build_timelines(events)
    assert t.order_id == "m1" and t.mission_type == "SEIZE" and t.unit_id == "b1"
    assert [leg.phase for leg in t.legs] == [
        "PLANNED",
        "MOVING",
        "ENGAGING",
        "CONSOLIDATING",
        "HOLDING",
    ]
    durations = {leg.phase: leg.duration_ticks for leg in t.legs}
    assert durations["MOVING"] == 8  # 3 → 11
    assert durations["ENGAGING"] == 3
    assert durations["HOLDING"] is None  # 局結束時仍在此階段
    assert t.legs[-1].note == "已佔領目標"
    assert not t.failed


def test_first_events_from_phase_supplies_the_starting_leg() -> None:
    """起始階段不必另外去猜——它就寫在第一則事件的 `from_phase` 裡。"""
    (t,) = build_timelines([_phase(5, "m1", "PLANNED", "MOVING")])
    assert t.legs[0].phase == "PLANNED" and t.legs[0].from_tick == 0
    assert t.legs[0].to_tick == 5


def test_failure_is_marked() -> None:
    events = [
        _phase(2, "m1", "PLANNED", "MOVING"),
        _phase(9, "m1", "MOVING", "FAILED", event_type="MISSION_FAILED", note="單位已被殲滅"),
    ]
    (t,) = build_timelines(events)
    assert t.failed and t.legs[-1].phase == "FAILED"


def test_evaluation_errors_are_counted_not_hidden() -> None:
    """一道壞任務不該拖垮整局，但**要看得見**——否則它只是靜靜什麼都沒做。"""
    events = [
        _phase(1, "m1", "PLANNED", "MOVING"),
        AarEvent(
            seq=2,
            tick=2,
            event_type="MISSION_EVAL_FAILED",
            initiator_id="b1",
            target_id=None,
            ai_decision={"order_id": "m1", "error": "boom"},
        ),
    ]
    (t,) = build_timelines(events)
    assert t.errors == 1
    assert [leg.phase for leg in t.legs] == ["PLANNED", "MOVING"]  # 錯誤不算階段


def test_multiple_missions_are_separated_and_ordered() -> None:
    events = [
        _phase(1, "m2", "PLANNED", "MOVING"),
        _phase(2, "m1", "PLANNED", "MOVING"),
    ]
    assert [t.order_id for t in build_timelines(events)] == ["m1", "m2"]


def test_non_mission_events_are_ignored() -> None:
    other = AarEvent(
        seq=1, tick=1, event_type="ENGAGEMENT_RESOLVED", initiator_id="b1", target_id="r1"
    )
    assert build_timelines([other]) == []


def test_events_without_an_order_id_are_skipped() -> None:
    """帳本裡的舊事件或壞資料不該生出一條沒有身分的時間軸。"""
    orphan = AarEvent(
        seq=1,
        tick=1,
        event_type="MISSION_PHASE_CHANGED",
        initiator_id="b1",
        target_id=None,
        ai_decision={"from_phase": "PLANNED", "to_phase": "MOVING"},
    )
    assert build_timelines([orphan]) == []
