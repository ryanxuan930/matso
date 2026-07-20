"""原始氣象值 → 效果係數映射（O5.1）：單調性 + 範圍。"""

from __future__ import annotations

from weather.effects import derive_effects
from weather.payload import RawWeather


def test_clear_weather_full_operability() -> None:
    e = derive_effects(RawWeather())  # 無雨、微風、良好能見度
    assert e.rf_attenuation_db == 0.0
    assert e.mobility_modifier == 1.0
    assert e.uav_operability is True
    assert e.rotary_wing_operability is True
    assert e.artillery_dispersion_modifier == 1.0


def test_rain_reduces_mobility_and_ir_raises_rf() -> None:
    light = derive_effects(RawWeather(precipitation_mmhr=5))
    heavy = derive_effects(RawWeather(precipitation_mmhr=40))
    assert heavy.mobility_modifier < light.mobility_modifier  # 雨越大機動越差
    assert heavy.rf_attenuation_db > light.rf_attenuation_db  # 雨越大 RF 衰減越多
    assert heavy.sensor_ir_modifier < light.sensor_ir_modifier


def test_low_visibility_reduces_optical() -> None:
    clear = derive_effects(RawWeather(visibility_m=10000))
    foggy = derive_effects(RawWeather(visibility_m=1000))
    assert foggy.sensor_optical_modifier < clear.sensor_optical_modifier


def test_high_wind_grounds_uav_and_raises_dispersion() -> None:
    calm = derive_effects(RawWeather(wind_ms=3))
    gale = derive_effects(RawWeather(wind_ms=18))
    assert calm.uav_operability is True
    assert gale.uav_operability is False  # 強風 → UAV 不可飛
    assert gale.artillery_dispersion_modifier > calm.artillery_dispersion_modifier


def test_low_cloud_base_grounds_rotary() -> None:
    assert derive_effects(RawWeather(cloud_base_m=100)).rotary_wing_operability is False


def test_all_effects_within_schema_bounds() -> None:
    # 極端值不得讓 modifier 越界（schema：0..1 / >=1 / >=0）
    for raw in (
        RawWeather(precipitation_mmhr=999, wind_ms=999, visibility_m=0, cloud_base_m=0),
        RawWeather(precipitation_mmhr=0, wind_ms=0, visibility_m=99999, cloud_base_m=99999),
    ):
        e = derive_effects(raw)
        assert e.rf_attenuation_db >= 0
        assert 0.0 <= e.mobility_modifier <= 1.0
        assert 0.0 <= e.sensor_optical_modifier <= 1.0
        assert 0.0 <= e.sensor_ir_modifier <= 1.0
        assert e.artillery_dispersion_modifier >= 1.0
