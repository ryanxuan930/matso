"""WeaponProfile 解析 / base_ph 插值 / 種子模板對 schema 驗證（O3.2）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from app.adjudication.seed_weapons import SEED_ARTILLERY, SEED_VEHICLES, SEED_WEAPONS
from app.adjudication.weapon import WeaponProfile

_SCHEMA_PATH = Path(__file__).resolve().parents[3] / "contracts" / "weaponeering.schema.json"


def _validator(defname: str) -> Draft202012Validator:
    """對某 $def 建驗證器；以 {$defs, $ref} 包裝使 allOf/$ref（如 artillery→kinetic）可解析。"""
    schema = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
    return Draft202012Validator({"$defs": schema["$defs"], "$ref": f"#/$defs/{defname}"})


def _kinetic_validator() -> Draft202012Validator:
    return _validator("kinetic")


def test_seed_weapons_conform_to_schema() -> None:
    validator = _kinetic_validator()
    for name, stats in SEED_WEAPONS.items():
        errors = sorted(validator.iter_errors(stats), key=str)
        assert not errors, f"{name} 不符 weaponeering.schema.json：{errors}"


def test_seed_artillery_conform_to_schema() -> None:
    validator = _validator("artillery")
    for name, stats in SEED_ARTILLERY.items():
        errors = sorted(validator.iter_errors(stats), key=str)
        assert not errors, f"{name} 不符 artillery $def：{errors}"


def test_seed_vehicles_conform_to_schema() -> None:
    validator = _validator("vehicle")
    for name, stats in SEED_VEHICLES.items():
        errors = sorted(validator.iter_errors(stats), key=str)
        assert not errors, f"{name} 不符 vehicle $def：{errors}"


def test_kinetic_kind_parsed() -> None:
    assert WeaponProfile.from_base_stats(SEED_WEAPONS["TANK_MAIN_GUN_120"]).kinetic_kind == (
        "TANK_MAIN_GUN"
    )
    assert WeaponProfile.from_base_stats(SEED_WEAPONS["RIFLE_556"]).kinetic_kind == "SMALL_ARMS"
    # artillery 種子亦可由 WeaponProfile 解析（火力欄位共用 kinetic）。
    assert WeaponProfile.from_base_stats(SEED_ARTILLERY["MORTAR_120"]).indirect_fire is True


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


def test_ph_interp_defaults_to_linear() -> None:
    w = WeaponProfile.from_base_stats(SEED_WEAPONS["RIFLE_556"])
    assert w.ph_interp == "linear"


def test_ph_interp_unknown_falls_back_to_linear() -> None:
    stats = dict(SEED_WEAPONS["RIFLE_556"])
    stats["ph_interp"] = "cubic-spline"  # 未支援 → 退回 linear
    w = WeaponProfile.from_base_stats(stats)
    assert w.ph_interp == "linear"


def test_base_ph_polynomial_passes_through_control_points() -> None:
    # 拉格朗日多項式必穿過所有控制點；#4。
    stats = dict(SEED_WEAPONS["RIFLE_556"])
    stats["ph_interp"] = "polynomial"
    stats["ph_by_range_band"] = [[100, 0.90], [300, 0.60], [600, 0.20]]
    w = WeaponProfile.from_base_stats(stats)
    assert w.base_ph(100) == pytest.approx(0.90)
    assert w.base_ph(300) == pytest.approx(0.60)
    assert w.base_ph(600) == pytest.approx(0.20)


def test_base_ph_polynomial_differs_from_linear_between_points() -> None:
    # 三點非共線 → 多項式在控制點間與線性插值不同（曲率）。
    bands = [[100, 0.90], [300, 0.60], [600, 0.20]]
    lin = WeaponProfile.from_base_stats({**SEED_WEAPONS["RIFLE_556"], "ph_by_range_band": bands})
    poly = WeaponProfile.from_base_stats(
        {**SEED_WEAPONS["RIFLE_556"], "ph_by_range_band": bands, "ph_interp": "polynomial"}
    )
    assert poly.base_ph(200) != pytest.approx(lin.base_ph(200))


def test_base_ph_polynomial_result_stays_in_unit_interval() -> None:
    # 多項式可能過衝；結果須夾於 [0,1]。
    stats = {
        **SEED_WEAPONS["RIFLE_556"],
        "ph_interp": "polynomial",
        "ph_by_range_band": [[100, 0.99], [110, 0.01], [600, 0.98]],  # 陡變易過衝
    }
    w = WeaponProfile.from_base_stats(stats)
    for r in range(100, 601, 10):
        assert 0.0 <= w.base_ph(float(r)) <= 1.0


def test_base_ph_polynomial_two_points_equals_linear() -> None:
    # <3 點時多項式退化為線性（直線）。
    stats = {**SEED_WEAPONS["RIFLE_556"], "ph_interp": "polynomial"}
    stats["ph_by_range_band"] = [[100, 0.80], [300, 0.50]]
    w = WeaponProfile.from_base_stats(stats)
    assert w.base_ph(200) == pytest.approx(0.65)


def test_expected_casualties_uses_pk_when_present() -> None:
    stats = {**SEED_WEAPONS["ATGM"], "pk_by_armor_class": {"ARMOR": 0.8, "INFANTRY": 0.5}}
    w = WeaponProfile.from_base_stats(stats)
    assert w.expected_casualties("ARMOR") == pytest.approx(0.8)
    assert w.expected_casualties("INFANTRY") == pytest.approx(0.5)
    assert w.expected_casualties("UAS") == pytest.approx(0.0)  # 未列 → 0


def test_expected_casualties_falls_back_to_damage_over_100() -> None:
    # 無 pk_by_armor_class → 舊 damage 值視為百分比擊殺率。
    stats = dict(SEED_WEAPONS["RIFLE_556"])
    stats.pop("pk_by_armor_class", None)
    w = WeaponProfile.from_base_stats(stats)
    assert w.expected_casualties("INFANTRY") == pytest.approx(35.0 / 100.0)
    assert w.expected_casualties("ARMOR") == pytest.approx(0.0)


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
