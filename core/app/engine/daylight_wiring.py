"""晝夜在活執行期的接線（WP-C4a）。

`adjudication/daylight.py` 是純函數；本模組只做 I/O 邊界：把該局的日出日落宣告讀出來、
把 `SimTime` 翻成光照等級、供偵測與移動查詢。

## 中性保證做在入口

`read_day_night()` 讀不到宣告就回 `DayNight()`（未宣告），而 `light_at()` 對未宣告一律回
`DAY`——DAY 的三個係數全是 1.0。既有局的 `WargameSession` 沒有這個欄位，因此
**逐 tick 這條路徑算出來的一律是 1.0，位元不變**，golden 不必重錄。

⚠ SPEC_V2 原本把整張 C4 標成「golden：重錄」。那是針對天氣快照語意變更（C4b）；
晝夜這一段只要中性預設守住就不必重錄——與 WP-C1/C3 用過的是同一招。

## 為什麼光照是「每 tick 由時刻導出」而不是熱狀態欄位

光照是**時刻的函數**，不是可獨立變動的狀態。存進熱狀態就會出現「熱狀態說 NIGHT、
時鐘說中午」這種對不起來的可能，而且 checkpoint 還會把它一起存下來。
照明彈那種**局部且短暫**的覆寫才需要狀態（見 `ILLUM_DURATION_TICKS`；本卡未做）。
"""

from __future__ import annotations

from typing import Any

from app.adjudication.daylight import (
    DayNight,
    LightLevel,
    light_at,
    minutes_of_day,
)
from app.engine.clock import SimTime


def read_day_night(session: Any) -> DayNight:
    """`WargameSession.day_night` → 宣告。缺欄位/缺鍵/型別不對一律回未宣告。

    **不要在這裡拋例外**：想定的環境宣告壞掉不該讓整局跑不動，而「未宣告」的降級
    語義（整天白天）正好就是既有局的行為。
    """
    raw = getattr(session, "day_night", None)
    if not isinstance(raw, dict):
        return DayNight()
    sunrise, sunset = raw.get("sunrise_min"), raw.get("sunset_min")
    if not isinstance(sunrise, (int, float)) or not isinstance(sunset, (int, float)):
        return DayNight()
    return DayNight(sunrise_min=int(sunrise) % 1440, sunset_min=int(sunset) % 1440)


def start_minute(session: Any) -> int:
    """該局 tick 0 對應的當日分鐘。未宣告 → 0（午夜開演）。"""
    raw = getattr(session, "day_night", None)
    value = raw.get("start_min") if isinstance(raw, dict) else None
    return int(value) % 1440 if isinstance(value, (int, float)) else 0


class LightClock:
    """把 `SimTime` 翻成光照等級。整局建一次，逐 tick 查。

    宣告在開局時快照（同 `create_session_from_scenario` 的紀律）——推演中途改想定不影響
    進行中的局。
    """

    def __init__(self, day: DayNight, start_min: int = 0) -> None:
        self._day = day
        self._start_min = start_min

    @property
    def declared(self) -> bool:
        """該局有沒有宣告日出日落。**沒有宣告時呼叫端可以整段跳過**——
        那條路徑一次都不算，比「算出來剛好是 1.0」更省也更不會出錯。"""
        return self._day.declared

    def level_at(self, now: SimTime) -> LightLevel:
        return light_at(self._day, minutes_of_day(now.sim_time_ms, start_min=self._start_min))

    def minute_at(self, now: SimTime) -> int:
        """當前模擬時刻的當日分鐘——供 COP 顯示「現在幾點」。"""
        return minutes_of_day(now.sim_time_ms, start_min=self._start_min)


__all__ = ["LightClock", "read_day_night", "start_minute"]
