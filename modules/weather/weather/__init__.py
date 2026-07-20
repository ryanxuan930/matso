"""MATSO Weather Module（M5）——SYNTHETIC 關鍵影格插值 → 格網化效果係數（SPEC §5）。"""

from weather.config import WeatherSettings
from weather.cwa import CwaHttpSource, parse_stations
from weather.effects import derive_effects
from weather.live import (
    CwaFetchError,
    CwaSource,
    LiveWeather,
    StationObservation,
    run_refresh_loop,
)
from weather.payload import (
    RawWeather,
    WeatherCell,
    WeatherEffects,
    WeatherMode,
    WeatherPayload,
)
from weather.plugin import WeatherPlugin
from weather.provider import WeatherProvider
from weather.service import WeatherService
from weather.synthetic import SyntheticWeather

__version__ = "0.1.0"

__all__ = [
    "CwaFetchError",
    "CwaHttpSource",
    "CwaSource",
    "LiveWeather",
    "RawWeather",
    "StationObservation",
    "SyntheticWeather",
    "WeatherCell",
    "WeatherEffects",
    "WeatherMode",
    "WeatherPayload",
    "WeatherPlugin",
    "WeatherProvider",
    "WeatherService",
    "WeatherSettings",
    "__version__",
    "derive_effects",
    "parse_stations",
    "run_refresh_loop",
]
