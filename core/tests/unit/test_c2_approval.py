"""C2 核覆權（WP-B5.2）——純函數，不碰 DB。"""

from __future__ import annotations

import pytest

from app.c2 import may_approve
from app.models.enums import RequestKind, SeatRole, UserRole


@pytest.mark.parametrize("kind", list(RequestKind))
def test_commander_seat_may_approve_everything(kind: RequestKind) -> None:
    assert may_approve(UserRole.STAFF, SeatRole.COMMANDER, kind)


@pytest.mark.parametrize(
    "seat", [SeatRole.S2_INTEL, SeatRole.S3_OPS, SeatRole.FSO_FIRES, SeatRole.S4_LOG]
)
def test_staff_seats_may_not_approve(seat: SeatRole) -> None:
    """下級席位送出、上級核覆——參謀席不能自己核准自己的申請。"""
    assert not may_approve(UserRole.STAFF, seat, RequestKind.FIRE_SUPPORT)


def test_observer_seat_may_not_approve() -> None:
    assert not may_approve(UserRole.OBSERVER, SeatRole.OBSERVER, RequestKind.AIR_RECON)


@pytest.mark.parametrize("role", [UserRole.WHITE_CELL_STAFF, UserRole.EXERCISE_DIRECTOR])
def test_white_cell_may_always_approve(role: UserRole) -> None:
    """白軍/導演循角色旁通，與席位無關（含未指派席位）。"""
    assert may_approve(role, None, RequestKind.FIRE_SUPPORT)
    assert may_approve(role, SeatRole.S2_INTEL, RequestKind.FIRE_SUPPORT)


def test_no_seat_falls_back_to_role_not_to_deny_all() -> None:
    """**未指派席位沿用角色規則**（與 B5.1 同一條原則）。

    既有局的參與者 seat_role 全是 NULL；若這裡要求「必須有席位才能核覆」，
    等於整個審批鏈沒有人能動——那不是嚴謹，是把功能鎖死。
    """
    assert may_approve(UserRole.COMMANDER, None, RequestKind.FIRE_SUPPORT)
    assert not may_approve(UserRole.STAFF, None, RequestKind.FIRE_SUPPORT)
    assert not may_approve(UserRole.OBSERVER, None, RequestKind.FIRE_SUPPORT)
