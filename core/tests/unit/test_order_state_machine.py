"""Order 狀態機測試（O3.1）——合法轉移、非法防護、終態、使用者取消規則。"""

from __future__ import annotations

import itertools

import pytest

from app.errors import IllegalOrderTransitionError
from app.models.enums import OrderStatus
from app.orders.state_machine import (
    TERMINAL_STATUSES,
    can_transition,
    is_user_cancellable,
    next_status,
)

_LEGAL = {
    (OrderStatus.PENDING, OrderStatus.VALIDATED),
    (OrderStatus.PENDING, OrderStatus.REJECTED),
    (OrderStatus.PENDING, OrderStatus.CANCELLED),
    (OrderStatus.VALIDATED, OrderStatus.EXECUTING),
    (OrderStatus.VALIDATED, OrderStatus.CANCELLED),
    (OrderStatus.EXECUTING, OrderStatus.COMPLETED),
    (OrderStatus.EXECUTING, OrderStatus.REJECTED),
    (OrderStatus.EXECUTING, OrderStatus.CANCELLED),
}


def test_legal_transitions_allowed() -> None:
    for src, dst in _LEGAL:
        assert can_transition(src, dst)
        assert next_status(src, dst) is dst


def test_all_other_transitions_illegal() -> None:
    # property：合法集合以外的任一 (src, dst) 都必須被拒
    for src, dst in itertools.product(OrderStatus, repeat=2):
        if (src, dst) in _LEGAL:
            continue
        assert not can_transition(src, dst)
        with pytest.raises(IllegalOrderTransitionError):
            next_status(src, dst)


def test_terminal_states_have_no_exits() -> None:
    assert {
        OrderStatus.COMPLETED,
        OrderStatus.REJECTED,
        OrderStatus.CANCELLED,
    } == TERMINAL_STATUSES
    for terminal in TERMINAL_STATUSES:
        for dst in OrderStatus:
            assert not can_transition(terminal, dst)


def test_user_cancellable_until_terminal() -> None:
    # 未完成者皆可取消——含 EXECUTING（取消執行中移動＝就地凍結，不彈回原位，#15）。
    assert is_user_cancellable(OrderStatus.PENDING)
    assert is_user_cancellable(OrderStatus.VALIDATED)
    assert is_user_cancellable(OrderStatus.EXECUTING)
    for status in (
        OrderStatus.COMPLETED,
        OrderStatus.REJECTED,
        OrderStatus.CANCELLED,
    ):
        assert not is_user_cancellable(status)


def test_illegal_transition_carries_details() -> None:
    with pytest.raises(IllegalOrderTransitionError) as ei:
        next_status(OrderStatus.COMPLETED, OrderStatus.CANCELLED)
    assert ei.value.details == {"from": "COMPLETED", "to": "CANCELLED"}
    assert ei.value.http_status == 409
