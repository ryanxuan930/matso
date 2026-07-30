"""乘駐車與隊形在活執行期的接線（WP-C3）。

`adjudication/formation.py` 是純函數；本模組只做 I/O 邊界：讀寫熱狀態、執行 FORMATION 令。

## 為什麼是一個令型而不是三個

規格寫「MOUNT/DISMOUNT 令」，實作收成**一個 `FORMATION` 令**，payload 可帶
`formation` 與/或 `mounted`。理由：三個令型會讓 `SEAT_ORDER_TYPES`、`_PAYLOAD_MODELS`、
`run_precheck` 的分派、前端的令型下拉各自多兩個分支，而它們表達的是同一件事——
**宣告本單位要以什麼狀態行動**（與 POSTURE 同類）。

一次可以同時改兩者（「下車並展開成橫隊」是一個動作，不是兩個）。

## 與姿態刻意分開

姿態（WP-C1）是「有沒有挖掩體」，隊形/乘駐車是「怎麼擺開、在不在車上」。
兩者可以同時成立（掘壕的橫隊），混成一個欄位就分不清。
"""

from __future__ import annotations

from typing import Any

from app.adjudication.formation import Formation, formation_of
from app.state.hot_state import HotStateStore

MOUNTED_KEY = "mounted"
FORMATION_KEY = "formation"


def read_formation(state: dict[str, Any]) -> tuple[Formation, bool]:
    """熱狀態 → (隊形, 是否乘車)。缺鍵 → (COLUMN, False)，即中性預設。"""
    return formation_of(state.get(FORMATION_KEY)), bool(state.get(MOUNTED_KEY))


def set_formation(
    hot: HotStateStore,
    unit_id: str,
    *,
    formation: Formation | None = None,
    mounted: bool | None = None,
) -> None:
    """套用宣告。**None 代表不動該欄**——只想下車的令不該把隊形一起重設。"""
    patch: dict[str, Any] = {}
    if formation is not None:
        patch[FORMATION_KEY] = formation.value
    if mounted is not None:
        patch[MOUNTED_KEY] = mounted
    if patch:
        hot.update_unit(unit_id, patch)


def drain_formation_orders(db: Any, session_id: str, hot: HotStateStore, tick: int) -> int:
    """把 VALIDATED 的 FORMATION 令套進熱狀態並轉 COMPLETED。回處理數。

    與 POSTURE 令同一個形狀：**沒有裁決階段**（不產生戰損、不抽隨機、不需要物理判定），
    所以走 pre_tick 而不是 Kernel 的裁決槽。
    """
    from sqlalchemy import select

    from app.models.enums import OrderStatus
    from app.models.tables import Order
    from app.orders.state_machine import next_status

    orders = db.scalars(
        select(Order)
        .where(
            Order.session_id == session_id,
            Order.status == OrderStatus.VALIDATED,
            Order.order_type == "FORMATION",
        )
        .order_by(Order.issued_at_tick, Order.id)
    ).all()
    applied = 0
    for order in orders:
        order.status = next_status(order.status, OrderStatus.EXECUTING)
        payload = order.payload or {}
        raw_formation = payload.get("formation")
        raw_mounted = payload.get("mounted")
        formation: Formation | None = None
        if raw_formation:
            try:
                formation = Formation(str(raw_formation))
            except ValueError:
                # 令面隊形無效 → 判 REJECTED 而不是靜靜完成。
                order.status = next_status(order.status, OrderStatus.REJECTED)
                continue
        mounted = bool(raw_mounted) if raw_mounted is not None else None
        if formation is None and mounted is None:
            order.status = next_status(order.status, OrderStatus.REJECTED)
            continue
        set_formation(hot, order.unit_id, formation=formation, mounted=mounted)
        order.status = next_status(order.status, OrderStatus.COMPLETED)
        order.resolved_at_tick = tick
        applied += 1
    db.commit()
    return applied


__all__ = [
    "FORMATION_KEY",
    "MOUNTED_KEY",
    "drain_formation_orders",
    "read_formation",
    "set_formation",
]
