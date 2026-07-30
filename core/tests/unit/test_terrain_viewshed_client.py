"""TerrainClient.get_viewshed 對真 in-process gRPC 的整合測試（M11a）。

沿用 test_terrain_client_grpc.py 的做法：以 matso_sdk 起一個假 terrain 插件（core 只依賴
SDK，不依賴 matso-terrain），驗證視域查詢的 roundtrip、失敗退化與 deadline。
"""

from __future__ import annotations

import time
from collections.abc import Iterator

import grpc
import pytest
from matso_sdk import HealthState, Manifest, MatsoPlugin, PluginKind, build_server
from matso_sdk._generated import terrain_pb2, terrain_pb2_grpc

from app.errors import TerrainUnavailableError
from app.plugins.terrain_client import CircuitBreaker, TerrainClient

_VISIBLE = ("8828308281fffff", "8828308283fffff", "8828308285fffff")
_OBS = (23.75, 121.25, 2.0)
# 比領域 RPC 預設 deadline（0.2s）長、比視域 deadline（1.0s）短：正好卡在兩者之間，
# 用來證明視域走的是自己的 deadline 而不是預設值。
_SLOW_S = 0.35


class _FakeTerrainServicer(terrain_pb2_grpc.TerrainServiceServicer):
    def __init__(self, delay_s: float = 0.0) -> None:
        self._delay = delay_s

    def GetViewshed(  # noqa: N802
        self, request: terrain_pb2.GetViewshedRequest, context: grpc.ServicerContext
    ) -> terrain_pb2.GetViewshedResponse:
        if self._delay:
            time.sleep(self._delay)
        return terrain_pb2.GetViewshedResponse(visible_h3=list(_VISIBLE))

    def GetElevation(  # noqa: N802
        self, request: terrain_pb2.GetElevationRequest, context: grpc.ServicerContext
    ) -> terrain_pb2.GetElevationResponse:
        if self._delay:
            time.sleep(self._delay)
        return terrain_pb2.GetElevationResponse(elevation_m=100.0, water=False)


class _FakeTerrainPlugin(MatsoPlugin):
    def __init__(self, delay_s: float = 0.0) -> None:
        self._delay = delay_s

    @property
    def manifest(self) -> Manifest:
        return Manifest(name="faketerrain", kind=PluginKind.TERRAIN, contract_version="0.1.0")

    def register_domain_services(self, server: grpc.Server) -> None:
        terrain_pb2_grpc.add_TerrainServiceServicer_to_server(
            _FakeTerrainServicer(self._delay), server
        )

    def health(self) -> tuple[HealthState, str]:
        return HealthState.HEALTHY, ""


def _serve(delay_s: float = 0.0) -> Iterator[tuple[grpc.Server, grpc.Channel]]:
    server, port = build_server(_FakeTerrainPlugin(delay_s), host="127.0.0.1", port=0)
    server.start()
    channel = grpc.insecure_channel(f"127.0.0.1:{port}")
    grpc.channel_ready_future(channel).result(timeout=5.0)
    try:
        yield server, channel
    finally:
        channel.close()
        server.stop(0).wait()


@pytest.fixture
def live_server() -> Iterator[tuple[grpc.Server, grpc.Channel]]:
    yield from _serve()


@pytest.fixture
def slow_server() -> Iterator[tuple[grpc.Server, grpc.Channel]]:
    yield from _serve(_SLOW_S)


def test_get_viewshed_roundtrip(live_server: tuple[grpc.Server, grpc.Channel]) -> None:
    """抓的病：Core 端根本沒有 get_viewshed（rg 零命中），感測涵蓋只能逐目標問 CheckLos。"""
    _, channel = live_server
    resp = TerrainClient(channel).get_viewshed(_OBS, radius_m=3000.0)
    assert tuple(resp.visible_h3) == _VISIBLE


def test_get_viewshed_raises_instead_of_empty_list_when_plugin_dead(
    live_server: tuple[grpc.Server, grpc.Channel],
) -> None:
    """抓的病：插件掛掉時回空視域（＝靜默降級），上層會把「查不到」當成「看不到」用下去。"""
    server, channel = live_server
    client = TerrainClient(channel, deadline_s=0.3, breaker=CircuitBreaker(failure_threshold=3))
    assert client.get_viewshed(_OBS, radius_m=3000.0).visible_h3  # 先確認活著

    server.stop(0).wait()  # 模擬 terrain 容器被殺
    with pytest.raises(TerrainUnavailableError):
        client.get_viewshed(_OBS, radius_m=3000.0)


def test_non_positive_radius_rejected_locally_without_tripping_breaker(
    live_server: tuple[grpc.Server, grpc.Channel],
) -> None:
    """抓的病：非正半徑（呼叫端 bug）被送到插件回 INVALID_ARGUMENT，卻被計入斷路器失敗數，
    最後把整個 terrain 打成快速失敗、連累 GetElevation/CheckLos。"""
    _, channel = live_server
    breaker = CircuitBreaker(failure_threshold=1)
    client = TerrainClient(channel, breaker=breaker)

    for bad in (0.0, -1.0):
        with pytest.raises(ValueError, match="radius_m"):
            client.get_viewshed(_OBS, radius_m=bad)

    assert breaker.state.value == "CLOSED"  # 沒有被誤記成插件故障
    assert client.get_viewshed(_OBS, radius_m=3000.0).visible_h3  # 正常查詢不受影響


def test_viewshed_uses_its_own_longer_deadline(
    slow_server: tuple[grpc.Server, grpc.Channel],
) -> None:
    """抓的病：視域沿用 0.2s 預設 deadline，但契約 SLA 就是 p99<200ms——正常回應會在 p99
    附近整片 DEADLINE_EXCEEDED，還會誤觸斷路器。"""
    _, channel = slow_server

    # 同一個插件、同樣的 0.35s 回應時間：視域過（1.0s），預設 deadline 的領域 RPC 逾時。
    assert TerrainClient(channel).get_viewshed(_OBS, radius_m=3000.0).visible_h3 == list(_VISIBLE)
    with pytest.raises(TerrainUnavailableError):
        TerrainClient(channel).get_elevation(_OBS[0], _OBS[1])


def test_viewshed_deadline_is_configurable(
    slow_server: tuple[grpc.Server, grpc.Channel],
) -> None:
    """抓的病：deadline 若寫死或取 max()，呼叫端刻意設的短逾時會被靜默忽略（值存在卻沒作用）。"""
    _, channel = slow_server
    client = TerrainClient(channel, viewshed_deadline_s=0.05)
    with pytest.raises(TerrainUnavailableError):
        client.get_viewshed(_OBS, radius_m=3000.0)
