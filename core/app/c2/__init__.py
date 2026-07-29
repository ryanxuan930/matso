"""C2 信文與申請-核覆（WP-B5.2，[JCATS-A p.13,15,26]／[JCATS-F p.10–14]）。

「指參程序的磨練」靠的是異步審批鏈，不是即時生效的按鈕：下級席位送出申請 →
上級/白軍席位核覆 → 核准後才轉為效果，全程留痕供 AAR 重建事件鏈。

**純函數區**：本模組的 registry 與判定皆為純函數，DB 操作在 `service.py`。
"""

from __future__ import annotations

from app.models.enums import RequestKind, SeatRole, UserRole

# 誰能核覆——與 `app.seats.SEAT_ORDER_TYPES` 並列的第二張表。
# 規格：「下級席位送出 → 上級/白軍席位核覆」。指揮官是該陣營的上級；白軍/導演另循角色旁通。
SEAT_APPROVAL: dict[SeatRole, frozenset[RequestKind]] = {
    SeatRole.COMMANDER: frozenset(RequestKind),
    SeatRole.S2_INTEL: frozenset(),
    SeatRole.S3_OPS: frozenset(),
    SeatRole.FSO_FIRES: frozenset(),
    SeatRole.S4_LOG: frozenset(),
    SeatRole.OBSERVER: frozenset(),
}

# 白軍/導演可核覆任何申請（與下令的 `_OVERRIDE_ROLES` 同一組角色）。
_OVERRIDE_ROLES = frozenset({UserRole.WHITE_CELL_STAFF, UserRole.EXERCISE_DIRECTOR})


def may_approve(role: UserRole, seat: SeatRole | None, kind: RequestKind) -> bool:
    """此人是否可核覆這一類申請。

    **未指派席位（None）沿用角色規則**——與 B5.1 同一條原則：既有局的參與者
    seat_role 都是 NULL，若在這裡要求「必須有席位才能核覆」，等於沒有人能核覆。
    故 None 時退回看角色：指揮官角色可核覆，其餘不可。
    """
    if role in _OVERRIDE_ROLES:
        return True
    if seat is None:
        return role is UserRole.COMMANDER
    return kind in SEAT_APPROVAL.get(seat, frozenset())


REQUEST_LABELS: dict[RequestKind, str] = {
    RequestKind.AIR_RECON: "空中偵察",
    RequestKind.FIRE_SUPPORT: "火力支援",
    RequestKind.RESUPPLY_VOUCHER: "補給憑單",
}

__all__ = ["REQUEST_LABELS", "SEAT_APPROVAL", "may_approve"]
