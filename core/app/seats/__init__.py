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
# 宣告了但**沒有任何執行端**的令型。誰都不該下得了——送出去只會永遠停在 VALIDATED，
# 而且沒有任何錯誤訊息（`_PAYLOAD_MODELS` 也沒登錄它，連 payload 都不驗）。
# 這不是權限問題，是「這個功能還沒做」。做好了就把它從這裡拿掉並指派席位。
UNIMPLEMENTED_ORDER_TYPES: frozenset[OrderType] = frozenset({OrderType.RECON})

SEAT_ORDER_TYPES: dict[SeatRole, frozenset[OrderType]] = {
    # 指揮官：除了還沒實作的以外全部。**不是 `frozenset(OrderType)`**——
    # 讓指揮官下得了一道永遠不會執行的令，比擋掉它糟。
    SeatRole.COMMANDER: frozenset(OrderType) - UNIMPLEMENTED_ORDER_TYPES,
    # 作戰官：兵力運用的全部——機動、任務級下令、姿態、乘駐車/隊形、障礙作業、偵蒐派遣。
    # **這張表過去只有 MOVE**，而 MISSION/POSTURE/FORMATION/ENGINEER 是後來幾張卡新增的
    # 令型：新增時只改了 OrderType 與預檢，沒回來改這裡，於是作戰官連任務令都下不了
    # （前端顯示得出下拉選項，送出去被 ORDER_SEAT_DENIED 擋掉）。
    SeatRole.S3_OPS: frozenset(
        {
            OrderType.MOVE,
            OrderType.MISSION,
            OrderType.POSTURE,
            OrderType.FORMATION,
            OrderType.ENGINEER,
        }
    ),
    # 火力支援協調官：火力（含面目標射擊——WP-C10.2 新增令型時只改這一張表）
    SeatRole.FSO_FIRES: frozenset({OrderType.ENGAGE, OrderType.FIRE_MISSION}),
    SeatRole.S2_INTEL: frozenset(),  # 情報官：唯讀（使用者裁示 2026-07-30）
    # 後勤官：補給撥交。舊註解寫「待補給令型（WP-C7）」——**那張卡早就完成了**，
    # `RESUPPLY` 與 `ResupplySystem` 都在，只有這張表沒跟上，後勤官一直是不能下令的。
    SeatRole.S4_LOG: frozenset({OrderType.RESUPPLY}),
    SeatRole.OBSERVER: frozenset(),  # 觀察員：唯讀
}

# 只有指揮官能下的令型。**目前是空的**——留這個集合是為了讓底下那條漂移守門有話可說：
# 新增 OrderType 時必須明確決定它歸誰，不能靜默落成「只有指揮官下得了而沒人發現」。
COMMANDER_ONLY_ORDER_TYPES: frozenset[OrderType] = frozenset()

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


__all__ = [
    "COMMANDER_ONLY_ORDER_TYPES",
    "SEAT_LABELS",
    "SEAT_ORDER_TYPES",
    "UNIMPLEMENTED_ORDER_TYPES",
    "seat_may_order",
]
