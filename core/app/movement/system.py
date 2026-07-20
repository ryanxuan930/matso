"""移動執行子系統（O3.4，SPEC §2.3）——MOVE order → terrain path → 逐 tick 推進。

`MovementSystem.step(now)` 每 tick 做兩件事：
  1. **admit**：拉取本 session 的 VALIDATED MOVE 指令 → 規劃路徑 → 註冊 mission → 轉 EXECUTING。
  2. **advance**：讓每個 mission 沿路徑前進（speed 格/tick）、更新熱狀態位置、消耗油料；
     抵達終點 → COMPLETED；下一格不可通行（地形事件）→ 停在斷點 + MOVE_INTERRUPTED；
     油料耗盡 → MOVE_HALTED_FUEL。

依賴以 Protocol 注入（OrderStore / PathPlanner / passable），測試用假件即可，不需 DB/terrain。
確定性：admit 依 OrderStore 的排序、advance 依 unit_id 排序；無 RNG。單位位置只由本系統
（經 Kernel 呼叫）寫入熱狀態，符合 single-writer。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

import h3

from app.engine.clock import SimTime
from app.state.hot_state import HotStateStore
from app.state.ledger import LedgerEvent


@dataclass(frozen=True, slots=True)
class MoveCommand:
    order_id: str
    unit_id: str
    from_h3: str
    to_h3: str
    mobility_profile: str


class OrderStore(Protocol):
    """VALIDATED MOVE 指令的來源與狀態轉移（DB 版見 db_store.DbOrderStore）。"""

    def pending_moves(self, session_id: str) -> list[MoveCommand]: ...
    def mark_executing(self, order_id: str) -> None: ...
    def mark_completed(self, order_id: str, tick: int) -> None: ...


class PathPlanner(Protocol):
    """回傳含起訖的 h3 路徑；不可達回空清單（真版轉接 terrain get_path）。"""

    def plan(self, from_h3: str, to_h3: str, mobility_profile: str) -> list[str]: ...


@dataclass
class Mission:
    order_id: str
    unit_id: str
    path: list[str]  # h3 cells，index 0 = 起點
    index: int  # 單位目前位於 path[index]


class MovementSystem:
    def __init__(
        self,
        session_id: str,
        hot_state: HotStateStore,
        order_store: OrderStore,
        planner: PathPlanner,
        *,
        speed_hexes: int = 1,
        fuel_per_hex: float = 1.0,
        passable: Callable[[str], bool] = lambda _h: True,
    ) -> None:
        self._session_id = session_id
        self._hot = hot_state
        self._orders = order_store
        self._planner = planner
        self._speed = speed_hexes
        self._fuel_per_hex = fuel_per_hex
        self._passable = passable
        self._missions: dict[str, Mission] = {}

    async def step(self, now: SimTime) -> list[LedgerEvent]:
        events = self._admit(now)
        events.extend(self._advance(now))
        return events

    # ---------------- admit ----------------

    def _admit(self, now: SimTime) -> list[LedgerEvent]:
        events: list[LedgerEvent] = []
        for cmd in self._orders.pending_moves(self._session_id):
            self._orders.mark_executing(cmd.order_id)  # VALIDATED → EXECUTING
            path = self._planner.plan(cmd.from_h3, cmd.to_h3, cmd.mobility_profile)
            if len(path) <= 1:  # 已在終點或不可達 → 立即結束
                self._orders.mark_completed(cmd.order_id, now.tick)
                events.append(
                    self._event("MOVE_COMPLETED", cmd.unit_id, now, cmd.order_id, {"trivial": True})
                )
                continue
            self._missions[cmd.unit_id] = Mission(cmd.order_id, cmd.unit_id, path, index=0)
            self._set_position(cmd.unit_id, path[0])
            events.append(
                self._event("MOVE_STARTED", cmd.unit_id, now, cmd.order_id, {"path_len": len(path)})
            )
        return events

    # ---------------- advance ----------------

    def _advance(self, now: SimTime) -> list[LedgerEvent]:
        events: list[LedgerEvent] = []
        for mission in sorted(self._missions.values(), key=lambda m: m.unit_id):
            events.extend(self._advance_one(mission, now))
        return events

    def _advance_one(self, mission: Mission, now: SimTime) -> list[LedgerEvent]:
        for _ in range(self._speed):
            nxt = mission.index + 1
            if nxt >= len(mission.path):
                return [self._finish(mission, now, "MOVE_COMPLETED", {})]
            next_hex = mission.path[nxt]
            if not self._passable(next_hex):  # 地形事件 → 停在當前格
                return [self._finish(mission, now, "MOVE_INTERRUPTED", {"blocked_hex": next_hex})]
            fuel = self._get_fuel(mission.unit_id)
            if fuel is not None and fuel < self._fuel_per_hex:
                return [self._finish(mission, now, "MOVE_HALTED_FUEL", {"fuel": fuel})]
            if fuel is not None:
                self._set_fuel(mission.unit_id, fuel - self._fuel_per_hex)
            mission.index = nxt
            self._set_position(mission.unit_id, next_hex)
        return []  # 本 tick 尚未抵達

    def _finish(
        self, mission: Mission, now: SimTime, event_type: str, detail: dict[str, Any]
    ) -> LedgerEvent:
        self._orders.mark_completed(mission.order_id, now.tick)
        del self._missions[mission.unit_id]
        return self._event(
            event_type,
            mission.unit_id,
            now,
            mission.order_id,
            {"reached_h3": mission.path[mission.index], **detail},
        )

    # ---------------- 熱狀態 ----------------

    def _set_position(self, unit_id: str, cell: str) -> None:
        lat, lng = h3.cell_to_latlng(cell)
        self._hot.update_unit(unit_id, {"h3": cell, "lat": lat, "lng": lng})

    def _get_fuel(self, unit_id: str) -> float | None:
        state = self._hot.get_unit(unit_id)
        if state is None:
            return None
        fuel = state.get("fuel")
        return float(fuel) if fuel is not None else None

    def _set_fuel(self, unit_id: str, fuel: float) -> None:
        self._hot.update_unit(unit_id, {"fuel": fuel})

    def _event(
        self, event_type: str, unit_id: str, now: SimTime, order_id: str, detail: dict[str, Any]
    ) -> LedgerEvent:
        return LedgerEvent(
            event_type=event_type,
            tick=now.tick,
            initiator_id=unit_id,
            ai_decision={"order_id": order_id, **detail},
        )
