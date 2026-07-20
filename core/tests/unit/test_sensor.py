"""SensorProfile / detect_probability / fidelity 分級（O3.3）+ 種子對 schema 驗證。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from app.intel.seed_sensors import SEED_SENSORS
from app.intel.sensor import (
    DetectionEnv,
    SensorProfile,
    detect_probability,
    fidelity_for,
)
from app.models.enums import IntelFidelity

_SCHEMA_PATH = Path(__file__).resolve().parents[3] / "contracts" / "weaponeering.schema.json"


def _sensor(name: str) -> SensorProfile:
    return SensorProfile.from_base_stats(SEED_SENSORS[name])


def test_seed_sensors_conform_to_schema() -> None:
    schema = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema["$defs"]["sensor"])
    for name, stats in SEED_SENSORS.items():
        errors = sorted(validator.iter_errors(stats), key=str)
        assert not errors, f"{name} 不符 sensor schema：{errors}"


def test_base_detect_interpolates_and_clamps() -> None:
    s = _sensor("EO_DAY")  # 500→0.95, 2000→0.75
    assert s.base_detect(0) == pytest.approx(0.95)
    assert s.base_detect(1250) == pytest.approx(0.85)  # 中點
    assert s.base_detect(99_999) == pytest.approx(0.40)


def test_beyond_max_range_zero() -> None:
    s = _sensor("EO_DAY")
    assert detect_probability(s, s.max_range_m + 1, DetectionEnv(los_clear=True)) == 0.0


def test_los_required_sensor_blind_without_los() -> None:
    optical = _sensor("EO_DAY")  # needs LOS
    assert detect_probability(optical, 1000, DetectionEnv(los_clear=False)) == 0.0


def test_acoustic_ignores_los() -> None:
    acoustic = _sensor("ACOUSTIC_ARRAY")  # 不需 LOS
    p = detect_probability(acoustic, 300, DetectionEnv(los_clear=False))
    assert p > 0.0


def test_unit_coefficients_reduce_to_base() -> None:
    s = _sensor("EO_DAY")
    assert detect_probability(s, 1000, DetectionEnv(los_clear=True)) == pytest.approx(
        s.base_detect(1000)
    )


def test_probability_clamped() -> None:
    s = _sensor("EO_DAY")
    env = DetectionEnv(los_clear=True, target_signature_modifier=10.0)
    assert detect_probability(s, 500, env) == 1.0  # 不越界


def test_fidelity_thresholds() -> None:
    assert fidelity_for(0.9) is IntelFidelity.IDENTIFIED
    assert fidelity_for(0.6) is IntelFidelity.CLASSIFIED
    assert fidelity_for(0.2) is IntelFidelity.DETECTED


def test_concealment_lowers_probability() -> None:
    s = _sensor("EO_DAY")
    clear = detect_probability(s, 1000, DetectionEnv(los_clear=True))
    hidden = detect_probability(s, 1000, DetectionEnv(los_clear=True, concealment_modifier=0.3))
    assert hidden < clear


def test_rejects_empty_detect_curve() -> None:
    with pytest.raises(ValueError, match="detect_curve"):
        SensorProfile.from_base_stats(
            {"sensor_kind": "OPTICAL", "max_range_m": 100, "detect_curve": []}
        )


def test_rejects_out_of_bounds_p_detect() -> None:
    with pytest.raises(ValueError, match=r"\[0,1\]"):
        SensorProfile.from_base_stats(
            {"sensor_kind": "IR", "max_range_m": 100, "detect_curve": [[100, 1.5]]}
        )


def test_rejects_non_increasing_range() -> None:
    with pytest.raises(ValueError, match="遞增"):
        SensorProfile.from_base_stats(
            {"sensor_kind": "IR", "max_range_m": 100, "detect_curve": [[300, 0.5], [100, 0.8]]}
        )
