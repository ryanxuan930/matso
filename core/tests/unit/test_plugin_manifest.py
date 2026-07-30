"""插件契約版本協商（M11b）——GetManifest + contract_version major 比對。

以 matso_sdk 起真的 in-process 插件（PluginBaseService 由 SDK 統一提供，三個插件都繼承），
驗證「不相容就明確拒絕」而不是靜默降級。
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from concurrent import futures

import grpc
import pytest
from matso_sdk import HealthState, Manifest, MatsoPlugin, PluginKind, build_server
from matso_sdk._generated import plugin_base_pb2, plugin_base_pb2_grpc

from app.plugins.manifest import (
    TERRAIN_REQUIRED_CAPABILITIES,
    PluginContractMismatchError,
    PluginManifest,
    PluginUnreachableError,
    fetch_manifest,
    negotiate_contract,
)

_TERRAIN_CAPS = ("GetElevation", "CheckLos", "GetPath", "GetCellBatch", "GetViewshed")


class _FakePlugin(MatsoPlugin):
    def __init__(
        self,
        *,
        name: str = "terrain",
        kind: PluginKind = PluginKind.TERRAIN,
        contract_version: str = "0.1.0",
        capabilities: tuple[str, ...] = _TERRAIN_CAPS,
    ) -> None:
        self._manifest = Manifest(
            name=name, kind=kind, contract_version=contract_version, capabilities=capabilities
        )

    @property
    def manifest(self) -> Manifest:
        return self._manifest

    def register_domain_services(self, server: grpc.Server) -> None:
        return None  # 這一組測試只碰 PluginBaseService

    def health(self) -> tuple[HealthState, str]:
        return HealthState.HEALTHY, ""


def _serve(plugin: MatsoPlugin) -> Iterator[tuple[grpc.Server, grpc.Channel]]:
    server, port = build_server(plugin, host="127.0.0.1", port=0)
    server.start()
    channel = grpc.insecure_channel(f"127.0.0.1:{port}")
    grpc.channel_ready_future(channel).result(timeout=5.0)
    try:
        yield server, channel
    finally:
        channel.close()
        server.stop(0).wait()


@pytest.fixture
def terrain_channel() -> Iterator[grpc.Channel]:
    for _, channel in _serve(_FakePlugin()):
        yield channel


def test_negotiate_accepts_matching_plugin(terrain_channel: grpc.Channel) -> None:
    """抓的病：Core 從沒呼叫過 GetManifest（contract_version/capabilities 零命中），
    連「對面是誰」都不知道就開始用。"""
    manifest = negotiate_contract(
        terrain_channel,
        expected_kind=PluginKind.TERRAIN,
        required_capabilities=TERRAIN_REQUIRED_CAPABILITIES,
    )
    assert manifest.name == "terrain"
    assert manifest.major == 0
    assert manifest.supports("GetViewshed")


def test_incompatible_major_is_refused_and_logged(caplog: pytest.LogCaptureFixture) -> None:
    """抓的病：換上 major 不相容的插件，Core 照樣連上去用，直到某個欄位讀出垃圾才發現。"""
    for _, channel in _serve(_FakePlugin(contract_version="1.0.0")):
        with (
            caplog.at_level(logging.ERROR, logger="app.plugins.manifest"),
            pytest.raises(PluginContractMismatchError, match=r"1\.0\.0"),
        ):
            negotiate_contract(channel, expected_kind=PluginKind.TERRAIN)
        # 「明確拒絕並記錄」——只拋不記，維運在 log 裡看不到插件為什麼沒載入。
        assert any("主版本不相容" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("bad_version", ["", "unknown", "0", "0.1", "v0.1.0"])
def test_unparsable_version_is_refused_not_treated_as_major_zero(bad_version: str) -> None:
    """抓的病：寬鬆解析版本字串（split(".")[0] 之類）會讓沒填版本的插件被當成 major=0，
    正好通過目前的相容性檢查——沒版本的插件反而最容易被載入（fail-open）。"""
    for _, channel in _serve(_FakePlugin(contract_version=bad_version)):
        with pytest.raises(PluginContractMismatchError, match="semver"):
            negotiate_contract(channel, expected_kind=PluginKind.TERRAIN)


def test_wrong_kind_is_refused() -> None:
    """抓的病：把 weather 插件的位址填到 terrain 設定，Core 也會連上去——之後每支地形 RPC
    都回 UNIMPLEMENTED，錯誤要到執行期才浮現。"""
    for _, channel in _serve(_FakePlugin(name="weather", kind=PluginKind.WEATHER)):
        with pytest.raises(PluginContractMismatchError, match="種類不符"):
            negotiate_contract(channel, expected_kind=PluginKind.TERRAIN)


def test_missing_required_capability_is_refused() -> None:
    """抓的病：capabilities 契約有、Core 沒讀——缺能力的插件照樣載入，直到推演中途某支
    RPC 回 UNIMPLEMENTED 才炸，那時已經在推演結果裡了。"""
    for _, channel in _serve(_FakePlugin(capabilities=("GetElevation", "CheckLos"))):
        with pytest.raises(PluginContractMismatchError, match="GetCellBatch"):
            negotiate_contract(
                channel,
                expected_kind=PluginKind.TERRAIN,
                required_capabilities=TERRAIN_REQUIRED_CAPABILITIES,
            )


def test_unreachable_plugin_raises_instead_of_assuming_compatible() -> None:
    """抓的病：握手打不通時當作「大概沒問題」放行（fail-open），等於沒做協商。"""
    for server, channel in _serve(_FakePlugin()):
        server.stop(0).wait()
        with pytest.raises(PluginUnreachableError):
            negotiate_contract(channel, expected_kind=PluginKind.TERRAIN, deadline_s=0.5)


class _EmptyManifestServicer(plugin_base_pb2_grpc.PluginBaseServiceServicer):
    """回一個沒設定 manifest 欄位的成功回應（proto3 讀出來是全預設值的空 Manifest）。"""

    def GetManifest(  # noqa: N802
        self, request: plugin_base_pb2.GetManifestRequest, context: grpc.ServicerContext
    ) -> plugin_base_pb2.GetManifestResponse:
        return plugin_base_pb2.GetManifestResponse()


def test_empty_manifest_field_is_refused() -> None:
    """抓的病：proto3 讀未設定的 message 欄位會拿到全預設值，於是「名叫空字串、版本空白的
    插件」會被當成一個合法 manifest 往下走。"""
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=2))
    plugin_base_pb2_grpc.add_PluginBaseServiceServicer_to_server(_EmptyManifestServicer(), server)
    port = server.add_insecure_port("127.0.0.1:0")
    server.start()
    channel = grpc.insecure_channel(f"127.0.0.1:{port}")
    try:
        grpc.channel_ready_future(channel).result(timeout=5.0)
        with pytest.raises(PluginContractMismatchError, match="manifest"):
            fetch_manifest(channel, deadline_s=2.0)
    finally:
        channel.close()
        server.stop(0).wait()


def test_manifest_validates_version_at_construction() -> None:
    """抓的病：版本字串只在讀 major 的分支才驗證——插件已經在用了才炸。"""
    with pytest.raises(PluginContractMismatchError):
        PluginManifest(
            name="terrain", kind="TERRAIN", contract_version="", capabilities=frozenset()
        )
