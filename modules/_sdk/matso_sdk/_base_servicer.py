"""PluginBaseService 的通用實作——所有插件共用（SPEC §16.3）。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from matso_sdk._generated import plugin_base_pb2, plugin_base_pb2_grpc
from matso_sdk.health import to_proto

if TYPE_CHECKING:
    import grpc

    from matso_sdk.plugin import MatsoPlugin


class PluginBaseServicer(plugin_base_pb2_grpc.PluginBaseServiceServicer):
    """把 MatsoPlugin 的 manifest/health/configure 暴露為 gRPC 基礎服務。"""

    def __init__(self, plugin: MatsoPlugin) -> None:
        self._plugin = plugin

    def GetManifest(  # noqa: N802 (gRPC 產生的方法名)
        self, request: plugin_base_pb2.GetManifestRequest, context: grpc.ServicerContext
    ) -> plugin_base_pb2.GetManifestResponse:
        m = self._plugin.manifest
        return plugin_base_pb2.GetManifestResponse(
            manifest=plugin_base_pb2.Manifest(
                name=m.name,
                kind=m.kind.value,
                contract_version=m.contract_version,
                capabilities=list(m.capabilities),
            )
        )

    def HealthCheck(  # noqa: N802
        self, request: plugin_base_pb2.HealthCheckRequest, context: grpc.ServicerContext
    ) -> plugin_base_pb2.HealthCheckResponse:
        state, detail = self._plugin.health()
        return plugin_base_pb2.HealthCheckResponse(state=to_proto(state), detail=detail)

    def Configure(  # noqa: N802
        self, request: plugin_base_pb2.ConfigureRequest, context: grpc.ServicerContext
    ) -> plugin_base_pb2.ConfigureResponse:
        ok, message = self._plugin.configure(request.config_json)
        return plugin_base_pb2.ConfigureResponse(ok=ok, message=message)
