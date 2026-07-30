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


# ---- 過期告警：`WeatherState.stale` 過去除定義處外全 repo 零讀取端 ----


def test_a_stale_snapshot_is_reported_once() -> None:
    """契約要求「stale > 30 分鐘 Core 需告警」，插件也照實回報——core 就是沒人讀。

    後果：來源斷線半小時，白軍畫面上什麼提示都沒有，
    命中率/機動修正繼續套一份不知道多舊的天氣。
    """
    from app.engine.weather_wiring import WeatherCache
    from app.weather import WeatherState

    fresh = WeatherState({}, stale=False)
    stale = WeatherState({}, stale=True)
    seq = [stale, stale, fresh]
    cache = WeatherCache(lambda _t: seq.pop(0), refresh_ticks=1, initial=fresh)

    cache.at(1)
    assert cache.take_stale_change() is True  # 轉為過期 → 報一次
    cache.at(2)
    assert cache.take_stale_change() is None  # 還是過期 → 不重複報（別灌爆 feed）
    cache.at(3)
    assert cache.take_stale_change() is False  # 恢復 → 也要報，好清掉告警


def test_a_never_stale_session_never_reports() -> None:
    """既有局（天氣正常/無天氣服務）一則告警都不該冒出來。"""
    from app.engine.weather_wiring import WeatherCache

    cache = WeatherCache(lambda _t: None, refresh_ticks=0)

    assert cache.take_stale_change() is None
    assert cache.stale is False


# ---- MSEL WEATHER_OVERRIDE（過去只落一筆 UNSUPPORTED，理由指向一張已完成的卡） ----


def test_an_override_beats_the_plugin() -> None:
    """統裁說「現在起下暴雨」就不該被下一次刷新蓋回去。"""
    from app.engine.weather_wiring import WeatherCache
    from app.weather import CellEffects, WeatherState

    plugin = WeatherState.uniform(CellEffects(mobility_modifier=1.0))
    storm = WeatherState.uniform(CellEffects(mobility_modifier=0.3))
    cache = WeatherCache(lambda _t: plugin, refresh_ticks=1, initial=plugin)

    cache.set_override(storm)
    assert cache.at(5).effects_at("x").mobility_modifier == 0.3  # type: ignore[union-attr]
    assert cache.at(6).effects_at("x").mobility_modifier == 0.3  # type: ignore[union-attr] # 刷新不蓋掉


def test_an_override_expires_on_its_own() -> None:
    from app.engine.weather_wiring import WeatherCache
    from app.weather import CellEffects, WeatherState

    plugin = WeatherState.uniform(CellEffects(mobility_modifier=1.0))
    storm = WeatherState.uniform(CellEffects(mobility_modifier=0.3))
    cache = WeatherCache(lambda _t: plugin, refresh_ticks=1, initial=plugin)

    cache.set_override(storm, until_tick=10)

    assert cache.at(10).effects_at("x").mobility_modifier == 0.3  # type: ignore[union-attr]
    assert cache.at(11).effects_at("x").mobility_modifier == 1.0  # type: ignore[union-attr]


def test_clearing_an_override_returns_to_the_plugin() -> None:
    """`effects` 缺席＝解除，不是「套一份晴天」——後者會讓取消與注入晴天分不開。"""
    from app.engine.weather_wiring import WeatherCache
    from app.weather import CellEffects, WeatherState

    plugin = WeatherState.uniform(CellEffects(mobility_modifier=0.8))
    cache = WeatherCache(lambda _t: plugin, refresh_ticks=1, initial=plugin)

    cache.set_override(WeatherState.uniform(CellEffects(mobility_modifier=0.3)))
    cache.set_override(None)

    assert cache.at(5).effects_at("x").mobility_modifier == 0.8  # type: ignore[union-attr]
