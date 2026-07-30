"""任務級下令在活執行期的接線（WP-A2 收尾）。

## 這個檔案補的是什麼

WP-A2 交付了分解器（`orders/decomposer.py`）、執行期評估（`orders/mission_runtime.py`）、
LLM 橋接、AAR 時間軸與 COP 下令 UI，`Kernel` 也留好了 `mission_planner` 槽——
**但 `sim_runtime` 從來沒有傳過一個進去**，於是活執行期一直吃 `NoOpMissionPlanner`。

後果：MISSION 令收得下、預檢會過、狀態變 VALIDATED、指令列看得到——**然後什麼都不會發生**。
沒有子令、沒有階段轉移、沒有錯誤訊息。`mission_seize_60` golden 抓不到，因為它在
`core/tests/replay/scenarios.py` 裡自帶了一個 `_A2MissionPlanner`（純記憶體版），
釘住的是分解邏輯，不是**生產接線**。

⚠ 這與 WP-B2 記過的 MSEL 缺陷是同一類：**槽留好了、實作寫好了、就是沒有人把它接上**。
下次再看到「Protocol + NoOp 各一個，grep 不到第三個引用」，那就是這個病。

## 進度存在令上，不存在記憶體

`MissionMemory` 若只活在 planner 實例裡，runner 一重啟（`SimManager` 每 3 秒掃描重建）
任務就從 PLANNED 重跑一遍——SEIZE 會退回去走第一個航路點。故階段/航路點進度寫回
`Order.payload._mission_state`：與 MOVE 的 `_leg`、ENGINEER 的 `_work_until_tick` 同一套，
**checkpoint 與重啟自動涵蓋**，不必另開熱狀態鍵。

## 迷霧

`world_view` 走 `build_faction_context()`——與 LLM 指揮官看的是**同一份投影**。
自己組一份「反正分解器是確定性的」會直接違反紅線 3：分解器據以選擇接敵目標的敵情，
必須是該陣營真的偵測得到的那些。
"""

from __future__ import annotations

import logging
from dataclasses import asdict
from typing import Any

from app.ai_loop.context import build_faction_context
from app.ai_loop.world_view import projected_snapshot
from app.engine.clock import SimTime
from app.factions.relations import FactionRelations
from app.orders.mission import MissionPayload, MissionPhase, MissionState
from app.orders.mission_runtime import ActiveMission, MissionMemory, evaluate
from app.state.ledger import LedgerEvent

_LOG = logging.getLogger(__name__)

STATE_KEY = "_mission_state"

# 任務令進到這些階段就結束了（不再評估、不再產子令）。
_TERMINAL = (MissionPhase.COMPLETE, MissionPhase.FAILED)


class LiveMissionPlanner:
    """活執行期的 `MissionPlanner`：撈任務令 → 評估 → 送子令 → 記進度。

    掛在 Kernel 的 `mission_planner` 槽（tick 順序：裁決之後、移動之前——
    子令要在同一個 tick 內就被移動子系統看見，否則每一步都慢一拍）。
    """

    def __init__(
        self,
        db: Any,
        session_id: str,
        hot: Any,
        *,
        gateway: Any,
        relations: FactionRelations | None = None,
    ) -> None:
        self._db = db
        self._session_id = session_id
        self._hot = hot
        self._gateway = gateway
        self._relations = relations or FactionRelations()
        self._memory = MissionMemory()

    # ---- Kernel 介面 ----

    def plan(self, now: SimTime) -> list[LedgerEvent]:
        """一個 tick 的任務評估。**任何例外都不得往上拋**——`run_tick` 對子系統
        沒有任何防護，一個 raise 會讓 runner 崩潰後被 `SimManager` 每 3 秒重建。"""
        try:
            return self._plan(now)
        except Exception:
            _LOG.exception("任務規劃失敗（session=%s tick=%s）", self._session_id, now.tick)
            return []

    def _plan(self, now: SimTime) -> list[LedgerEvent]:

        missions, orders_by_id = self._load(now)
        if not missions:
            return []
        to_submit, events = evaluate(missions, self._memory, self._world_view, now)
        for mission, sub_orders in to_submit:
            events.extend(
                self._submit(mission, sub_orders, orders_by_id.get(mission.order_id), now)
            )
        # 進度寫回令（重啟/checkpoint 涵蓋）+ 終局階段收令。
        for mission in missions:
            order = orders_by_id.get(mission.order_id)
            if order is None:
                continue
            state = self._memory.states.get(mission.order_id)
            if state is None:
                continue
            payload = dict(order.payload or {})
            payload[STATE_KEY] = asdict(state)
            order.payload = payload
            if state.phase in _TERMINAL:
                events.extend(self._terminate(order, state, now))
        self._db.commit()
        return events

    def _terminate(self, order: Any, state: Any, now: SimTime) -> list[LedgerEvent]:
        """任務走到終局 → 收母令 **並收掉還在飛的子令**。

        ⚠ **這一段在 A2 收尾時漏了**：planner 直接把母令寫成 COMPLETED 就結束，
        `_cancel_children` 只掛在使用者按取消那條路上。於是任務結束（或失敗）之後，
        最後一道 MOVE 子令仍是 EXECUTING——**部隊照著一個已經結束的任務繼續走**。

        每道令**各自 try**：`next_status` 對非法轉移會拋（例：使用者剛好在同一個 tick
        取消了這道令），一個例外若往上冒就會讓**本 tick 所有任務**都停止規劃。
        """
        from app.models.enums import OrderStatus
        from app.orders.service import cancel_child_orders
        from app.orders.state_machine import next_status

        # ⚠ **一定要 `populate_existing=True` 重讀**。session factory 設了
        # `expire_on_commit=False`，而 runner 整局共用一條 Session：`db.get` 會直接命中
        # identity map 回傳**這條 Session 上次讀到的舊狀態**，一句 SQL 都不發。
        # 於是使用者在 API 行程剛取消的令，在這裡看起來仍是 EXECUTING，
        # `next_status` 順利通過 → **把 CANCELLED 靜靜覆寫成 COMPLETED**。
        fresh = self._db.get(type(order), order.id, populate_existing=True)
        if fresh is None:
            return []
        try:
            fresh.status = next_status(fresh.status, OrderStatus.COMPLETED)
        except Exception:
            # 母令已被別條路徑收走（多半是使用者取消）——子令由那條路徑負責，這裡不搶。
            _LOG.info("任務 %s 已由他處結案，略過終局處理", order.id)
            return []
        order = fresh
        order.resolved_at_tick = now.tick
        cancelled = cancel_child_orders(self._db, order.id)
        return [
            LedgerEvent(
                event_type="MISSION_ENDED",
                tick=now.tick,
                initiator_id=order.unit_id,
                ai_decision={
                    "mission_order_id": order.id,
                    "phase": state.phase.value,
                    "cancelled_sub_orders": cancelled,
                },
            )
        ]

    # ---- 撈令 ----

    def _load(self, now: SimTime) -> tuple[list[ActiveMission], dict[str, Any]]:
        """撈本局進行中的 MISSION 令，並把上次的進度讀回記憶。"""
        from sqlalchemy import select

        from app.models.enums import OrderStatus
        from app.models.tables import Order, TacticalUnit

        rows = self._db.scalars(
            select(Order)
            .where(
                Order.session_id == self._session_id,
                Order.status.in_([OrderStatus.VALIDATED, OrderStatus.EXECUTING]),
                Order.order_type == "MISSION",
            )
            .order_by(Order.issued_at_tick, Order.id)
        ).all()
        missions: list[ActiveMission] = []
        by_id: dict[str, Any] = {}
        for order in rows:
            unit = self._db.get(TacticalUnit, order.unit_id)
            if unit is None:
                continue
            payload = dict(order.payload or {})
            try:
                parsed = MissionPayload.model_validate(
                    {k: v for k, v in payload.items() if not k.startswith("_")}
                )
            except Exception:
                # 形狀壞掉的令：收令時就該擋下（`_PAYLOAD_MODELS` 有登錄 MISSION），
                # 走到這裡代表是手工塞的。判 REJECTED 而不是每 tick 重試同一個錯。
                _LOG.warning("MISSION 令 %s payload 無效，判 REJECTED", order.id)
                order.status = OrderStatus.REJECTED
                order.resolved_at_tick = now.tick
                continue
            # 首見（VALIDATED）→ 轉 EXECUTING；已有進度 → 讀回記憶。
            if order.status == OrderStatus.VALIDATED:
                order.status = OrderStatus.EXECUTING
            raw_state = payload.get(STATE_KEY)
            if isinstance(raw_state, dict) and order.id not in self._memory.states:
                self._memory.states[order.id] = _state_from(raw_state)
            missions.append(
                ActiveMission(
                    order_id=order.id,
                    unit_id=order.unit_id,
                    faction=unit.faction,
                    payload=parsed,
                )
            )
            by_id[order.id] = order
        return missions, by_id

    # ---- 迷霧投影後的世界（與 LLM 指揮官同一份）----

    def _world_view(self, faction: str) -> dict[str, Any]:
        from app.ai_loop.worker import ground_truth_enemies, load_unit_meta
        from app.ai_loop.world_view import faction_granularity

        snapshot = projected_snapshot(self._hot.get_all())
        unit_meta = load_unit_meta(self._db, self._session_id)
        enemies = ground_truth_enemies(
            self._db,
            self._session_id,
            faction,
            self._relations,
            faction_granularity(snapshot, unit_meta, faction),
        )
        return build_faction_context(
            faction=faction,
            tick=0,
            hot_snapshot=snapshot,
            unit_meta=unit_meta,
            known_enemies=enemies,
            relations=self._relations,
        )

    # ---- 送子令 ----

    def _submit(
        self, mission: ActiveMission, sub_orders: list[Any], parent: Any, now: SimTime
    ) -> list[LedgerEvent]:
        """把子令送進 `OrderService.submit`——**不繞過任何閘門**。回未受理的帳本事件。

        分解器產的是意圖，不是既成事實：子令一樣要過驗證、預檢、禁射區與 ROE。
        被打回**不拋例外**（下一個 tick 會依當下世界重新決定），但**一定要留下痕跡**：
        原本只記 INFO log 的版本讓「任務看起來在跑、實際上一步都不動」完全無法從畫面上
        察覺——而那正是這張卡要修的病。子令連續被打回是使用者最需要知道的一件事。
        """
        from app.orders.schemas import OrderRequest, OrderType
        from app.orders.service import OrderService

        if parent is None:
            return []
        rejected: list[LedgerEvent] = []
        # ⚠ 關係矩陣要一起傳。planner 選目標時用的是 `self._relations`（`_world_view`），
        # 子令的閘門若退回全 HOSTILE 預設，兩邊對「誰是盟軍」的認定就不一致。
        service = OrderService(self._db, self._gateway, relations=self._relations)
        for sub in sub_orders:
            try:
                otype = OrderType(sub.order_type)
            except ValueError:
                _LOG.warning("任務 %s 產出未知令型 %s", mission.order_id, sub.order_type)
                continue

            try:
                service.submit(
                    self._session_id,
                    OrderRequest(
                        unit_id=mission.unit_id,
                        order_type=otype,
                        payload=_hydrate(otype, sub.payload),
                    ),
                    parent.issuer_id,
                    parent_order_id=parent.id,
                )
            except Exception as exc:
                # 預檢打回、ROE 攔截、目標消失——都在這裡。**不是崩潰條件**。
                _LOG.info("任務 %s 的子令未受理：%s", mission.order_id, exc)
                rejected.append(
                    LedgerEvent(
                        event_type="MISSION_SUBORDER_REJECTED",
                        tick=now.tick,
                        initiator_id=mission.unit_id,
                        # 走 `ai_decision` 而非 `detail`：與 WP-A2 其餘任務事件同一個欄位
                        # （`detail` 不入雜湊鏈，任務的決策軌跡要能被追究）。
                        ai_decision={
                            "mission_order_id": mission.order_id,
                            "sub_order_type": sub.order_type,
                            "reason": str(exc),
                        },
                    )
                )
        return rejected


# 戰術預設解析度（與 terrain hex grid、`precheck._HEX_RES` 一致）。
_HEX_RES = 8


def _hydrate(order_type: Any, payload: dict[str, Any]) -> dict[str, Any]:
    """補上分解器**刻意不產**的欄位。

    `MovePayload.to_h3` 是必填，而 `decomposer` 只給 `to_lat/to_lng`——那不是疏漏：
    分解器的 import 被白名單鎖在 `{__future__, typing, app.orders.mission}`，
    它**不能** import h3（那條白名單是為了擋住「分解器偷看地形/DB」）。
    所以 latlng→hex 這一步的正確位置就是這裡，接線層。

    ⚠ 少了這一步，每一道 MOVE 子令都會在驗證層被打成「MOVE 載荷格式錯誤」，
    而 `_submit` 把那個例外記成 INFO 就吞掉了——任務看起來在跑，實際上一步都不動。
    """
    if order_type.value != "MOVE" or payload.get("to_h3"):
        return payload
    lat, lng = payload.get("to_lat"), payload.get("to_lng")
    if not isinstance(lat, (int, float)) or not isinstance(lng, (int, float)):
        return payload
    import h3

    return {**payload, "to_h3": h3.latlng_to_cell(float(lat), float(lng), _HEX_RES)}


def _opt_int(raw: object) -> int | None:
    """可選整數欄位。**None 與 0 是不同的意思**，故不能用 `or 0` 那一套。"""
    return int(raw) if isinstance(raw, (int, float)) else None


def _state_from(raw: dict[str, Any]) -> MissionState:
    """令上的進度 → `MissionState`。認不得的階段回 PLANNED（重跑好過崩潰）。"""
    try:
        phase = MissionPhase(str(raw.get("phase")))
    except ValueError:
        phase = MissionPhase.PLANNED
    return MissionState(
        phase=phase,
        waypoint_index=int(raw.get("waypoint_index") or 0),
        since_tick=int(raw.get("since_tick") or 0),
        withdrew_at_tick=_opt_int(raw.get("withdrew_at_tick")),
    )


__all__ = ["STATE_KEY", "LiveMissionPlanner"]
