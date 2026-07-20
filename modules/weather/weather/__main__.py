"""Weather 插件服務進入點（O5.1）。

    MATSO_WEATHER_SCRIPT_PATH=data/synthetic_weather.json \
        uv run python -m weather [--port 50052]

無腳本時仍啟動（health=DEGRADED），由 Core 健檢決定預案。
"""

from __future__ import annotations

import argparse
import json
import logging
import os

from matso_sdk import serve

from weather.config import WeatherSettings
from weather.plugin import WeatherPlugin
from weather.synthetic import SyntheticWeather


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="MATSO Weather 插件 gRPC 服務")
    parser.add_argument(
        "--port", type=int, default=int(os.environ.get("WEATHER_GRPC_PORT", "50052"))
    )
    parser.add_argument("--host", default="0.0.0.0")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = WeatherSettings()
    engine: SyntheticWeather | None = None
    if settings.script_available():
        script = json.loads(settings.script_path.read_text(encoding="utf-8"))
        engine = SyntheticWeather.from_script(script)
    plugin = WeatherPlugin(engine)
    state, detail = plugin.health()
    logging.getLogger("weather").info("啟動健康狀態：%s（%s）", state, detail)
    serve(plugin, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
