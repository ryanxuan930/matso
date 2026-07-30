"""任務時間軸（WP-A2 卡 4）——從帳本重建「每道任務走過哪些階段、各花了多久」。

**純函數**：吃 `AarEvent` 序列，吐時間軸。不碰 DB。

## 為什麼從事件重建而不是查任務的當前狀態

任務的當前狀態只回答「現在到哪一階段」；AAR 要回答的是「它**怎麼**走到這裡的」——
機動花了幾 tick、在哪一 tick 接敵、佔領後多久才構工完成。那些只存在於事件序列裡。

而且已結束的局根本沒有「當前狀態」可查（記憶活在 runner 行程），
從帳本重建是唯一在事後仍成立的做法。

## 迷霧

本模組**不做投影**——`aar/fog.py` 已經在事件進來之前處理過了（`project_events`）。
在這裡再做一次會是第二套規則，而兩套規則必然漂移。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from app.aar.events import AarEvent

_PHASE_CHANGED = "MISSION_PHASE_CHANGED"
_FAILED = "MISSION_FAILED"
_EVAL_FAILED = "MISSION_EVAL_FAILED"


@dataclass(frozen=True, slots=True)
class MissionLeg:
    """任務時間軸上的一段：從進入某階段到離開它。"""

    phase: str
    from_tick: int
    to_tick: int | None  # None ＝還在這個階段（局結束時仍未離開）
    note: str = ""

    @property
    def duration_ticks(self) -> int | None:
        return None if self.to_tick is None else self.to_tick - self.from_tick


@dataclass
class MissionTimeline:
    order_id: str
    mission_type: str
    unit_id: str | None
    legs: list[MissionLeg] = field(default_factory=list)
    failed: bool = False
    errors: int = 0  # 評估失敗次數（一道壞任務不該拖垮整局，但要看得見）

    def to_dict(self) -> dict[str, Any]:
        return {
            "order_id": self.order_id,
            "mission_type": self.mission_type,
            "unit_id": self.unit_id,
            "failed": self.failed,
            "errors": self.errors,
            "legs": [
                {
                    "phase": leg.phase,
                    "from_tick": leg.from_tick,
                    "to_tick": leg.to_tick,
                    "duration_ticks": leg.duration_ticks,
                    "note": leg.note,
                }
                for leg in self.legs
            ],
        }


def build_timelines(events: Sequence[AarEvent]) -> list[MissionTimeline]:
    """帳本 → 每道任務一條時間軸（依 order_id 排序，決定性）。

    `MISSION_PHASE_CHANGED` 帶 from/to；**第一則事件的 `from_phase` 就是起始階段**，
    所以不需要另外去猜任務從哪裡開始——那個資訊已經在事件裡了。
    """
    by_order: dict[str, MissionTimeline] = {}
    for event in events:
        if event.event_type not in (_PHASE_CHANGED, _FAILED, _EVAL_FAILED):
            continue
        decision = event.ai_decision or {}
        order_id = str(decision.get("order_id") or "")
        if not order_id:
            continue
        timeline = by_order.get(order_id)
        if timeline is None:
            timeline = MissionTimeline(
                order_id=order_id,
                mission_type=str(decision.get("mission_type") or "?"),
                unit_id=event.initiator_id,
            )
            by_order[order_id] = timeline

        if event.event_type == _EVAL_FAILED:
            timeline.errors += 1
            continue

        from_phase = str(decision.get("from_phase") or "")
        to_phase = str(decision.get("to_phase") or "")
        # 收掉上一段（若有）；沒有的話用 from_phase 補一段起始。
        if timeline.legs:
            last = timeline.legs[-1]
            timeline.legs[-1] = MissionLeg(last.phase, last.from_tick, event.tick, last.note)
        elif from_phase:
            timeline.legs.append(MissionLeg(from_phase, 0, event.tick))
        timeline.legs.append(
            MissionLeg(to_phase, event.tick, None, str(decision.get("note") or ""))
        )
        if event.event_type == _FAILED:
            timeline.failed = True
    return [by_order[k] for k in sorted(by_order)]


__all__ = ["MissionLeg", "MissionTimeline", "build_timelines"]
