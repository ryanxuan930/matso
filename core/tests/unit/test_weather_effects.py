"""Core 端天氣效果映射 + WeatherState（O5.3）。"""

from __future__ import annotations

from app.weather import (
    CLEAR,
    CellEffects,
    WeatherState,
    aggregate_weather_modifier,
    detection_weather_modifier,
    engagement_weather_modifier,
    movement_mobility_modifier,
    rotary_wing_operable,
    uav_operable,
)

_STORM = CellEffects(
    mobility_modifier=0.6,
    sensor_optical_modifier=0.4,
    sensor_ir_modifier=0.7,
    uav_operability=False,
    rotary_wing_operability=True,
    artillery_dispersion_modifier=1.3,
)


def test_clear_defaults_are_neutral() -> None:
    assert CLEAR.mobility_modifier == 1.0
    assert CLEAR.sensor_optical_modifier == 1.0
    assert CLEAR.uav_operability is True


def test_state_effects_at_missing_cell_is_clear() -> None:
    state = WeatherState({"c1": _STORM})
    assert state.effects_at("c1").sensor_optical_modifier == 0.4
    assert state.effects_at("unknown") is CLEAR  # 查無 → 晴天


def test_state_clear_factory() -> None:
    state = WeatherState.clear()
    assert state.stale is False
    assert state.effects_at("anything") is CLEAR


def test_engagement_direct_uses_optical() -> None:
    assert engagement_weather_modifier(_STORM, indirect_fire=False) == 0.4
    assert engagement_weather_modifier(CLEAR, indirect_fire=False) == 1.0


def test_engagement_indirect_uses_dispersion() -> None:
    # 間瞄：散佈 1.3 → 命中效益 1/1.3
    assert engagement_weather_modifier(_STORM, indirect_fire=True) == 1.0 / 1.3
    assert engagement_weather_modifier(CLEAR, indirect_fire=True) == 1.0


def test_detection_by_sensor_kind() -> None:
    assert detection_weather_modifier(_STORM, "OPTICAL") == 0.4
    assert detection_weather_modifier(_STORM, "IR") == 0.7
    assert detection_weather_modifier(_STORM, "RADAR") == 1.0  # v0 不受天氣影響
    assert detection_weather_modifier(_STORM, "ACOUSTIC") == 1.0


def test_aggregate_and_movement_and_air() -> None:
    assert aggregate_weather_modifier(_STORM) == 0.4
    assert movement_mobility_modifier(_STORM) == 0.6
    assert uav_operable(_STORM) is False
    assert rotary_wing_operable(_STORM) is True
