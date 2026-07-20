"""TerrainPlugin — 把 TerrainService 套進 MatsoPlugin（O2.5, SPEC §16.3/§17）。

健康狀態依資源可用性（外接硬碟 fallback）：
- DTED + hex 快取都在 → HEALTHY
- 只缺其一 → DEGRADED（部分服務可用：缺 DTED 仍可規劃路徑；缺快取仍可查高程/LOS）
- 兩者皆缺 → DOWN（Core 端據此 PAUSE session，因 terrain 是物理預檢硬依賴）
"""

from __future__ import annotations

import grpc
from matso_sdk import HealthState, Manifest, MatsoPlugin, PluginKind
from matso_sdk._generated import terrain_pb2_grpc

from terrain.config import TerrainSettings
from terrain.dted import DtedMap
from terrain.hexgrid import HexGridCache
from terrain.service import TerrainService

CONTRACT_VERSION = "0.1.0"
_CAPABILITIES = ("GetElevation", "CheckLos", "GetPath", "GetCellBatch", "GetViewshed")


class TerrainPlugin(MatsoPlugin):
    def __init__(
        self, dted: DtedMap | None, cache: HexGridCache | None, viewshed_resolution: int = 8
    ) -> None:
        self._dted = dted
        self._cache = cache
        self._service = TerrainService(dted, cache, viewshed_resolution)

    @property
    def manifest(self) -> Manifest:
        return Manifest(
            name="terrain",
            kind=PluginKind.TERRAIN,
            contract_version=CONTRACT_VERSION,
            capabilities=_CAPABILITIES,
        )

    def register_domain_services(self, server: grpc.Server) -> None:
        terrain_pb2_grpc.add_TerrainServiceServicer_to_server(self._service, server)

    def health(self) -> tuple[HealthState, str]:
        has_dted = self._dted is not None
        has_cache = self._cache is not None
        if has_dted and has_cache:
            return HealthState.HEALTHY, "DTED + hex 快取就緒"
        if not has_dted and not has_cache:
            return HealthState.DOWN, "DTED 與 hex 快取皆缺（外接硬碟未掛載且未預計算）"
        missing = "DTED" if not has_dted else "hex 快取"
        return HealthState.DEGRADED, f"缺 {missing}，僅部分服務可用"


def build_from_settings(
    settings: TerrainSettings | None = None, resolution: int = 8
) -> TerrainPlugin:
    """由 TerrainSettings 建插件：DTED 缺 → None（降級）；hex 快取存在才載入。

    外接硬碟未掛載時 DtedMap.try_open_default 回 None，插件仍能以快取提供路徑/cell 服務。
    """
    settings = settings or TerrainSettings()
    dted = DtedMap.try_open_default(settings)
    cache_path = settings.hex_cache_dir / f"res{resolution}.parquet"
    cache = HexGridCache.open(cache_path) if cache_path.is_file() else None
    return TerrainPlugin(dted, cache, resolution)
