"""WeaponProfile 解析 / base_ph 插值 / 種子模板對 schema 驗證（O3.2）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from app.adjudication.seed_weapons import SEED_WEAPONS
from app.adjudication.weapon import WeaponProfile

_SCHEMA_PATH = Path(__file__).resolve().parents[3] / "contracts" / "weaponeering.schema.json"


def _kinetic_validator() -> Draft202012Validator:
    schema = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
    return Draft202012Validator(schema["$defs"]["kinetic"])


def test_seed_weapons_conform_to_schema() -> None:
    validator = _kinetic_validator()
    for name, stats in SEED_WEAPONS.items():
        errors = sorted(validator.iter_errors(stats), key=str)
        assert not errors, f"{name} 不符 weaponeering.schema.json：{errors}"


def test_all_seed_weapons_parse() -> None:
    for stats in SEED_WEAPONS.values():
        w = WeaponProfile.from_base_stats(stats)
        assert w.max_range_m > 0
        assert len(w.ph_by_range_band) >= 1


def test_base_ph_clamps_outside_control_points() -> None:
    w = WeaponProfile.from_base_stats(SEED_WEAPONS["RIFLE_556"])
    assert w.base_ph(0) == pytest.approx(0.80)  # 近端夾住
    assert w.base_ph(10_000) == pytest.approx(0.20)  # 遠端夾住


def test_base_ph_linear_interpolation() -> None:
    w = WeaponProfile.from_base_stats(SEED_WEAPONS["RIFLE_556"])
    # 100m→0.80, 300m→0.50 → 200m 應為中點 0.65
    assert w.base_ph(200) == pytest.approx(0.65)


def test_damage_against_unknown_armor_is_zero() -> None:
    w = WeaponProfile.from_base_stats(SEED_WEAPONS["RIFLE_556"])
    assert w.damage_against("ARMOR") == pytest.approx(0.0)
    assert w.damage_against("UNKNOWN_CLASS") == pytest.approx(0.0)
    assert w.damage_against("INFANTRY") == pytest.approx(35.0)


def test_rejects_non_increasing_range_bands() -> None:
    bad = dict(SEED_WEAPONS["RIFLE_556"])
    bad["ph_by_range_band"] = [[300, 0.5], [100, 0.8]]  # range 未遞增
    with pytest.raises(ValueError, match="遞增"):
        WeaponProfile.from_base_stats(bad)


def test_rejects_out_of_bounds_ph() -> None:
    bad = dict(SEED_WEAPONS["RIFLE_556"])
    bad["ph_by_range_band"] = [[100, 1.5]]
    with pytest.raises(ValueError, match=r"\[0,1\]"):
        WeaponProfile.from_base_stats(bad)


def test_rejects_empty_range_bands() -> None:
    bad = dict(SEED_WEAPONS["RIFLE_556"])
    bad["ph_by_range_band"] = []
    with pytest.raises(ValueError, match="ph_by_range_band"):
        WeaponProfile.from_base_stats(bad)
