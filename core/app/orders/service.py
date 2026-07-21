"""Order pipeline 編排（O3.1，SPEC §2.3 步驟 [1]–[2]）。

submit：validate → 物理預檢 → 持久化（PENDING→VALIDATED 或 →REJECTED，經狀態機）。
cancel：使用者取消未執行指令（→CANCELLED）。

issued_at_tick 由注入的 tick_source 提供——kernel↔API 整合（後續卡）時改讀活的 SimClock；
O3.1 預設回 0（尚無運行中的 kernel 綁定）。
"""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import IllegalOrderTransitionError, OrderNotFoundError, PrecheckFailedError
from app.factions import FactionRelations
from app.models.enums import OrderStatus
from app.models.tables import Order, TacticalUnit
from app.orders.precheck import PhysicsGateway, precheck_error_code, run_precheck
from app.orders.schemas import OrderRequest, OrderResponse, PrecheckResult
from app.orders.state_machine import is_user_cancellable, next_status
from app.orders.validator import validate_order


class OrderService:
    def __init__(
        self,
        db: Session,
        gateway: PhysicsGateway,
        tick_source: Callable[[], int] = lambda: 0,
        relations: FactionRelations | None = None,
    ) -> None:
        self._db = db
        self._gateway = gateway
        self._tick_source = tick_source
        self._relations = relations  # None → 全 HOSTILE（O7 scenario 載入實際矩陣）

    def submit(self, session_id: str, req: OrderRequest, issuer_id: str) -> OrderResponse:
        """驗證 + 預檢 + 落庫。不可行 → 持久化 REJECTED 後拋 PrecheckFailedError（API 轉 422）。"""
        validated = validate_order(self._db, session_id, req, issuer_id)
        precheck = run_precheck(self._db, validated, self._gateway, self._relations)

        order = Order(
            session_id=session_id,
            issuer_id=issuer_id,
            unit_id=req.unit_id,
            order_type=req.order_type.value,
            payload=req.payload,
            status=OrderStatus.PENDING,
            precheck=precheck.model_dump(),
            issued_at_tick=self._tick_source(),
        )
        target = OrderStatus.VALIDATED if precheck.feasible else OrderStatus.REJECTED
        order.status = next_status(order.status, target)  # PENDING → VALIDATED / REJECTED
        self._db.add(order)
        self._db.commit()

        if not precheck.feasible:
            raise PrecheckFailedError(
                precheck.reason or "物理預檢不可行",
                error_code=precheck_error_code(precheck),
                details={"order_id": order.id, "precheck": precheck.model_dump()},
            )
        return _to_response(order, precheck)

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
        self._db.commit()
        return _to_response(order, _precheck_of(order))


def _precheck_of(order: Order) -> PrecheckResult | None:
    return PrecheckResult.model_validate(order.precheck) if order.precheck else None


def _to_response(order: Order, precheck: PrecheckResult | None) -> OrderResponse:
    return OrderResponse(
        id=order.id,
        unit_id=order.unit_id,
        order_type=order.order_type,
        status=order.status,
        precheck=precheck,
        issued_at_tick=order.issued_at_tick,
    )
