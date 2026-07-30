"""天氣逐 tick 刷新（WP-C4b）：中性預設、失敗退化、風場接進來。

## 開工前查證推翻了規格的兩個前提

SPEC_V2 說「天氣是 session 啟動單一快照」並把本卡標成 **golden：重錄**。實際上：
1. 插件的 RPC 早就是 tick-aware（`GetWeatherRequest.sim_tick`），是 core 只呼叫一次且永遠傳 0。
2. **風場早就在契約裡**（`WeatherCell.wind_ms`/`wind_dir_deg`），是 core 的
   `_cell_from_proto` 只讀 `effects` 把它丟掉了。
3. 刷新間隔預設 0 ＝永不刷新 ＝既有行為 → **golden 不必重錄**。
"""

from __future__ import annotations

from app.engine.weather_wiring import DEFAULT_REFRESH_TICKS, WeatherCache
from app.weather import CellEffects, WeatherState


def _state(tag: float) -> WeatherState:
    return WeatherState({"cell": CellEffects(mobility_modifier=tag)})


def test_the_default_never_refreshes() -> None:
    """**中性預設**：0 ＝整局沿用啟動快照＝既有行為，一個位元都不差。"""
    assert DEFAULT_REFRESH_TICKS == 0
    calls: list[int] = []
    cache = WeatherCache(lambda t: (calls.append(t), _state(t))[1], initial=_state(0.0))
    assert cache.refreshes is False
    for tick in range(50):
        cache.at(tick)
    assert calls == [], "預設不該打任何一次 RPC"


def test_refreshing_asks_the_plugin_with_the_real_tick() -> None:
    """插件的 RPC 一直收 tick——缺的從來不是能力，是「每隔 N tick 再問一次」。"""
    calls: list[int] = []
    cache = WeatherCache(lambda t: (calls.append(t), _state(t))[1], refresh_ticks=10)
    for tick in range(25):
        cache.at(tick)
    assert calls == [0, 10, 20]


def test_a_failed_refresh_keeps_the_last_snapshot_not_clear_skies() -> None:
    """天氣服務抖一下就讓全場忽然放晴，比慢一拍嚴重得多
    （同 `has_los` 服務中斷不致盲的退化紀律）。"""

    def flaky(tick: int) -> WeatherState:
        if tick >= 10:
            raise RuntimeError("weather down")
        return _state(0.5)

    cache = WeatherCache(flaky, refresh_ticks=10)
    assert cache.at(0) is not None
    before = cache.at(0)
    assert cache.at(10) is before, "取不到就該沿用上一份"


def test_a_failed_refresh_does_not_retry_every_tick() -> None:
    """**取不到也要更新時間戳**，否則每個 tick 都重試一次失敗的 RPC，
    把 tick 預算耗在一個已知不可用的服務上。"""
    calls: list[int] = []

    def always_fails(tick: int) -> WeatherState:
        calls.append(tick)
        raise RuntimeError("down")

    cache = WeatherCache(always_fails, refresh_ticks=5)
    for tick in range(20):
        cache.at(tick)
    assert calls == [0, 5, 10, 15]


def test_wind_is_neutral_by_default_and_carried_when_present() -> None:
    """風場**契約裡本來就有**，是 core 沒讀。0＝無風（中性），既有行為不變。"""
    assert CellEffects().wind_ms == 0.0
    assert CellEffects().wind_dir_deg == 0.0
    windy = CellEffects(wind_ms=7.5, wind_dir_deg=225.0)
    assert (windy.wind_ms, windy.wind_dir_deg) == (7.5, 225.0)


def test_the_proto_cell_carries_wind_so_no_contract_change_was_needed() -> None:
    """釘住那件查證：**不需要改契約**。改的是 core 的讀法。"""
    from app.plugins import weather_client

    fields = {f.name for f in weather_client.weather_pb2.WeatherCell.DESCRIPTOR.fields}
    assert {"wind_ms", "wind_dir_deg"} <= fields
