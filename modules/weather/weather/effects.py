"""原始氣象值 → 效果係數映射（SPEC §5.2）。純函數、確定性。

**v0 佔位映射**（同 mobility_matrix/weaponeering）：真實係數由 effects_mapping.yaml 定義、
White Cell 可於推演中調整（記入 Ledger）——yaml 外部化與熱調整為 O5.2。範圍嚴格符合
weather_payload.schema.json（各 modifier 夾在合法區間）。
"""

from __future__ import annotations

from weather.payload import RawWeather, WeatherEffects

# v0 映射常數（可調參數；O5.2 移入 effects_mapping.yaml）
_RF_DB_PER_MMHR = 0.5  # 雨衰：每 mm/hr 的 RF 衰減（dB）
_MOBILITY_RAIN_FULL_MMHR = 50.0  # 此雨量使機動降至下限
_MOBILITY_FLOOR = 0.4
_IR_RAIN_FULL_MMHR = 60.0  # 此雨量使 IR 感測降至下限
_IR_FLOOR = 0.3
_OPTICAL_FLOOR = 0.1
_VISIBILITY_FULL_M = 10000.0  # 此能見度以上光學不受限
_UAV_MAX_WIND_MS = 12.0
_UAV_MAX_PRECIP_MMHR = 25.0
_ROTARY_MAX_WIND_MS = 20.0
_ROTARY_MIN_CLOUD_BASE_M = 150.0
_ARTY_DISPERSION_PER_WIND_MS = 0.02  # 每 m/s 風速增加的火砲散佈


def _clamp(value: float, lo: float, hi: float) -> float:
    return min(hi, max(lo, value))


def derive_effects(raw: RawWeather) -> WeatherEffects:
    """由原始氣象值推導效果係數。純函數；範圍保證符合 schema。"""
    precip = max(0.0, raw.precipitation_mmhr)
    wind = max(0.0, raw.wind_ms)

    rf = precip * _RF_DB_PER_MMHR  # >= 0
    mobility = _clamp(
        1.0 - precip / _MOBILITY_RAIN_FULL_MMHR * (1.0 - _MOBILITY_FLOOR), _MOBILITY_FLOOR, 1.0
    )
    optical = _clamp(raw.visibility_m / _VISIBILITY_FULL_M, _OPTICAL_FLOOR, 1.0)
    ir = _clamp(1.0 - precip / _IR_RAIN_FULL_MMHR * (1.0 - _IR_FLOOR), _IR_FLOOR, 1.0)
    uav = wind < _UAV_MAX_WIND_MS and precip < _UAV_MAX_PRECIP_MMHR
    rotary = wind < _ROTARY_MAX_WIND_MS and raw.cloud_base_m > _ROTARY_MIN_CLOUD_BASE_M
    dispersion = 1.0 + wind * _ARTY_DISPERSION_PER_WIND_MS  # >= 1

    return WeatherEffects(
        rf_attenuation_db=rf,
        mobility_modifier=mobility,
        sensor_optical_modifier=optical,
        sensor_ir_modifier=ir,
        uav_operability=uav,
        rotary_wing_operability=rotary,
        artillery_dispersion_modifier=dispersion,
    )
