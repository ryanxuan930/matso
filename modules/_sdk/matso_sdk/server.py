"""gRPC server 啟動樣板 + graceful shutdown（SPEC §17 隔離：每插件獨立 process）。"""

from __future__ import annotations

import logging
import signal
import threading
from concurrent import futures

import grpc

from matso_sdk._base_servicer import PluginBaseServicer
from matso_sdk._generated import plugin_base_pb2_grpc
from matso_sdk.plugin import MatsoPlugin

_LOG = logging.getLogger("matso_sdk.server")
_DEFAULT_MAX_WORKERS = 8
_DEFAULT_GRACE_S = 5.0


def build_server(
    plugin: MatsoPlugin,
    host: str = "0.0.0.0",
    port: int = 50051,
    max_workers: int = _DEFAULT_MAX_WORKERS,
) -> tuple[grpc.Server, int]:
    """建 gRPC server，掛上 PluginBaseService + 插件領域 servicer。回 (server, bound_port)。

    port=0 → 由 OS 指派臨時埠（測試 harness 用）；回傳實際綁定的埠。
    """
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=max_workers))
    plugin_base_pb2_grpc.add_PluginBaseServiceServicer_to_server(PluginBaseServicer(plugin), server)
    plugin.register_domain_services(server)
    bound = server.add_insecure_port(f"{host}:{port}")
    if bound == 0:
        raise RuntimeError(f"gRPC server 無法綁定 {host}:{port}")
    return server, bound


def serve(
    plugin: MatsoPlugin,
    host: str = "0.0.0.0",
    port: int = 50051,
    grace_s: float = _DEFAULT_GRACE_S,
) -> None:
    """啟動並阻塞至收到 SIGTERM/SIGINT，然後 graceful shutdown（僅可於主執行緒呼叫）。"""
    server, bound = build_server(plugin, host, port)
    server.start()
    m = plugin.manifest
    _LOG.info("plugin '%s' (%s v%s) 監聽 %s:%d", m.name, m.kind, m.contract_version, host, bound)

    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    stop.wait()

    _LOG.info("plugin '%s' 收到停止訊號，graceful shutdown（grace=%.1fs）", m.name, grace_s)
    server.stop(grace_s).wait()
