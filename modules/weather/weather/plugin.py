"""WeatherPlugin — 把 WeatherService 套進 MatsoPlugin（O5.1, SPEC §5/§17）。

O5.1 只做 SYNTHETIC 模式（腳本關鍵影格插值）。LIVE（CWA）於 O5.2、REPLAY 於 AAR。
無腳本時 health=DEGRADED（服務仍在，但無天氣資料）。
"""

from __future__ import annotations

import grpc
from matso_sdk import HealthState, Manifest, MatsoPlugin, PluginKind
from matso_sdk._generated import weather_pb2_grpc

from weather.service import WeatherService
from weather.synthetic import SyntheticWeather

CONTRACT_VERSION = "0.1.0"
_CAPABILITIES = ("GetWeather", "SYNTHETIC")


class WeatherPlugin(MatsoPlugin):
    def __init__(self, engine: SyntheticWeather | None) -> None:
        self._engine = engine
        self._service = WeatherService(engine) if engine is not None else None

    @property
    def manifest(self) -> Manifest:
        return Manifest(
            name="weather",
            kind=PluginKind.WEATHER,
            contract_version=CONTRACT_VERSION,
            capabilities=_CAPABILITIES,
        )

    def register_domain_services(self, server: grpc.Server) -> None:
        if self._service is not None:
            weather_pb2_grpc.add_WeatherServiceServicer_to_server(self._service, server)

    def health(self) -> tuple[HealthState, str]:
        if self._engine is None:
            return HealthState.DEGRADED, "無 SYNTHETIC 腳本，天氣資料不可用"
        return HealthState.HEALTHY, "SYNTHETIC 就緒"
