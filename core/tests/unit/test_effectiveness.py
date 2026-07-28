"""戰力→效能映射（真實化交戰 Phase 1）——effectiveness.py 純函數。"""

from __future__ import annotations

import pytest

from app.adjudication.effectiveness import (
    effectiveness_pct,
    health_state,
    interp_effectiveness,
)


def test_endpoints_and_full_strength() -> None:
    assert interp_effectiveness(1.0) == pytest.approx(1.0)
    assert interp_effectiveness(0.0) == pytest.approx(0.0)  # 0.30 以下夾到 0
    assert interp_effectiveness(0.30) == pytest.approx(0.0)
    assert effectiveness_pct(1.0) == pytest.approx(100.0)


def test_concave_monotonic_nondecreasing() -> None:
    prev = -1.0
    for i in range(0, 101):
        e = interp_effectiveness(i / 100.0)
        assert 0.0 <= e <= 1.0
        assert e >= prev - 1e-9  # 遞增（非遞減）
        prev = e


def test_early_losses_hurt_less_than_late() -> None:
    # 凹形：滿編附近掉一點效能影響小；逼近折點掉一點影響大。
    near_full = interp_effectiveness(1.0) - interp_effectiveness(0.90)
    near_break = interp_effectiveness(0.50) - interp_effectiveness(0.40)
    assert near_break > near_full


def test_health_state_thresholds() -> None:
    assert health_state(1.0) == "OK"
    assert health_state(0.90) == "OK"
    assert health_state(0.60) == "DEGRADED"
    assert health_state(0.31) == "DEGRADED"
    assert health_state(0.29) == "DOWN"
    assert health_state(0.0) == "DOWN"
