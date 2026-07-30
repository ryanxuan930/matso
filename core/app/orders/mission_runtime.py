"""任務執行期（WP-A2 卡 2）——每 tick 推進任務、送出子令、落階段事件。

## 分工

- `decomposer.step` 是**純函數**：吃 (任務, 狀態, 單位, 迷霧視圖) 吐 (下一狀態, 子令)。
- 本模組是它的 **I/O 邊界**：讀活任務、組迷霧視圖、把子令送進 `OrderService.submit`、落帳。

同 `msel_runtime` 的形狀：判斷（純）與副作用（I/O）分開，記憶進 checkpoint。

## 三個非做不可的紀律

1. **每道任務各自 try/except**。`kernel.run_tick` 對子系統的例外**沒有任何防護**——
   一個 raise 會讓 runner 崩潰，然後 `SimManager` 每 3 秒把它重建一次，形成無限重啟迴圈。
   一道壞任務不該拖垮整局。
2. **依 order id 排序後評估**。同一份輸入必得同一串子令（紅線 1）。
3. **記憶進 checkpoint**。不進的話重啟會讓每道任務退回 PLANNED 重跑一次——
   那正是 `MselMemory` 當初被建出來要避免的 bug。

## 階段事件寫 `ai_decision` 不寫 `detail`

`detail` 刻意不入 hash chain（見 `state/ledger.py` 的警語），是給非證據性診斷用的。
任務階段是 AAR 的任務時間軸要用的事實，得進得了雜湊鏈才算數。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from app.engine.clock import SimTime
from app.orders.decomposer import step as decompose_step
from app.orders.mission import MissionPayload, MissionPhase, MissionState
from app.state.ledger import LedgerEvent

_LOG = logging.getLogger("app.mission")

EVENT_PHASE_CHANGED = "MISSION_PHASE_CHANGED"
EVENT_FAILED = "MISSION_FAILED"
EVENT_ERROR = "MISSION_EVAL_FAILED"


@dataclass
class MissionMemory:
    """跨 tick 的任務狀態。**必須進 checkpoint**——不進的話重啟會讓每道任務退回 PLANNED。

    key ＝ 母令 order id。`MselMemory` 已經踩過這個坑（`MselEngine._fired` 曾是純記憶體
    的 set），這裡不重蹈。
    """

    states: dict[str, MissionState] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            oid: {
                "phase": st.phase.value,
                "waypoint_index": st.waypoint_index,
                "since_tick": st.since_tick,
            }
            for oid, st in sorted(self.states.items())
        }

    @staticmethod
    def from_dict(raw: Any) -> MissionMemory:
        """壞掉/缺失的輸入一律退回空記憶。

        舊 checkpoint 根本沒有這個鍵——**不能因此拋例外**，否則加了這個子系統之後
        所有既有快照都還原不了。
        """
        if not isinstance(raw, dict):
            return MissionMemory()
        states: dict[str, MissionState] = {}
        for oid, blob in raw.items():
            if not isinstance(blob, dict):
                continue
            try:
                states[str(oid)] = MissionState(
                    phase=MissionPhase(str(blob.get("phase") or MissionPhase.PLANNED)),
                    waypoint_index=int(blob.get("waypoint_index") or 0),
                    since_tick=int(blob.get("since_tick") or 0),
                )
            except (ValueError, TypeError):
                continue  # 認不得的階段字串 → 略過該道任務，不讓整份記憶還原不了
        return MissionMemory(states=states)


@dataclass(frozen=True, slots=True)
class ActiveMission:
    """一道進行中的任務令（由呼叫端從 DB 撈好餵進來——本模組不查庫）。"""

    order_id: str
    unit_id: str
    faction: str
    payload: MissionPayload


def evaluate(
    missions: list[ActiveMission],
    memory: MissionMemory,
    world_view_for: Any,
    now: SimTime,
) -> tuple[list[tuple[ActiveMission, list[Any]]], list[LedgerEvent]]:
    """**純評估**：推進每道任務，回 (要送出的子令, 帳本事件)。不做任何 I/O。

    `world_view_for(faction) -> dict` 由呼叫端提供（那裡有 DB 與熱狀態）。
    分解器本身看不到 DB——迷霧陷阱靠這個分層擋住（見 `decomposer` 的模組說明）。
    """
    to_submit: list[tuple[ActiveMission, list[Any]]] = []
    events: list[LedgerEvent] = []
    # **排序**：同一份輸入必得同一串子令（紅線 1）。
    for mission in sorted(missions, key=lambda m: m.order_id):
        try:
            before = memory.states.get(mission.order_id, MissionState())
            wv = world_view_for(mission.faction) or {}
            unit = _own_unit(wv, mission.unit_id)
            result = decompose_step(mission.payload, before, unit, wv, tick=now.tick)
        except Exception as exc:  # 一道壞任務不該拖垮整局——見模組說明第 1 點
            _LOG.exception("任務 %s 評估失敗", mission.order_id)
            events.append(
                LedgerEvent(
                    event_type=EVENT_ERROR,
                    tick=now.tick,
                    initiator_id=mission.unit_id,
                    ai_decision={"order_id": mission.order_id, "error": str(exc)},
                )
            )
            continue

        memory.states[mission.order_id] = result.state
        if result.state.phase is not before.phase:
            events.append(_phase_event(mission, before.phase, result, now))
        if result.orders:
            to_submit.append((mission, result.orders))
    return to_submit, events


def _phase_event(
    mission: ActiveMission, before: MissionPhase, result: Any, now: SimTime
) -> LedgerEvent:
    failed = result.state.phase is MissionPhase.FAILED
    return LedgerEvent(
        event_type=EVENT_FAILED if failed else EVENT_PHASE_CHANGED,
        tick=now.tick,
        initiator_id=mission.unit_id,
        # 階段是 AAR 任務時間軸的事實 → 進 `ai_decision`（入 hash chain），
        # 不進 `detail`（那是刻意排除在鏈外的非證據性診斷欄）。
        ai_decision={
            "order_id": mission.order_id,
            "mission_type": mission.payload.mission_type.value,
            "from_phase": before.value,
            "to_phase": result.state.phase.value,
            "note": result.note,
        },
    )


def _own_unit(world_view: dict[str, Any], unit_id: str) -> dict[str, Any]:
    """從迷霧視圖裡挑出這道任務的執行單位。

    找不到 → 回 `{"unit_id": ...}`（無座標），分解器會據此判失敗。
    **不查 DB 補位置**：那正是迷霧陷阱——單位看不見自己就是看不見（例如已被殲滅移出視圖）。
    """
    for u in world_view.get("own_units") or []:
        if str(u.get("unit_id")) == unit_id:
            return dict(u)
    return {"unit_id": unit_id}


__all__ = [
    "EVENT_ERROR",
    "EVENT_FAILED",
    "EVENT_PHASE_CHANGED",
    "ActiveMission",
    "MissionMemory",
    "evaluate",
]
