"""Core 端插件客戶端（gRPC）。Terrain 為 Phase 1 硬依賴（SPEC §16.3/§17）。"""

from app.plugins.comms_client import CommsClient
from app.plugins.manifest import (
    CORE_CONTRACT_MAJOR,
    TERRAIN_REQUIRED_CAPABILITIES,
    PluginContractMismatchError,
    PluginHandshakeError,
    PluginManifest,
    PluginUnreachableError,
    fetch_manifest,
    negotiate_contract,
)
from app.plugins.terrain_client import (
    BreakerState,
    CircuitBreaker,
    HealthMonitor,
    SessionController,
    TerrainClient,
)
from app.plugins.weather_client import WeatherClient

__all__ = [
    "CORE_CONTRACT_MAJOR",
    "TERRAIN_REQUIRED_CAPABILITIES",
    "BreakerState",
    "CircuitBreaker",
    "CommsClient",
    "HealthMonitor",
    "PluginContractMismatchError",
    "PluginHandshakeError",
    "PluginManifest",
    "PluginUnreachableError",
    "SessionController",
    "TerrainClient",
    "WeatherClient",
    "fetch_manifest",
    "negotiate_contract",
]
