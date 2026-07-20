"""插件整合測試 harness（SPEC §17）——in-process 起真 gRPC server，回 client channel。

用法：
    with run_plugin(MyPlugin()) as h:
        stub = plugin_base_pb2_grpc.PluginBaseServiceStub(h.channel)
        resp = stub.GetManifest(plugin_base_pb2.GetManifestRequest())
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

import grpc

from matso_sdk._generated import plugin_base_pb2, plugin_base_pb2_grpc
from matso_sdk.plugin import MatsoPlugin
from matso_sdk.server import build_server


@dataclass(frozen=True, slots=True)
class PluginHarness:
    channel: grpc.Channel
    port: int

    @property
    def target(self) -> str:
        return f"127.0.0.1:{self.port}"

    def base_stub(self) -> plugin_base_pb2_grpc.PluginBaseServiceStub:
        return plugin_base_pb2_grpc.PluginBaseServiceStub(self.channel)

    def manifest(self) -> plugin_base_pb2.Manifest:
        return self.base_stub().GetManifest(plugin_base_pb2.GetManifestRequest()).manifest


@contextmanager
def run_plugin(
    plugin: MatsoPlugin, host: str = "127.0.0.1", port: int = 0
) -> Iterator[PluginHarness]:
    """啟動插件於臨時埠（port=0），yield 連好的 channel；離開時 graceful 關閉。"""
    server, bound = build_server(plugin, host, port)
    server.start()
    channel = grpc.insecure_channel(f"{host}:{bound}")
    try:
        grpc.channel_ready_future(channel).result(timeout=5.0)
        yield PluginHarness(channel=channel, port=bound)
    finally:
        channel.close()
        server.stop(0).wait()
