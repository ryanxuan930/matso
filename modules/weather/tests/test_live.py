"""LIVE 天氣（O5.2）：格網化 + stale 降級狀態機（斷網→stale、恢復→LIVE）+ 30min 告警。"""

from __future__ import annotations

import h3
import pytest
from weather.live import CwaFetchError, LiveWeather, StationObservation
from weather.payload import RawWeather, WeatherMode

_CELL_A = h3.latlng_to_cell(23.75, 121.25, 8)
_CELL_B = h3.latlng_to_cell(24.50, 121.50, 8)
_STATION_A = StationObservation(23.75, 121.25, RawWeather(precipitation_mmhr=10.0))
_STATION_B = StationObservation(24.50, 121.50, RawWeather(precipitation_mmhr=2.0))


class _FakeSource:
    def __init__(self, observations: list[StationObservation], fail: bool = False) -> None:
        self._obs = observations
        self.fail = fail
        self.calls = 0

    def fetch(self) -> list[StationObservation]:
        self.calls += 1
        if self.fail:
            raise CwaFetchError("boom")
        return self._obs


class _Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def _live(source: _FakeSource, clock: _Clock | None = None) -> LiveWeather:
    return LiveWeather(source, [_CELL_A, _CELL_B], clock or _Clock())


def test_starts_stale_before_first_fetch() -> None:
    live = _live(_FakeSource([_STATION_A, _STATION_B]))
    assert live.is_stale() is True  # 尚無資料
    assert live.payload_at(0).stale is True


def test_refresh_success_goes_live_and_gridifies() -> None:
    live = _live(_FakeSource([_STATION_A, _STATION_B]))
    assert live.refresh() is True
    assert live.is_stale() is False
    payload = live.payload_at(7)
    assert payload.mode is WeatherMode.LIVE
    assert payload.stale is False
    assert payload.issued_at_sim_tick == 7
    by_cell = {c.h3_index: c for c in payload.cells}
    # 最近測站指派：cell_a→station_a(10)、cell_b→station_b(2)
    assert by_cell[_CELL_A].raw.precipitation_mmhr == 10.0
    assert by_cell[_CELL_B].raw.precipitation_mmhr == 2.0


def test_fetch_failure_degrades_to_stale_keeping_last_values() -> None:
    source = _FakeSource([_STATION_A, _STATION_B])
    live = _live(source)
    live.refresh()  # 成功 → 有值
    source.fail = True
    assert live.refresh() is False  # 斷網
    assert live.is_stale() is True
    payload = live.payload_at(0)
    assert payload.stale is True  # stale=true
    # 最後有效值仍在
    by_cell = {c.h3_index: c for c in payload.cells}
    assert by_cell[_CELL_A].raw.precipitation_mmhr == 10.0


def test_recovery_returns_to_live() -> None:
    source = _FakeSource([_STATION_A, _STATION_B], fail=True)
    live = _live(source)
    assert live.refresh() is False and live.is_stale()  # 斷網
    source.fail = False
    assert live.refresh() is True  # 恢復
    assert live.is_stale() is False
    assert live.payload_at(0).stale is False  # 自動回 LIVE


def test_stale_alert_after_30min() -> None:
    clock = _Clock()
    source = _FakeSource([_STATION_A])
    live = _live(source, clock)
    live.refresh()  # 成功於 t=0
    source.fail = True
    clock.t = 100.0
    live.refresh()  # 斷網（stale since 最後成功 t=0）
    assert live.stale_alert() is False  # 才 100s
    clock.t = 30 * 60  # 30 分鐘
    assert live.stale_duration_s() == pytest.approx(30 * 60)
    assert live.stale_alert() is True  # 達告警門檻


def test_gridify_empty_observations_uses_defaults() -> None:
    # 全無測站（例如首次成功但空資料）→ 各 cell 預設值、非 stale
    live = _live(_FakeSource([]))
    assert live.refresh() is True
    assert live.is_stale() is False
    assert live.payload_at(0).cells[0].raw.precipitation_mmhr == RawWeather().precipitation_mmhr


def test_run_refresh_loop_runs_then_stops() -> None:
    import threading

    from weather.live import run_refresh_loop

    stop = threading.Event()
    source = _FakeSource([_STATION_A])
    live = _live(source)
    orig = live.refresh

    def _refresh_then_stop() -> bool:
        r = orig()
        stop.set()  # 第一次拉取後即停
        return r

    live.refresh = _refresh_then_stop  # type: ignore[method-assign]
    run_refresh_loop(live, 0.01, stop)
    assert source.calls == 1  # 恰跑一輪
