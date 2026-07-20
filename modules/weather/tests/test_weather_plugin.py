"""Weather 插件整合測試（O5.1）：harness 起 gRPC server，GetWeather 回有效 payload。"""

from __future__ import annotations

from matso_sdk import HealthState, from_proto, run_plugin
from matso_sdk._generated import plugin_base_pb2, weather_pb2, weather_pb2_grpc
from weather.plugin import WeatherPlugin
from weather.synthetic import SyntheticWeather

_SCRIPT = {
    "cells": {
        "cell_a": {
            "keyframes": [
                {"tick": 0, "precipitation_mmhr": 0, "wind_ms": 2},
                {"tick": 100, "precipitation_mmhr": 20, "wind_ms": 14},
            ]
        }
    }
}


def _plugin() -> WeatherPlugin:
    return WeatherPlugin(SyntheticWeather.from_script(_SCRIPT))


def test_manifest_and_health() -> None:
    with run_plugin(_plugin()) as h:
        m = h.manifest()
        assert m.name == "weather"
        assert m.kind == "WEATHER"
        resp = h.base_stub().HealthCheck(plugin_base_pb2.HealthCheckRequest())
        assert from_proto(resp.state) is HealthState.HEALTHY


def test_get_weather_returns_interpolated_payload() -> None:
    with run_plugin(_plugin()) as h:
        stub = weather_pb2_grpc.WeatherServiceStub(h.channel)
        resp = stub.GetWeather(weather_pb2.GetWeatherRequest(sim_tick=50))
        assert resp.issued_at_sim_tick == 50
        assert resp.mode == weather_pb2.WEATHER_MODE_SYNTHETIC
        assert resp.stale is False
        assert len(resp.cells) == 1
        cell = resp.cells[0]
        assert cell.h3_index == "cell_a"
        assert cell.precipitation_mmhr == 10.0  # 0→20 中點
        assert cell.wind_ms == 8.0  # 2→14 中點
        assert cell.effects.mobility_modifier < 1.0  # 有雨 → 機動略降
        assert cell.effects.uav_operability is True  # wind 8 < 12、precip 10 < 25 → 可飛


def test_degraded_without_engine() -> None:
    from matso_sdk import HealthState

    plugin = WeatherPlugin(None)  # 無腳本 → DEGRADED，不註冊 WeatherService
    state, _ = plugin.health()
    assert state is HealthState.DEGRADED
    with run_plugin(plugin) as h:
        m = h.manifest()
        assert m.name == "weather"  # 基礎服務仍在


def test_live_stale_health_degraded() -> None:
    from matso_sdk import HealthState
    from weather.live import CwaFetchError, LiveWeather

    class _Fail:
        def fetch(self) -> list:  # type: ignore[type-arg]
            raise CwaFetchError("no net")

    live = LiveWeather(_Fail(), [], lambda: 0.0)
    live.refresh()  # 失敗 → stale
    plugin = WeatherPlugin(live)
    state, _ = plugin.health()
    assert state is HealthState.DEGRADED  # SPEC §16.3：weather stale → DEGRADED
