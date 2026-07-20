"""Order pipeline（O3.1, SPEC §2.3 八步生命週期步驟 [1]–[3]）。

下令 → [1] validator → [2] 物理預檢（同步<50ms，呼叫 terrain client）→ VALIDATED（入 pending
queue）或 REJECTED。狀態機（app.orders.state_machine）為所有狀態轉移的唯一權威。
"""

from app.orders.schemas import (
    OrderRequest,
    OrderResponse,
    OrderType,
    PrecheckCheck,
    PrecheckResult,
)
from app.orders.service import OrderService
from app.orders.state_machine import (
    TERMINAL_STATUSES,
    is_user_cancellable,
    next_status,
)

__all__ = [
    "TERMINAL_STATUSES",
    "OrderRequest",
    "OrderResponse",
    "OrderService",
    "OrderType",
    "PrecheckCheck",
    "PrecheckResult",
    "is_user_cancellable",
    "next_status",
]
