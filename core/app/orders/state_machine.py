"""Order 狀態機（O3.1）——所有 OrderStatus 轉移的唯一權威。

合法轉移（SPEC §2.3 生命週期）：

    PENDING   → VALIDATED | REJECTED | CANCELLED
    VALIDATED → EXECUTING | CANCELLED
    EXECUTING → COMPLETED | REJECTED | CANCELLED
    COMPLETED / REJECTED / CANCELLED → （終態，無出邊）

語意：
- PENDING：剛收到，尚未驗證/預檢。
- VALIDATED：通過驗證 + 物理預檢，進入 pending queue 等待 tick 取用。
- EXECUTING：Kernel 於某 tick 取出開始執行（O3.3/O3.4）。
- COMPLETED/REJECTED/CANCELLED：終態。

**使用者取消**（DELETE 端點）允許尚未完成者（PENDING/VALIDATED/EXECUTING）。取消執行中的
MOVE 會讓單位「就地凍結」——移動系統只撿 VALIDATED/EXECUTING 指令，CANCELLED 後不再推進，
單位停在當前位置（不彈回原位，#15）。EXECUTING→CANCELLED 亦供 Kernel 端中斷（如移動被地形
事件打斷，O3.4）。
"""

from __future__ import annotations

from app.errors import IllegalOrderTransitionError
from app.models.enums import OrderStatus

_TRANSITIONS: dict[OrderStatus, frozenset[OrderStatus]] = {
    OrderStatus.PENDING: frozenset(
        {OrderStatus.VALIDATED, OrderStatus.REJECTED, OrderStatus.CANCELLED}
    ),
    OrderStatus.VALIDATED: frozenset({OrderStatus.EXECUTING, OrderStatus.CANCELLED}),
    OrderStatus.EXECUTING: frozenset(
        {OrderStatus.COMPLETED, OrderStatus.REJECTED, OrderStatus.CANCELLED}
    ),
    OrderStatus.COMPLETED: frozenset(),
    OrderStatus.REJECTED: frozenset(),
    OrderStatus.CANCELLED: frozenset(),
}

TERMINAL_STATUSES: frozenset[OrderStatus] = frozenset(
    status for status, nexts in _TRANSITIONS.items() if not nexts
)

# 使用者可主動取消的狀態（尚未完成）——含 EXECUTING：取消執行中的移動＝就地凍結（#15）。
_USER_CANCELLABLE: frozenset[OrderStatus] = frozenset(
    {OrderStatus.PENDING, OrderStatus.VALIDATED, OrderStatus.EXECUTING}
)


def can_transition(current: OrderStatus, target: OrderStatus) -> bool:
    return target in _TRANSITIONS[current]


def next_status(current: OrderStatus, target: OrderStatus) -> OrderStatus:
    """驗證並回傳目標狀態；非法轉移拋 IllegalOrderTransitionError。"""
    if not can_transition(current, target):
        raise IllegalOrderTransitionError(
            f"非法 Order 轉移：{current} → {target}",
            details={"from": current.value, "to": target.value},
        )
    return target


def is_user_cancellable(current: OrderStatus) -> bool:
    """使用者（DELETE 端點）是否可取消此狀態的指令（僅限尚未執行）。"""
    return current in _USER_CANCELLABLE
