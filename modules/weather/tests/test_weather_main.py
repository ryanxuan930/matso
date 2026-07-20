"""Weather 進入點 + config（O5.1）：serve 以 mock 取代，驗證有/無腳本的組裝。"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from weather.config import WeatherSettings

from weather import __main__

_SCRIPT = {"cells": {"c": {"keyframes": [{"tick": 0, "precipitation_mmhr": 3}]}}}


def test_main_with_script_serves(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    script = tmp_path / "w.json"
    script.write_text(json.dumps(_SCRIPT), encoding="utf-8")
    monkeypatch.setenv("MATSO_WEATHER_SCRIPT_PATH", str(script))
    with patch.object(__main__, "serve") as serve_mock:
        __main__.main(["--port", "0"])
    serve_mock.assert_called_once()


def test_main_without_script_still_serves(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("MATSO_WEATHER_SCRIPT_PATH", str(tmp_path / "nope.json"))
    with patch.object(__main__, "serve") as serve_mock:
        __main__.main(["--port", "0"])  # 無腳本 → DEGRADED，但仍 serve
    serve_mock.assert_called_once()


def test_config_script_available(tmp_path: Path) -> None:
    present = tmp_path / "s.json"
    present.write_text("{}", encoding="utf-8")
    assert WeatherSettings(script_path=present).script_available()
    assert not WeatherSettings(script_path=tmp_path / "absent.json").script_available()


def test_live_mode_builds_live_provider(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import h3
    from weather.live import LiveWeather, StationObservation
    from weather.payload import RawWeather, WeatherMode

    from weather import __main__

    cell = h3.latlng_to_cell(23.75, 121.25, 8)
    (tmp_path / "cells.json").write_text(json.dumps([cell]), encoding="utf-8")
    monkeypatch.setenv("MATSO_WEATHER_MODE", "LIVE")
    monkeypatch.setenv("MATSO_WEATHER_CWA_API_KEY", "testkey")
    monkeypatch.setenv("MATSO_WEATHER_LIVE_CELLS_PATH", str(tmp_path / "cells.json"))
    # 假 CWA：不打真網路
    monkeypatch.setattr(
        __main__.CwaHttpSource,
        "fetch",
        lambda self: [StationObservation(23.75, 121.25, RawWeather(precipitation_mmhr=5))],
    )
    with patch.object(__main__, "serve") as serve_mock, patch.object(__main__.threading, "Thread"):
        __main__.main(["--port", "0"])
    serve_mock.assert_called_once()
    plugin = serve_mock.call_args[0][0]
    assert isinstance(plugin._provider, LiveWeather)
    assert plugin._provider.payload_at(0).mode is WeatherMode.LIVE
