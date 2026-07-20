"""DB-backed OrderStore（O3.4）——VALIDATED MOVE 指令的拉取與狀態轉移（走狀態機）。

from_h3 由單位當前座標推導（權威來自 DB，非信任 client）。狀態轉移一律經 orders 狀態機。
"""

from __future__ import annotations

import h3
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.enums import OrderStatus
from app.models.tables import Order, TacticalUnit
from app.movement.system import MoveCommand
from app.orders.schemas import OrderType
from app.orders.state_machine import next_status

_HEX_RES = 8  # 與 terrain hex grid / precheck 一致


class DbOrderStore:
    """以 SQLAlchemy 實作 movement.OrderStore。呼叫方（Kernel 組裝）提供 session。"""

    def __init__(self, db: Session) -> None:
        self._db = db

    def pending_moves(self, session_id: str) -> list[MoveCommand]:
        orders = self._db.scalars(
            select(Order)
            .where(
                Order.session_id == session_id,
                Order.status == OrderStatus.VALIDATED,
                Order.order_type == OrderType.MOVE.value,
            )
            .order_by(Order.issued_at_tick, Order.id)  # 確定性順序
        ).all()
        commands: list[MoveCommand] = []
        for order in orders:
            unit = self._db.get(TacticalUnit, order.unit_id)
            if unit is None or unit.current_lat is None or unit.current_lng is None:
                continue  # 單位不存在/無座標 → 跳過（不會產生 mission）
            from_h3 = h3.latlng_to_cell(unit.current_lat, unit.current_lng, _HEX_RES)
            payload = order.payload
            commands.append(
                MoveCommand(
                    order_id=order.id,
                    unit_id=order.unit_id,
                    from_h3=from_h3,
                    to_h3=str(payload["to_h3"]),
                    mobility_profile=str(payload["mobility_profile"]),
                )
            )
        return commands

    def mark_executing(self, order_id: str) -> None:
        self._transition(order_id, OrderStatus.EXECUTING)

    def mark_completed(self, order_id: str, tick: int) -> None:
        order = self._db.get(Order, order_id)
        if order is None:
            return
        order.status = next_status(order.status, OrderStatus.COMPLETED)
        order.resolved_at_tick = tick
        self._db.commit()

    def _transition(self, order_id: str, target: OrderStatus) -> None:
        order = self._db.get(Order, order_id)
        if order is None:
            return
        order.status = next_status(order.status, target)
        self._db.commit()
