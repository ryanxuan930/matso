"""晝夜與照明（WP-C4a）——純同步純函數（紅線 2）。

[JCATS-A p.7]：晝夜與人工照明影響運動與偵測。`SimClock` 已經有模擬時刻，缺的只是
「把時刻翻成光照等級」與「光照等級對誰有什麼影響」這兩層。

## 為什麼日出日落是想定參數而不是天文計算

真的算太陽仰角要緯度、日期、時區與大氣折射，而**這裡不需要那個精度**：兵推要的是
「這場推演是白天打還是晚上打」。想定作者給兩個時刻，比一個他無法覆寫的天文公式有用得多
（夜間演訓本來就會挑時間）。

## 中性預設：沒宣告日出日落的既有局永遠是白天

`DayNight()` 的預設是「整天都是 DAY」，而 DAY 的三個係數全是 1.0。既有局的
`WargameSession` 沒有這個欄位 → 光照恆為 DAY → **一個位元都不差**，golden 不必重錄。

⚠ SPEC_V2 原本寫本卡「golden：重錄」。那是針對天氣快照語意變更（C4b）；
晝夜這一段只要中性預設守住就不必重錄——與 C1/C3 用過的是同一招。

## 夜視是**裝備**屬性，不是單位屬性

`night_capable` 掛在感測器裝備上。掛在單位上的話，一個連只要配一支夜視鏡就整連免罰，
而那正是夜戰最關鍵的差別（有沒有配發到人）。
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

_MS_PER_DAY = 86_400_000
_MS_PER_MIN = 60_000


class LightLevel(enum.StrEnum):
    """光照等級。**DAY 是中性值**——所有係數 1.0。"""

    DAY = "DAY"
    DUSK = "DUSK"  # 曙暮光：日出前/日落後的過渡帶
    NIGHT = "NIGHT"


@dataclass(frozen=True, slots=True)
class LightCoeffs:
    """一個光照等級的三個係數。

    `optical_range_mult`：無夜視的光學感測距離倍率。
    `move_speed_mult`：無夜視單位的移動速度倍率。
    `concealment_mult`：**被偵測**的訊號倍率——夜裡比較難被看見（對雙方都成立）。
    """

    optical_range_mult: float
    move_speed_mult: float
    concealment_mult: float


# v0 校準值。規格明列 NIGHT 光學 ×0.3、移動 ×0.8。
# **DAY 全 1.0 ＝中性**（見模組說明）。
LIGHT_COEFFS: dict[LightLevel, LightCoeffs] = {
    LightLevel.DAY: LightCoeffs(1.0, 1.0, 1.0),
    # 曙暮光不是「半個晚上」——肉眼在那段時間退化得比線性快，但還沒到要開夜視的程度。
    LightLevel.DUSK: LightCoeffs(0.6, 0.9, 0.85),
    LightLevel.NIGHT: LightCoeffs(0.3, 0.8, 0.6),
}

# 曙暮光帶的長度（分鐘）：日出前 N 分鐘與日落後 N 分鐘算 DUSK。
DUSK_MINUTES = 30

# 照明彈在其半徑內把光照拉回 DAY 的持續時間（tick）。
ILLUM_DURATION_TICKS = 3


@dataclass(frozen=True, slots=True)
class DayNight:
    """該局的日出日落宣告。

    `sunrise_min` / `sunset_min`：自模擬日 00:00 起算的分鐘數。
    **兩者皆 None ＝未宣告 ＝整天都是 DAY**（既有局的語義）。
    """

    sunrise_min: int | None = None
    sunset_min: int | None = None

    @property
    def declared(self) -> bool:
        return self.sunrise_min is not None and self.sunset_min is not None


def minutes_of_day(sim_time_ms: int, *, start_min: int = 0) -> int:
    """模擬毫秒 → 當日分鐘數（0–1439）。

    `start_min` 是想定宣告的**開演時刻**（tick 0 對應的當日分鐘）；未宣告則 tick 0 ＝午夜。
    """
    total = (sim_time_ms % _MS_PER_DAY) // _MS_PER_MIN
    return int((total + start_min) % 1440)


def light_at(day: DayNight, minute: int) -> LightLevel:
    """當日第幾分鐘的光照等級。未宣告日出日落 → 一律 DAY。

    ⚠ 跨午夜的夜間（日落 22:00、日出 05:00）要算對——**這是最容易寫錯的一段**：
    `sunrise <= m < sunset` 那種寫法在跨午夜時會把整個夜晚判成白天。
    """
    if not day.declared:
        return LightLevel.DAY
    sunrise, sunset = int(day.sunrise_min or 0), int(day.sunset_min or 0)
    if _within(minute, sunrise + DUSK_MINUTES, sunset - DUSK_MINUTES):
        return LightLevel.DAY
    if _within(minute, sunrise - DUSK_MINUTES, sunrise + DUSK_MINUTES) or _within(
        minute, sunset - DUSK_MINUTES, sunset + DUSK_MINUTES
    ):
        return LightLevel.DUSK
    return LightLevel.NIGHT


def _within(minute: int, start: int, end: int) -> bool:
    """`minute` 是否落在 [start, end) 之內，**允許跨午夜**（start > end 時繞回去）。"""
    start, end = start % 1440, end % 1440
    if start <= end:
        return start <= minute < end
    return minute >= start or minute < end


def coeffs_of(level: LightLevel) -> LightCoeffs:
    return LIGHT_COEFFS.get(level, LIGHT_COEFFS[LightLevel.DAY])


def optical_range_modifier(level: LightLevel, *, night_capable: bool) -> float:
    """光學/紅外感測的距離倍率。**有夜視就不受罰**（規格明列）。"""
    return 1.0 if night_capable else coeffs_of(level).optical_range_mult


def move_speed_modifier(level: LightLevel, *, night_capable: bool) -> float:
    """夜間行軍的速度倍率。有夜視器材的單位不受罰。"""
    return 1.0 if night_capable else coeffs_of(level).move_speed_mult


def concealment_modifier(level: LightLevel) -> float:
    """夜裡比較難被看見。**對雙方都成立**——這不是誰的優勢，是環境。

    與 `optical_range_modifier` 分開的理由：那是「我看多遠」，這是「我多好被看到」。
    合成一個數字會讓「我方有夜視」同時變成「敵人比較容易看見我」。
    """
    return coeffs_of(level).concealment_mult


__all__ = [
    "DUSK_MINUTES",
    "ILLUM_DURATION_TICKS",
    "LIGHT_COEFFS",
    "DayNight",
    "LightCoeffs",
    "LightLevel",
    "coeffs_of",
    "concealment_modifier",
    "light_at",
    "minutes_of_day",
    "move_speed_modifier",
    "optical_range_modifier",
]
