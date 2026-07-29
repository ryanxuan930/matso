"""席位權限（WP-B5.1，[JCATS-F p.9–10]）——同一陣營內的參謀分工。

**這裡是席位 → 可下令型別的唯一權威表。** 要調整分工只改 `SEAT_ORDER_TYPES` 這一張表，
不必動驗證器、端點或前端；B5.2（申請-核覆）與 C10（臨機火力鏈）接手時也只擴充這裡。

**未指派席位（None）＝完全沿用 UserRole 的既有規則**（使用者裁示 2026-07-30）。
理由：上線時所有既有參與者的 seat_role 都是 NULL，若把「無席位」當成「不能下令」，
跑到一半的演習會立刻鎖死。這條有測試釘住，別為了語意漂亮把它改掉。
"""

from __future__ import annotations

from app.models.enums import SeatRole
from app.orders.schemas import OrderType

# 席位 → 該席位可下的令型別。
# 對應依參謀職掌訂定（使用者確認 2026-07-30）：作戰官管兵力運用、火力支援協調官管火力，
# 情報官與觀察員不下戰術令。S4_LOG 的補給令型尚未存在（WP-C7），故先給空集合——
# **空集合＝不能下任何令**，與「未指派席位」是兩件不同的事，不要合併。
SEAT_ORDER_TYPES: dict[SeatRole, frozenset[OrderType]] = {
    SeatRole.COMMANDER: frozenset(OrderType),  # 指揮官：全部
    SeatRole.S3_OPS: frozenset({OrderType.MOVE}),  # 作戰官：機動
    # 火力支援協調官：火力（含面目標射擊——WP-C10.2 新增令型時只改這一張表）
    SeatRole.FSO_FIRES: frozenset({OrderType.ENGAGE, OrderType.FIRE_MISSION}),
    SeatRole.S2_INTEL: frozenset(),  # 情報官：唯讀
    SeatRole.S4_LOG: frozenset(),  # 後勤官：待補給令型（WP-C7）
    SeatRole.OBSERVER: frozenset(),  # 觀察員：唯讀
}

SEAT_LABELS: dict[SeatRole, str] = {
    SeatRole.COMMANDER: "指揮官",
    SeatRole.S2_INTEL: "情報官（S2）",
    SeatRole.S3_OPS: "作戰官（S3）",
    SeatRole.FSO_FIRES: "火力支援協調官（FSO）",
    SeatRole.S4_LOG: "後勤官（S4）",
    SeatRole.OBSERVER: "觀察員",
}


def seat_may_order(seat: SeatRole | None, order_type: OrderType) -> bool:
    """該席位是否可下此型別的令。

    `seat is None`（未指派席位）一律回 True——權限交還給既有的角色/陣營規則判斷，
    本函數不在那條路徑上加碼。
    """
    if seat is None:
        return True
    return order_type in SEAT_ORDER_TYPES.get(seat, frozenset())


__all__ = ["SEAT_LABELS", "SEAT_ORDER_TYPES", "seat_may_order"]
