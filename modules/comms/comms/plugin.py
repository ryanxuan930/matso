"""CommsPlugin — 把 CommsService 套進 MatsoPlugin（SPEC §6/§16.3/§17）。

Comms 為**純確定性**服務（無外部資料來源 / 無硬碟依賴）：地形遮蔽 + 天氣衰減由呼叫端
（Core）攜入。故健康狀態恆 HEALTHY（服務起得來即代表可解算）。
"""

from __future__ import annotations

import grpc
from matso_sdk import HealthState, Manifest, MatsoPlugin, PluginKind
from matso_sdk._generated import comms_pb2_grpc

from comms.service import CommsService

CONTRACT_VERSION = "0.1.0"
_CAPABILITIES = ("ComputeLinks",)


class CommsPlugin(MatsoPlugin):
    def __init__(self) -> None:
        self._service = CommsService()

    @property
    def manifest(self) -> Manifest:
        return Manifest(
            name="comms",
            kind=PluginKind.COMMS,
            contract_version=CONTRACT_VERSION,
            capabilities=_CAPABILITIES,
        )

    def register_domain_services(self, server: grpc.Server) -> None:
        comms_pb2_grpc.add_CommsServiceServicer_to_server(self._service, server)

    def health(self) -> tuple[HealthState, str]:
        return HealthState.HEALTHY, "comms 解算就緒（純確定性）"
