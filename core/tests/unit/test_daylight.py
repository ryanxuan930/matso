"""晝夜與照明（WP-C4a）：跨午夜、夜視、中性預設、接線。

[JCATS-A p.7]：晝夜與人工照明影響運動與偵測。
"""

from __future__ import annotations

import pytest

from app.adjudication.daylight import (
    DUSK_MINUTES,
    DayNight,
    LightLevel,
    coeffs_of,
    concealment_modifier,
    light_at,
    minutes_of_day,
    move_speed_modifier,
    optical_range_modifier,
)
from app.engine.clock import SimTime
from app.engine.daylight_wiring import LightClock, read_day_night, start_minute

_DAY = DayNight(sunrise_min=5 * 60 + 30, sunset_min=18 * 60)  # 05:30 / 18:00
_CROSS = DayNight(sunrise_min=5 * 60, sunset_min=22 * 60)  # 跨午夜的夜


def _at(day: DayNight, hhmm: str) -> LightLevel:
    h, m = (int(x) for x in hhmm.split(":"))
    return light_at(day, h * 60 + m)


# ---- 中性預設：既有局永遠是白天 ----


def test_an_undeclared_session_is_always_day() -> None:
    """**本卡最重要的保護**：既有局的 `WargameSession.dayNight` 是 NULL。

    未宣告 → 一律 DAY，而 DAY 的三個係數全 1.0 → golden 不必重錄。
    ⚠ SPEC_V2 原本把整張 C4 標成「golden：重錄」——那是針對天氣快照語意變更（C4b），
    晝夜這一段只要中性預設守住就不必。
    """
    for minute in (0, 3 * 60, 12 * 60, 23 * 60):
        assert light_at(DayNight(), minute) is LightLevel.DAY


def test_day_is_the_neutral_level() -> None:
    c = coeffs_of(LightLevel.DAY)
    assert (c.optical_range_mult, c.move_speed_mult, c.concealment_mult) == (1.0, 1.0, 1.0)


def test_reading_a_missing_or_broken_declaration_yields_undeclared() -> None:
    """想定的環境宣告壞掉不該讓整局跑不動，而「未宣告」的降級語義正好是既有局的行為。"""

    class _S:
        def __init__(self, value: object) -> None:
            self.day_night = value

    for raw in (None, {}, "05:30", {"sunrise_min": "x", "sunset_min": 1}, {"sunrise_min": 1}):
        assert read_day_night(_S(raw)).declared is False
    assert read_day_night(_S({"sunrise_min": 330, "sunset_min": 1080})).declared is True


def test_start_minute_defaults_to_midnight() -> None:
    class _S:
        day_night = None

    assert start_minute(_S()) == 0


# ---- 光照判定 ----


def test_the_normal_day_has_dawn_dusk_and_night_in_the_right_places() -> None:
    assert _at(_DAY, "12:00") is LightLevel.DAY
    assert _at(_DAY, "05:30") is LightLevel.DUSK  # 日出當下仍是曙光
    assert _at(_DAY, "06:00") is LightLevel.DAY
    assert _at(_DAY, "18:00") is LightLevel.DUSK
    assert _at(_DAY, "18:31") is LightLevel.NIGHT
    assert _at(_DAY, "02:00") is LightLevel.NIGHT


def test_a_night_that_crosses_midnight_is_still_night() -> None:
    """**這是最容易寫錯的一段**：`sunrise <= m < sunset` 那種寫法會把整個跨午夜的
    夜晚判成白天（日落 22:00、日出 05:00 時，00:00 不在 [5:00, 22:00) 內…但 21:00 在）。"""
    assert _at(_CROSS, "21:00") is LightLevel.DAY
    for hhmm in ("23:00", "00:00", "02:00", "04:00"):
        assert _at(_CROSS, hhmm) is LightLevel.NIGHT, f"{hhmm} 應該是夜間"
    assert _at(_CROSS, "05:30") is LightLevel.DAY


def test_a_dusk_band_that_straddles_midnight_still_works() -> None:
    """**這才是真正會分出對錯的案例**，而我第一版沒測到。

    「日落 22:00、日出 05:00」看起來像跨午夜，但被判定的**白天區間**（05:30–21:30）
    根本沒有繞回去，所以把 `_within` 換成單純的 `start <= m < end` 照樣全綠——
    突變測試才把這件事抓出來。

    真正會繞的是日出 00:15 這種：曙光帶 23:45→00:45 橫跨午夜。
    """
    day = DayNight(sunrise_min=15, sunset_min=12 * 60)  # 00:15 日出、12:00 日落
    assert _at(day, "23:50") is LightLevel.DUSK  # ← 繞回午夜前
    assert _at(day, "00:30") is LightLevel.DUSK
    assert _at(day, "01:00") is LightLevel.DAY
    assert _at(day, "23:00") is LightLevel.NIGHT


def test_dusk_band_is_symmetric_around_both_events() -> None:
    sunrise = _DAY.sunrise_min or 0
    assert light_at(_DAY, sunrise - DUSK_MINUTES + 1) is LightLevel.DUSK
    assert light_at(_DAY, sunrise - DUSK_MINUTES - 1) is LightLevel.NIGHT


def test_minutes_of_day_wraps_and_respects_the_start_time() -> None:
    assert minutes_of_day(0) == 0
    assert minutes_of_day(90 * 60_000) == 90
    assert minutes_of_day(0, start_min=13 * 60) == 13 * 60  # 13:00 開演
    assert minutes_of_day(25 * 3600 * 1000) == 60  # 跨日繞回


# ---- 夜視 ----


def test_night_vision_removes_the_penalty_entirely() -> None:
    """規格明列「有夜視裝備的單位不受罰」。"""
    for level in LightLevel:
        assert optical_range_modifier(level, night_capable=True) == 1.0
        assert move_speed_modifier(level, night_capable=True) == 1.0


def test_without_night_vision_night_hurts_both_sight_and_speed() -> None:
    assert optical_range_modifier(LightLevel.NIGHT, night_capable=False) == 0.3
    assert move_speed_modifier(LightLevel.NIGHT, night_capable=False) == 0.8
    assert optical_range_modifier(LightLevel.DUSK, night_capable=False) > 0.3


def test_concealment_is_environmental_and_ignores_night_vision() -> None:
    """「我多好被看到」是環境，對雙方成立——**不因為我有夜視就變得比較顯眼或比較隱蔽**。

    與 `optical_range_modifier`（我看多遠）刻意分開：合成一個數字會讓「我方有夜視」
    同時變成「敵人比較容易看見我」。
    """
    assert concealment_modifier(LightLevel.NIGHT) < concealment_modifier(LightLevel.DAY)
    assert concealment_modifier(LightLevel.DAY) == 1.0


# ---- LightClock 接線 ----


def test_light_clock_reports_undeclared_so_callers_can_skip_entirely() -> None:
    """未宣告時消費端整段跳過——「一次都不算」比「算出來剛好是 1.0」更省，
    也更不會在改係數時意外動到既有局。"""
    assert LightClock(DayNight()).declared is False
    assert LightClock(_DAY).declared is True


def test_light_clock_tracks_the_simulated_clock() -> None:
    clock = LightClock(_DAY, start_min=17 * 60)  # 17:00 開演
    assert clock.level_at(SimTime(tick=0, sim_time_ms=0)) is LightLevel.DAY
    two_hours = SimTime(tick=120, sim_time_ms=120 * 60_000)  # → 19:00
    assert clock.level_at(two_hours) is LightLevel.NIGHT


def test_detection_at_night_is_worse_and_night_vision_recovers_it() -> None:
    """驗收條文的可測形式：同一對觀測者/目標，只有光照不同 → 偵測率顯著下降；
    配了夜視的觀測者不受罰。"""
    from app.intel.sensor import DetectionEnv, SensorProfile, detect_probability

    sensor = SensorProfile.from_base_stats(
        {"sensor_kind": "OPTICAL", "max_range_m": 5000, "detect_curve": [[5000, 0.9]]}
    )
    day = detect_probability(sensor, 1000, DetectionEnv(los_clear=True))
    night = detect_probability(
        sensor,
        1000,
        DetectionEnv(
            los_clear=True,
            light_modifier=optical_range_modifier(LightLevel.NIGHT, night_capable=False),
            concealment_modifier=concealment_modifier(LightLevel.NIGHT),
        ),
    )
    night_eq = detect_probability(
        sensor,
        1000,
        DetectionEnv(
            los_clear=True,
            light_modifier=optical_range_modifier(LightLevel.NIGHT, night_capable=True),
            concealment_modifier=concealment_modifier(LightLevel.NIGHT),
        ),
    )
    assert night < night_eq < day
    assert night == pytest.approx(day * 0.3 * 0.6)


def test_night_capable_is_an_equipment_flag_not_a_unit_flag() -> None:
    """掛在單位上的話，一個連只要配一支夜視鏡就整連免罰——而那正是夜戰最關鍵的差別。"""
    from app.intel.sensor import SensorProfile

    base = {"sensor_kind": "OPTICAL", "max_range_m": 5000, "detect_curve": [[5000, 0.9]]}
    assert SensorProfile.from_base_stats(base).night_capable is False  # 缺鍵→無夜視
    assert SensorProfile.from_base_stats({**base, "night_capable": True}).night_capable is True
