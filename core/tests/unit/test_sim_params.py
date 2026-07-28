"""推演參數（#93 P1）：預設等同原常數、壞值退預設、預覽與執行同源。

最重要的不變式：**未設定時與硬編碼行為位元相同** —— 既有推演局、既有測試、
golden replay 都不能因為「加了可調參數」而改變結果。
"""

from __future__ import annotations

import pytest

from app.movement import params as mp
from app.sim_params import DEFAULTS, SimParams, parse_sim_params, to_config


def test_defaults_match_the_original_constants() -> None:
    """預設值必須等於原本寫死的常數——這條若鬆掉，等於偷偷改了所有既有局的物理。"""
    p = SimParams()

    assert p.foot_xc_kmh == mp.FOOT_XC_KMH
    assert p.foot_road_kmh == mp.FOOT_ROAD_KMH
    assert p.vehicle_fallback_kmh == mp.MOVE_SPEED_KMH
    assert p.march_attrition == mp.MARCH_ATTRITION_PER_KM
    assert p.attrition_for("FOOT") == mp.march_attrition_per_km("FOOT")
    assert p.attrition_for("TRACKED") == mp.march_attrition_per_km("TRACKED")


def test_unknown_profile_falls_back_like_before() -> None:
    assert SimParams().attrition_for("HOVERCRAFT") == mp.march_attrition_per_km("HOVERCRAFT")


@pytest.mark.parametrize("raw", [None, "nope", [], 42])
def test_missing_or_wrong_shaped_config_is_all_defaults(raw: object) -> None:
    assert parse_sim_params(raw) == SimParams()


def test_values_are_applied() -> None:
    p = parse_sim_params(
        {
            "foot_xc_kmh": 4.0,
            "foot_road_kmh": 7.5,
            "resupply_range_km": 5.0,
            "intrinsic_optical_range_m": 8000,
            "sensor_interval_ticks": 2,
            "march_attrition": {"FOOT": 0.09},
        }
    )

    assert p.foot_xc_kmh == 4.0
    assert p.foot_road_kmh == 7.5
    assert p.resupply_range_km == 5.0
    assert p.intrinsic_optical_range_m == 8000
    assert p.sensor_interval_ticks == 2
    assert p.attrition_for("FOOT") == 0.09
    # 未指定的 profile 保留預設（不是被清成 0）
    assert p.attrition_for("TRACKED") == mp.MARCH_ATTRITION_PER_KM["TRACKED"]


@pytest.mark.parametrize(
    ("field", "bad"),
    [
        ("foot_xc_kmh", 0),  # 0 速度會讓單位永遠走不到
        ("foot_xc_kmh", -3),
        ("foot_xc_kmh", "fast"),
        ("resupply_range_km", -1),
        ("intrinsic_optical_range_m", None),
    ],
)
def test_bad_values_fall_back_per_field(field: str, bad: object) -> None:
    """壞值只影響該欄，其餘照常——一個打錯的數字不該讓整場推演跑不動。"""
    p = parse_sim_params({field: bad, "foot_road_kmh": 9.0})

    assert getattr(p, field) == getattr(DEFAULTS, field)
    assert p.foot_road_kmh == 9.0  # 同批的好值仍生效


def test_sensor_interval_is_at_least_one_tick() -> None:
    """0 或負的間隔會讓 `tick % interval` 爆掉或每 tick 全掃。"""
    assert parse_sim_params({"sensor_interval_ticks": 0}).sensor_interval_ticks == 1
    assert parse_sim_params({"sensor_interval_ticks": -5}).sensor_interval_ticks == 1


def test_round_trip_through_config() -> None:
    """存進 DB 再讀回來要一致（設定頁 PUT → GET 的往返）。"""
    p = parse_sim_params({"foot_xc_kmh": 6.25, "march_attrition": {"WHEELED": 0.04}})

    assert parse_sim_params(to_config(p)) == p
