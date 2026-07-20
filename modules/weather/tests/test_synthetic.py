"""SYNTHETIC 關鍵影格插值正確性（O5.1）。"""

from __future__ import annotations

import pytest
from weather.synthetic import SyntheticWeather

_SCRIPT = {
    "cells": {
        "cell_a": {
            "keyframes": [
                {
                    "tick": 0,
                    "precipitation_mmhr": 0,
                    "wind_ms": 2,
                    "wind_dir_deg": 350,
                    "visibility_m": 10000,
                    "cloud_base_m": 3000,
                },
                {
                    "tick": 100,
                    "precipitation_mmhr": 20,
                    "wind_ms": 12,
                    "wind_dir_deg": 10,
                    "visibility_m": 2000,
                    "cloud_base_m": 500,
                },
            ]
        },
        "cell_b": {"keyframes": [{"tick": 0, "precipitation_mmhr": 5}]},
    }
}


def _engine() -> SyntheticWeather:
    return SyntheticWeather.from_script(_SCRIPT)


def test_exact_at_keyframe() -> None:
    eng = _engine()
    assert eng.interpolate("cell_a", 0).precipitation_mmhr == 0
    assert eng.interpolate("cell_a", 100).precipitation_mmhr == 20
    assert eng.interpolate("cell_a", 100).visibility_m == 2000


def test_linear_midpoint() -> None:
    raw = _engine().interpolate("cell_a", 50)  # 0→20, 2→12, 10000→2000
    assert raw.precipitation_mmhr == pytest.approx(10.0)
    assert raw.wind_ms == pytest.approx(7.0)
    assert raw.visibility_m == pytest.approx(6000.0)
    assert raw.cloud_base_m == pytest.approx(1750.0)


def test_wind_dir_shortest_angle() -> None:
    # 350° → 10°：最短路徑 +20°（穿越 0），中點 = 0°（非 180°）
    assert _engine().interpolate("cell_a", 50).wind_dir_deg == pytest.approx(0.0)


def test_endpoint_clamping() -> None:
    eng = _engine()
    assert eng.interpolate("cell_a", -50).precipitation_mmhr == 0  # 起點前夾住
    assert eng.interpolate("cell_a", 999).precipitation_mmhr == 20  # 終點後夾住


def test_single_keyframe_constant() -> None:
    eng = _engine()
    assert eng.interpolate("cell_b", 0).precipitation_mmhr == 5
    assert eng.interpolate("cell_b", 500).precipitation_mmhr == 5  # 恆定


def test_payload_covers_all_cells_sorted() -> None:
    payload = _engine().payload_at(50)
    assert [c.h3_index for c in payload.cells] == ["cell_a", "cell_b"]  # 排序確定
    assert payload.issued_at_sim_tick == 50
    assert payload.mode.value == "SYNTHETIC"
    assert payload.stale is False


def test_deterministic() -> None:
    a = _engine().payload_at(37).to_dict()
    b = _engine().payload_at(37).to_dict()
    assert a == b


def test_rejects_empty_script() -> None:
    with pytest.raises(ValueError, match="cells"):
        SyntheticWeather.from_script({})


def test_rejects_cell_without_keyframes() -> None:
    with pytest.raises(ValueError, match="keyframes"):
        SyntheticWeather.from_script({"cells": {"x": {}}})
