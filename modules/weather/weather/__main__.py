"""Weather 插件服務進入點（O5.1 SYNTHETIC / O5.2 LIVE）。

    # SYNTHETIC
    MATSO_WEATHER_MODE=SYNTHETIC MATSO_WEATHER_SCRIPT_PATH=data/w.json uv run python -m weather
    # LIVE（CWA）
    MATSO_WEATHER_MODE=LIVE MATSO_WEATHER_CWA_API_KEY=xxx uv run python -m weather

未設定來源時仍啟動（health=DEGRADED），由 Core 健檢決定預案。
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import threading
import time

from matso_sdk import serve

from weather.config import WeatherSettings
from weather.cwa import CwaHttpSource
from weather.live import LiveWeather, run_refresh_loop
from weather.plugin import WeatherPlugin
from weather.provider import WeatherProvider
from weather.synthetic import SyntheticWeather


def _build_provider(
    settings: WeatherSettings,
) -> tuple[WeatherProvider | None, threading.Event | None]:
    """依 mode 建 provider。LIVE 另回背景拉取的 stop event（供關閉）。"""
    if settings.mode.upper() == "LIVE" and settings.live_configured():
        cells: list[str] = []
        if settings.live_cells_path.is_file():
            cells = list(json.loads(settings.live_cells_path.read_text(encoding="utf-8")))
        live = LiveWeather(
            CwaHttpSource(settings.cwa_api_key, settings.cwa_url), cells, time.monotonic
        )
        live.refresh()  # 首次拉取
        stop = threading.Event()
        threading.Thread(
            target=run_refresh_loop, args=(live, settings.fetch_interval_s, stop), daemon=True
        ).start()
        return live, stop
    if settings.script_available():
        script = json.loads(settings.script_path.read_text(encoding="utf-8"))
        return SyntheticWeather.from_script(script), None
    return None, None


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="MATSO Weather 插件 gRPC 服務")
    parser.add_argument(
        "--port", type=int, default=int(os.environ.get("WEATHER_GRPC_PORT", "50052"))
    )
    parser.add_argument("--host", default="0.0.0.0")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = WeatherSettings()
    provider, _stop = _build_provider(settings)
    plugin = WeatherPlugin(provider)
    state, detail = plugin.health()
    logging.getLogger("weather").info(
        "啟動（mode=%s）健康狀態：%s（%s）", settings.mode, state, detail
    )
    serve(plugin, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
