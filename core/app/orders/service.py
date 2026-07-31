"""Order pipeline 編排（O3.1，SPEC §2.3 步驟 [1]–[2]）。

submit：validate → 物理預檢 → 持久化（PENDING→VALIDATED 或 →REJECTED，經狀態機）。
cancel：使用者取消未執行指令（→CANCELLED）。

issued_at_tick 由注入的 tick_source 提供——kernel↔API 整合（後續卡）時改讀活的 SimClock；
O3.1 預設回 0（尚無運行中的 kernel 綁定）。
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.c2.service import expend_request
from app.errors import IllegalOrderTransitionError, OrderNotFoundError, PrecheckFailedError
from app.factions import FactionRelations
from app.models.enums import OrderStatus
from app.models.tables import Order, TacticalUnit
from app.orders.precheck import PhysicsGateway, precheck_error_code, run_precheck
from app.orders.schemas import (
    EngagePayload,
    FireMissionPayload,
    OrderRequest,
    OrderResponse,
    OrderType,
    PrecheckResult,
)
from app.orders.state_machine import is_user_cancellable, next_status
from app.orders.validator import validate_order
from app.state.ledger import LedgerEvent


class OrderService:
    def __init__(
        self,
        db: Session,
        gateway: PhysicsGateway,
        tick_source: Callable[[], int] = lambda: 0,
        relations: FactionRelations | None = None,
        event_sink: Any = None,
        publisher: Callable[[str, dict[str, Any], str | None], None] | None = None,
    ) -> None:
        self._db = db
        self._gateway = gateway
        self._tick_source = tick_source
        self._relations = relations  # None → 全 HOSTILE（O7 scenario 載入實際矩陣）
        # WP-A3：限制射擊區 override 的留痕出口（LedgerWriter）。None＝不落帳（測試/舊呼叫端）。
        self._event_sink = event_sink
        # **落帳與廣播是兩件事。** `LedgerWriter` 全檔沒有一行 redis——寫進帳本的事件
        # 不會自己出現在戰況 feed 上。過去這兩種事件因此只活在 DB 裡：
        # 前端的中文標籤備好了，一次都不會被渲染。
        # `(event_type, payload, faction)` → 推播；None＝不推（測試/合成想定）。
        self._publisher = publisher

    def _broadcast(self, event_type: str, payload: dict[str, Any], faction: str | None) -> None:
        """把事件推上戰況 feed。**推播失敗不可以讓下令失敗**——令的結果已經在回應裡了。"""
        if self._publisher is None:
            return
        with contextlib.suppress(Exception):
            self._publisher(event_type, payload, faction)

    def submit(
        self,
        session_id: str,
        req: OrderRequest,
        issuer_id: str,
        *,
        parent_order_id: str | None = None,
    ) -> OrderResponse:
        """驗證 + 預檢 + 落庫。不可行 → 持久化 REJECTED 後拋 PrecheckFailedError（API 轉 422）。

        `parent_order_id`（WP-A2）＝這道令是哪一道 MISSION 分解出來的。
        **子令走的是與人工下令完全相同的這條路**——一樣過驗證、預檢、禁射區、ROE、席位。
        分解器不繞過任何閘門；讓子令走側門正是護欄鏈會出現漏洞的方式（紅線 3 的精神）。
        """
        validated = validate_order(self._db, session_id, req, issuer_id)
        # 去重（補充 2d）：多機同時查看/操作時，同單位 + 同型別 + 同 payload 的未終結指令若已存在，
        # 採先到先處理、忽略後到重複——回既有指令（idempotent），不重複落庫。驗證在前確保授權無誤。
        dup = self._find_active_duplicate(session_id, req, parent_order_id)
        if dup is not None:
            return _to_response(dup, _precheck_of(dup))
        precheck = run_precheck(
            self._db,
            validated,
            self._gateway,
            self._relations,
            acknowledge_restricted=req.acknowledge_restricted,
        )

        # WP-B5.3：火協核准單在**令被收下時**兌現，不是在裁決命中時。
        # 一張核准單對應「一次火力任務」的授權；令被接受＝授權已使用。
        # 若等命中才扣，令被取消或未命中時授權會憑空復活。
        #
        # **FIRE_MISSION 必須一起收**（WP-C10.2）：FireMissionPayload 不是 EngagePayload 的子類，
        # 只判 EngagePayload 的話，同一張核准單可以無限次掛在面射擊令上——
        # 預檢擋得住「沒核准單」，擋不住「一張單用一百次」。
        if precheck.feasible and isinstance(validated.payload, (EngagePayload, FireMissionPayload)):
            rid = validated.payload.fire_request_id
            if rid:
                expend_request(self._db, rid)
        order = Order(
            session_id=session_id,
            issuer_id=issuer_id,
            unit_id=req.unit_id,
            order_type=req.order_type.value,
            payload=req.payload,
            status=OrderStatus.PENDING,
            precheck=precheck.model_dump(),
            issued_at_tick=self._tick_source(),
            parent_order_id=parent_order_id,
        )
        target = OrderStatus.VALIDATED if precheck.feasible else OrderStatus.REJECTED
        order.status = next_status(order.status, target)  # PENDING → VALIDATED / REJECTED
        self._db.add(order)
        self._db.commit()

        # WP-A3：限制射擊區的知情放行必須留痕——AAR 要能追究「誰在什麼時候明知而為」。
        if precheck.feasible and req.acknowledge_restricted and self._event_sink is not None:
            overridden = any(c.name == "no_strike" and c.passed for c in precheck.checks)
            if overridden:
                self._event_sink.append(
                    session_id,
                    [
                        LedgerEvent(
                            event_type="ORDER_RESTRICTED_FIRE_OVERRIDE",
                            tick=order.issued_at_tick or 0,
                            initiator_id=req.unit_id,
                            target_id=str(req.payload.get("target_unit_id") or "") or None,
                            ai_decision={
                                "order_id": order.id,
                                "issuer_id": issuer_id,
                                "reason": "下令者明確確認於限制射擊區射擊",
                            },
                        )
                    ],
                )
                # 明知而為的射擊要**當場**讓同陣營看到，不是等 AAR 才追究。
                self._broadcast(
                    "ORDER_RESTRICTED_FIRE_OVERRIDE",
                    {
                        "tick": order.issued_at_tick or 0,
                        "initiator_id": req.unit_id,
                        "order_id": order.id,
                        "issuer_id": issuer_id,
                    },
                    validated.unit.faction,
                )

        if not precheck.feasible:
            # **人工下令被護欄擋下也要落帳。**
            # `GUARDRAIL_INTERVENTION` 只給 AI 護欄用，於是 `/aar/stats` 的
            # guardrail_blocks 對人工下令結構性恆為 0——一筆「有人想砲擊醫院」的
            # 紀錄只活在 Order 列裡，AAR 讀不到，行動後檢討就追究不了。
            #
            # 事件帶 issuer 與失敗的檢查項名：檢討會問的是「誰、在第幾 tick、
            # 想做什麼、被哪一條擋下」，四個都要答得出來。
            # ⚠ **落帳失敗不可以把 422 變成 500**：下令被拒的理由使用者一定要看得到，
            # 而拒絕本身已經寫進 Order 列（status=REJECTED + precheck）——帳本是給
            # 行動後檢討用的**第二份**紀錄。第二份寫不成不該毀掉第一份的送達。
            failed = [c.name for c in precheck.checks if not c.passed]
            if self._event_sink is not None:
                with contextlib.suppress(Exception):
                    self._event_sink.append(
                        session_id,
                        [
                            LedgerEvent(
                                event_type="ORDER_REJECTED",
                                tick=order.issued_at_tick or 0,
                                initiator_id=req.unit_id,
                                target_id=str(req.payload.get("target_unit_id") or "") or None,
                                ai_decision={
                                    "order_id": order.id,
                                    "issuer_id": issuer_id,
                                    "order_type": order.order_type,
                                    "failed_checks": failed,
                                    "reason": precheck.reason or "",
                                    # FIRE_MISSION 打的是座標，target_id 為 None——把落點帶上，
                                    # 否則帳本上看得到「被禁射區擋下」卻看不到擋在哪裡。
                                    "target_lat": req.payload.get("target_lat"),
                                    "target_lng": req.payload.get("target_lng"),
                                },
                            )
                        ],
                    )
            # **人工下令被護欄擋下要當場看得到。** 一筆「有人想砲擊醫院」
            # 只寫進 Order 列與帳本的話，統裁要等到檢討會才知道——而那正是
            # 他當下最該介入的時刻。受眾限同陣營（敵軍不該知道我方被什麼擋住）。
            #
            # ⚠ 這一段**刻意不掛在 `event_sink is not None` 底下**：落帳與廣播是
            # 兩個獨立的出口，把廣播綁在帳本上等於重犯這張卡在修的那個錯。
            self._broadcast(
                "ORDER_REJECTED",
                {
                    "tick": order.issued_at_tick or 0,
                    "initiator_id": req.unit_id,
                    "order_id": order.id,
                    "order_type": order.order_type,
                    "reason": (failed[0] if failed else ""),
                    "reason_detail": precheck.reason or "",
                },
                validated.unit.faction,
            )

        if not precheck.feasible:
            raise PrecheckFailedError(
                precheck.reason or "物理預檢不可行",
                error_code=precheck_error_code(precheck),
                details={"order_id": order.id, "precheck": precheck.model_dump()},
            )
        return _to_response(order, precheck)

    def _find_active_duplicate(
        self, session_id: str, req: OrderRequest, parent_order_id: str | None = None
    ) -> Order | None:
        """尋找同單位 + 同型別 + 同 payload + **同母令**的未終結指令。

        `parent_order_id` 進去重鍵是刻意的（WP-A2）：兩道不同的任務可能對同一個單位分解出
        座標完全相同的 MOVE（例如兩張任務都要它先到同一個集結點）。不分母令的話，
        後一道任務會拿到前一道的子令當成自己的，於是**取消前一道任務會連帶取消後一道的子令**。

        反過來，同一道任務**重複分解出同一道子令則應該被去重**——分解器每 tick 都會跑，
        而它在「還在路上」時就是會重覆算出同一個目標點。
        """
        active = (OrderStatus.PENDING, OrderStatus.VALIDATED, OrderStatus.EXECUTING)
        existing = (
            self._db.execute(
                select(Order).where(
                    Order.session_id == session_id,
                    Order.unit_id == req.unit_id,
                    Order.order_type == req.order_type.value,
                    Order.status.in_(active),
                    Order.parent_order_id == parent_order_id,
                )
            )
            .scalars()
            .all()
        )
        for o in existing:
            if (o.payload or {}) == (req.payload or {}):
                return o
        return None

    def list_orders(self, session_id: str, faction: str, omniscient: bool) -> list[OrderResponse]:
        """列出 session 的指令（pending + 歷史），依 faction 過濾（omniscient 見全部）。

        faction 過濾下推到 SQL WHERE（CODE_REVIEW C12）——非全知者不把敵方指令載入行程記憶體。
        """
        stmt = (
            select(Order)
            .join(TacticalUnit, Order.unit_id == TacticalUnit.id)
            .where(Order.session_id == session_id)
            .order_by(Order.issued_at_tick.desc(), Order.id)
        )
        if not omniscient:
            stmt = stmt.where(TacticalUnit.faction == faction)
        orders = self._db.execute(stmt).scalars().all()
        return [_to_response(order, _precheck_of(order)) for order in orders]

    def cancel(
        self, session_id: str, order_id: str, faction: str, omniscient: bool
    ) -> OrderResponse:
        order = self._db.get(Order, order_id)
        if order is None or order.session_id != session_id:
            raise OrderNotFoundError(f"指令不存在：{order_id}")
        # 授權：非全知者只能取消己方陣營單位的指令（CODE_REVIEW C1）。他陣營指令一律以「不存在」
        # 回應，避免洩漏敵方指令存在（fog of war 與 GET /orders 過濾一致）。
        if not omniscient:
            unit = self._db.get(TacticalUnit, order.unit_id)
            if unit is None or unit.faction != faction:
                raise OrderNotFoundError(f"指令不存在：{order_id}")
        if not is_user_cancellable(order.status):
            raise IllegalOrderTransitionError(
                f"指令狀態 {order.status} 不可取消（僅未執行者可取消）",
                details={"status": order.status.value},
            )
        order.status = next_status(order.status, OrderStatus.CANCELLED)
        self._cancel_children(order.id)
        self._db.commit()
        return _to_response(order, _precheck_of(order))

    def _cancel_children(self, parent_id: str) -> int:
        return cancel_child_orders(self._db, parent_id)


def cancel_child_orders(db: Session, parent_id: str) -> int:
    """母任務令結束 → 連帶取消**尚未終結**的子令（WP-A2）。回取消數。

    母令只要離開「進行中」就該收，**不是只有使用者按取消時才收**：

    - 使用者取消（`OrderService.cancel`）
    - **任務自然結束/失敗**（`LiveMissionPlanner` 走到 COMPLETE/FAILED）

    第二條在 A2 收尾時漏了：planner 直接把母令寫成 COMPLETED 就結束，
    於是最後一道 MOVE 子令仍是 EXECUTING——**任務都結束了部隊還在往目標走**，
    失敗的任務更糟（照著失敗的計畫繼續執行）。故抽成模組函式讓兩條路徑共用。

    **已終結的子令不追溯**：那些是既成事實（已經走過的路、已經開過的火），AAR 要看得到。
    CANCELLED 對執行中的移動令語義是「原地凍結」（見 state_machine 說明），
    所以取消 EXECUTING 的子令是對的——不是把單位傳送回起點。

    ⚠ **只收直接子代，不遞迴**。今天分解器只產 MOVE/ENGAGE/POSTURE，樹深恆為 1；
    真要遞迴得先處理 `parentOrderId` 沒有 FK（可能成環）這件事。
    """
    active = (OrderStatus.PENDING, OrderStatus.VALIDATED, OrderStatus.EXECUTING)
    children = (
        db.execute(
            select(Order).where(Order.parent_order_id == parent_id, Order.status.in_(active))
        )
        .scalars()
        .all()
    )
    for child in children:
        child.status = next_status(child.status, OrderStatus.CANCELLED)
    return len(children)


def _precheck_of(order: Order) -> PrecheckResult | None:
    return PrecheckResult.model_validate(order.precheck) if order.precheck else None


def _to_response(order: Order, precheck: PrecheckResult | None) -> OrderResponse:
    payload = order.payload or {}
    tgt = payload.get("target_unit_id")
    to_h3 = payload.get("to_h3")
    return OrderResponse(
        id=order.id,
        unit_id=order.unit_id,
        order_type=order.order_type,
        status=order.status,
        precheck=precheck,
        issued_at_tick=order.issued_at_tick,
        resolved_at_tick=order.resolved_at_tick,
        target_unit_id=tgt if isinstance(tgt, str) else None,
        target_h3=to_h3 if isinstance(to_h3, str) else None,
        issuer_id=order.issuer_id,
        payload=dict(payload),
        parent_order_id=order.parent_order_id,
        mission_type=(
            str(payload["mission_type"])
            if order.order_type == OrderType.MISSION.value and payload.get("mission_type")
            else None
        ),
    )
