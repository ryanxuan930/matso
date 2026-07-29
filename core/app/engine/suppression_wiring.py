"""壓制與姿態在活執行期的接線（WP-C1）。

`adjudication/suppression.py` 是純函數；本模組只做 I/O 邊界：讀寫熱狀態。

三件事，各自在 tick 的不同位置：

1. **累積**——裁決命中後由 `EngagementAdjudicator` 呼叫（射手的武器類別決定累積量）。
2. **衰減 + 姿態收斂**——每 tick 一次，跑在 `pre_tick`（與火力排程同一個位置）。
3. **移動打斷姿態**——單位實際移動時把姿態打回 MOVING。

**熱狀態鍵刻意都有中性預設**：`suppression` 缺鍵讀作 0、`posture` 缺鍵讀作 MOVING，
於是既有局（那些鍵都不存在）的修正剛好都是 1.0——位元不變。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.adjudication.suppression import (
    Posture,
    PostureState,
    add_suppression,
    decay_suppression,
)
from app.state.hot_state import HotStateStore

SUPPRESSION_KEY = "suppression"
POSTURE_KEY = "posture"
POSTURE_TARGET_KEY = "posture_target"
POSTURE_SINCE_KEY = "posture_since_tick"


def read_posture(state: dict[str, Any]) -> PostureState:
    """熱狀態 → 姿態狀態機。缺鍵 → MOVING（既有局零行為變更）。"""

    def _p(key: str) -> Posture:
        raw = state.get(key)
        try:
            return Posture(str(raw)) if raw else Posture.MOVING
        except ValueError:
            return Posture.MOVING

    since = state.get(POSTURE_SINCE_KEY)
    return PostureState(
        current=_p(POSTURE_KEY),
        target=_p(POSTURE_TARGET_KEY),
        since_tick=int(since) if isinstance(since, (int, float)) else 0,
    )


def _write_posture(hot: HotStateStore, unit_id: str, st: PostureState) -> None:
    hot.update_unit(
        unit_id,
        {
            POSTURE_KEY: st.current.value,
            POSTURE_TARGET_KEY: st.target.value,
            POSTURE_SINCE_KEY: st.since_tick,
        },
    )


def apply_hit_suppression(
    hot: HotStateStore, unit_id: str, weapon_category: str, rounds: int = 1
) -> float:
    """被命中 → 累積壓制。回新值。

    **由裁決層在命中後呼叫**，而不是每 tick 掃描——壓制的來源是具體的一次命中，
    掃描式的模型會分不清「被打了三次」與「被打了一次但很久」。
    """
    state = hot.get_unit(unit_id) or {}
    raw = state.get(SUPPRESSION_KEY, 0.0)
    current = float(raw) if isinstance(raw, (int, float)) else 0.0
    value = add_suppression(current, weapon_category, rounds)
    hot.update_unit(unit_id, {SUPPRESSION_KEY: round(value, 3)})
    return value


def apply_area_suppression(
    hot: HotStateStore, rounds_by_unit: Mapping[str, int], weapon_category: str
) -> int:
    """面射擊 → 壓制半徑內每個單位都被壓制。回受影響的單位數。

    `rounds_by_unit` ＝ `AreaFireResult.suppressed`：unit_id → 落進**它的**壓制半徑的發數。
    逐單位帶發數而不是全體一律 `fired`——齊放外緣的單位不該與正中心的同等壓制。

    **不是只壓制有戰損的單位**：砲彈在你旁邊炸開卻沒傷到你，你照樣得趴下，
    那正是壓制射擊的定義（也是砲兵最主要的用途）。

    也**不分敵我**：友軍在落點附近一樣抬不起頭。誤傷語意屬 WP-C9，物理在這裡就要對。
    """
    touched = 0
    for unit_id, rounds in rounds_by_unit.items():
        if rounds <= 0:
            continue
        apply_hit_suppression(hot, unit_id, weapon_category, rounds)
        touched += 1
    return touched


def tick_suppression(hot: HotStateStore, tick: int) -> int:
    """每 tick 的衰減與姿態收斂。回實際更新的單位數。

    **只寫真的變了的單位**：熱狀態的每一次 `update_unit` 都會進 STATE_DIFF 推給 client，
    每 tick 對每個單位寫一次「壓制還是 0」是純粹的雜訊。
    """
    touched = 0
    for unit_id, state in hot.get_all().items():
        patch: dict[str, Any] = {}
        raw = state.get(SUPPRESSION_KEY)
        if isinstance(raw, (int, float)) and raw > 0:
            decayed = decay_suppression(float(raw))
            if decayed != raw:
                patch[SUPPRESSION_KEY] = round(decayed, 3)
        posture = read_posture(state)
        advanced = posture.advance(tick)
        if advanced != posture:
            patch[POSTURE_KEY] = advanced.current.value
            patch[POSTURE_SINCE_KEY] = advanced.since_tick
        if patch:
            hot.update_unit(unit_id, patch)
            touched += 1
    return touched


def set_posture(hot: HotStateStore, unit_id: str, target: Posture, tick: int) -> PostureState:
    """POSTURE 令：宣告要進入的姿態。**轉換要時間**，這裡只是登記目標。"""
    st = read_posture(hot.get_unit(unit_id) or {}).order(target, tick)
    _write_posture(hot, unit_id, st)
    return st


def interrupt_posture(hot: HotStateStore, unit_id: str, tick: int) -> None:
    """單位移動了 → 姿態打回 MOVING。挖到一半的洞帶不走。

    已經是 MOVING 的單位**不寫**——否則每個移動中的單位每 tick 都推一次無意義的 diff。
    """
    st = read_posture(hot.get_unit(unit_id) or {})
    nxt = st.interrupted(tick)
    if nxt != st:
        _write_posture(hot, unit_id, nxt)


__all__ = [
    "POSTURE_KEY",
    "POSTURE_SINCE_KEY",
    "POSTURE_TARGET_KEY",
    "SUPPRESSION_KEY",
    "apply_area_suppression",
    "apply_hit_suppression",
    "drain_posture_orders",
    "interrupt_posture",
    "read_posture",
    "set_posture",
    "tick_suppression",
]


# ---- POSTURE 令的執行（WP-C1）----


def drain_posture_orders(db: Any, session_id: str, hot: HotStateStore, tick: int) -> int:
    """把 VALIDATED 的 POSTURE 令套進熱狀態並轉 COMPLETED。回處理數。

    **姿態令沒有裁決階段**：它不產生戰損、不抽隨機、不需要物理判定，
    只是「這個單位打算進入什麼狀態」。所以走 pre_tick 而不是 Kernel 的裁決槽——
    塞進裁決槽只會讓那條路徑多一個與交戰無關的分支。

    轉換要時間由 `PostureState` 管；這裡只登記目標與起算 tick。
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
            Order.order_type == "POSTURE",
        )
        .order_by(Order.issued_at_tick, Order.id)
    ).all()
    applied = 0
    for order in orders:
        order.status = next_status(order.status, OrderStatus.EXECUTING)
        raw = (order.payload or {}).get("posture")
        try:
            target = Posture(str(raw))
        except ValueError:
            # 令面姿態無效 → 判 REJECTED 而不是靜靜完成。
            order.status = next_status(order.status, OrderStatus.REJECTED)
            continue
        set_posture(hot, order.unit_id, target, tick)
        order.status = next_status(order.status, OrderStatus.COMPLETED)
        order.resolved_at_tick = tick
        applied += 1
    db.commit()
    return applied
