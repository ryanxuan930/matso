"""鏈路預算純函數（O5.4，SPEC §6.1）。"""

from __future__ import annotations

import math

import pytest
from comms.link_budget import (
    DEGRADED_MARGIN_DB,
    ONLINE_MARGIN_DB,
    LinkState,
    Radio,
    free_space_path_loss_db,
    haversine_m,
    link_margin_db,
    link_state_from_margin,
)

_R = Radio(tx_power_dbm=30.0, antenna_gain_db=3.0, rx_sensitivity_dbm=-100.0, freq_mhz=150.0)


def test_haversine_known_distance() -> None:
    # 約 1 緯度 ≈ 111km
    d = haversine_m(24.0, 121.0, 25.0, 121.0)
    assert 110_000 < d < 112_000


def test_haversine_zero() -> None:
    assert haversine_m(24.0, 121.0, 24.0, 121.0) == 0.0


def test_fspl_increases_with_distance_and_frequency() -> None:
    assert free_space_path_loss_db(10_000, 150) > free_space_path_loss_db(1_000, 150)
    assert free_space_path_loss_db(1_000, 1500) > free_space_path_loss_db(1_000, 150)


def test_fspl_doubling_distance_adds_6db() -> None:
    near = free_space_path_loss_db(1_000, 150)
    far = free_space_path_loss_db(2_000, 150)
    assert far - near == pytest.approx(6.02, abs=0.01)  # 20log10(2)


def test_fspl_floor_avoids_neg_inf() -> None:
    # 同址（0m）以 1m 地板 → 有限值
    assert math.isfinite(free_space_path_loss_db(0.0, 150))


def test_margin_decreases_with_obstruction_weather_jamming() -> None:
    base = link_margin_db(_R, _R, 1_000)
    assert link_margin_db(_R, _R, 1_000, obstruction_db=20) == pytest.approx(base - 20)
    assert link_margin_db(_R, _R, 1_000, weather_attenuation_db=5) == pytest.approx(base - 5)
    assert link_margin_db(_R, _R, 1_000, jamming_db=10) == pytest.approx(base - 10)


def test_margin_formula_matches_spec() -> None:
    # margin = tx + gain_a + gain_b − FSPL − obstruction − weather − jamming − rx_sens
    d = 5_000.0
    expected = 30.0 + 3.0 + 3.0 - free_space_path_loss_db(d, 150.0) - 12.0 - 4.0 - 2.0 - (-100.0)
    got = link_margin_db(_R, _R, d, obstruction_db=12, weather_attenuation_db=4, jamming_db=2)
    assert got == pytest.approx(expected)


@pytest.mark.parametrize(
    ("margin", "expected"),
    [
        (10.0, LinkState.ONLINE),
        (6.01, LinkState.ONLINE),
        (ONLINE_MARGIN_DB, LinkState.DEGRADED),  # 邊界：==6 → DEGRADED（>6 才 ONLINE）
        (3.0, LinkState.DEGRADED),
        (DEGRADED_MARGIN_DB, LinkState.DEGRADED),  # 邊界：==0 → DEGRADED（>=0）
        (-0.01, LinkState.OFFLINE),
        (-50.0, LinkState.OFFLINE),
    ],
)
def test_link_state_thresholds(margin: float, expected: LinkState) -> None:
    assert link_state_from_margin(margin) is expected
