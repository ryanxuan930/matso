"""WeatherPlugin — 把 WeatherService 套進 MatsoPlugin（SPEC §5/§16.3/§17）。

支援 SYNTHETIC（O5.1 腳本插值）與 LIVE（O5.2 CWA 拉取），皆經 WeatherProvider 介面。
健康狀態（SPEC §16.3「weather 進入 stale 模式 → DEGRADED」）：
- 無 provider（無腳本/未設定）→ DEGRADED（無天氣資料）
- provider stale（LIVE 拉取失敗）→ DEGRADED（降級為最後有效值）
- 否則 → HEALTHY
"""

from __future__ import annotations

import grpc
from matso_sdk import HealthState, Manifest, MatsoPlugin, PluginKind
from matso_sdk._generated import weather_pb2_grpc

from weather.provider import WeatherProvider
from weather.service import WeatherService

CONTRACT_VERSION = "0.1.0"
_CAPABILITIES = ("GetWeather", "SYNTHETIC", "LIVE")


class WeatherPlugin(MatsoPlugin):
    def __init__(self, provider: WeatherProvider | None) -> None:
        self._provider = provider
        self._service = WeatherService(provider) if provider is not None else None

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
        if self._provider is None:
            return HealthState.DEGRADED, "無天氣來源（無腳本 / CWA 未設定）"
        if self._provider.is_stale():
            return HealthState.DEGRADED, "天氣來源 stale（LIVE 拉取失敗，用最後有效值）"
        return HealthState.HEALTHY, "天氣來源就緒"
