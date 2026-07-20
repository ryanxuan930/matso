"""TerrainClient + HealthMonitor 對真 in-process gRPC 的整合測試（O2.5）。

用 matso_sdk（core 僅依賴此，不依賴 matso-terrain）建一個假 terrain 插件，驗證：
- 領域 RPC roundtrip；
- server 掛掉 → 斷路器開啟（快速失敗）；
- **server 掛掉 → 健檢連續失敗 → 標記 DOWN → 觸發 session PAUSE 預案**（O2.5 驗收）。
"""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import MagicMock

import grpc
import pytest
from matso_sdk import HealthState, Manifest, MatsoPlugin, PluginKind, build_server
from matso_sdk._generated import terrain_pb2, terrain_pb2_grpc

from app.errors import TerrainUnavailableError
from app.plugins.terrain_client import CircuitBreaker, HealthMonitor, TerrainClient

_ELEV = 123.5


class _FakeTerrainServicer(terrain_pb2_grpc.TerrainServiceServicer):
    def GetElevation(  # noqa: N802
        self, request: terrain_pb2.GetElevationRequest, context: grpc.ServicerContext
    ) -> terrain_pb2.GetElevationResponse:
        return terrain_pb2.GetElevationResponse(elevation_m=_ELEV, water=False)


class _FakeTerrainPlugin(MatsoPlugin):
    def __init__(self, state: HealthState = HealthState.HEALTHY) -> None:
        self._state = state

    @property
    def manifest(self) -> Manifest:
        return Manifest(name="faketerrain", kind=PluginKind.TERRAIN, contract_version="0.1.0")

    def register_domain_services(self, server: grpc.Server) -> None:
        terrain_pb2_grpc.add_TerrainServiceServicer_to_server(_FakeTerrainServicer(), server)

    def health(self) -> tuple[HealthState, str]:
        return self._state, ""


@pytest.fixture
def live_server() -> Iterator[tuple[grpc.Server, grpc.Channel]]:
    """啟動假 terrain 插件於臨時埠，yield (server, channel)；測試可自行 stop 模擬崩潰。"""
    server, port = build_server(_FakeTerrainPlugin(), host="127.0.0.1", port=0)
    server.start()
    channel = grpc.insecure_channel(f"127.0.0.1:{port}")
    grpc.channel_ready_future(channel).result(timeout=5.0)
    try:
        yield server, channel
    finally:
        channel.close()
        server.stop(0).wait()


def test_get_elevation_roundtrip(live_server: tuple[grpc.Server, grpc.Channel]) -> None:
    _, channel = live_server
    client = TerrainClient(channel)
    resp = client.get_elevation(23.75, 121.25)
    assert resp.elevation_m == pytest.approx(_ELEV)


def test_breaker_opens_after_server_death(
    live_server: tuple[grpc.Server, grpc.Channel],
) -> None:
    server, channel = live_server
    client = TerrainClient(channel, deadline_s=0.3, breaker=CircuitBreaker(failure_threshold=3))
    assert client.get_elevation(23.75, 121.25).elevation_m == pytest.approx(_ELEV)

    server.stop(0).wait()  # 模擬 terrain 容器被殺
    for _ in range(3):
        with pytest.raises(TerrainUnavailableError):
            client.get_elevation(23.75, 121.25)
    # 斷路器現在應開啟 → 下一次「快速失敗」（不再實際發 RPC）
    with pytest.raises(TerrainUnavailableError, match="斷路器"):
        client.get_elevation(23.75, 121.25)


def test_monitor_marks_down_and_pauses_on_death(
    live_server: tuple[grpc.Server, grpc.Channel],
) -> None:
    server, channel = live_server
    controller = MagicMock()
    monitor = HealthMonitor(channel, controller, failure_threshold=3, deadline_s=0.5)

    monitor.evaluate()  # server 健在
    assert not monitor.is_down
    controller.pause_all.assert_not_called()

    server.stop(0).wait()  # 殺掉 terrain
    monitor.evaluate()
    monitor.evaluate()
    assert not monitor.is_down  # 才 2 次
    monitor.evaluate()  # 第 3 次連續失敗
    assert monitor.is_down
    controller.pause_all.assert_called_once()


def test_monitor_thread_lifecycle(live_server: tuple[grpc.Server, grpc.Channel]) -> None:
    _, channel = live_server
    monitor = HealthMonitor(channel, MagicMock(), interval_s=0.05)
    monitor.start()
    with pytest.raises(RuntimeError, match="已啟動"):
        monitor.start()
    monitor.stop()  # 不應拋例外，thread 乾淨結束
