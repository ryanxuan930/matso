"""CircuitBreaker 單元測試（O2.5）——注入假時鐘，確定性驗證狀態機。"""

from __future__ import annotations

from app.plugins.terrain_client import BreakerState, CircuitBreaker


class _Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def test_closed_until_threshold() -> None:
    b = CircuitBreaker(failure_threshold=3)
    for _ in range(2):
        b.record_failure()
        assert b.state is BreakerState.CLOSED
        assert b.allow()
    b.record_failure()  # 第 3 次 → OPEN
    assert b.state is BreakerState.OPEN
    assert not b.allow()  # 快速失敗


def test_success_resets_failures() -> None:
    b = CircuitBreaker(failure_threshold=3)
    b.record_failure()
    b.record_failure()
    b.record_success()
    b.record_failure()
    b.record_failure()
    assert b.state is BreakerState.CLOSED  # 累計被重置，未達門檻


def test_cooldown_transitions_to_half_open() -> None:
    clock = _Clock()
    b = CircuitBreaker(failure_threshold=1, cooldown_s=5.0, now=clock)
    b.record_failure()  # → OPEN
    assert not b.allow()
    clock.t = 4.9
    assert not b.allow()  # 冷卻未滿
    clock.t = 5.0
    assert b.allow()  # 冷卻滿 → HALF_OPEN 放行一次
    assert b.state is BreakerState.HALF_OPEN


def test_half_open_failure_reopens() -> None:
    clock = _Clock()
    b = CircuitBreaker(failure_threshold=1, cooldown_s=5.0, now=clock)
    b.record_failure()
    clock.t = 5.0
    assert b.allow()  # HALF_OPEN
    b.record_failure()  # 試探失敗 → 立即 OPEN
    assert b.state is BreakerState.OPEN
    assert not b.allow()


def test_half_open_success_closes() -> None:
    clock = _Clock()
    b = CircuitBreaker(failure_threshold=1, cooldown_s=5.0, now=clock)
    b.record_failure()
    clock.t = 5.0
    assert b.allow()  # HALF_OPEN
    b.record_success()  # 試探成功 → CLOSED
    assert b.state is BreakerState.CLOSED
    assert b.allow()
