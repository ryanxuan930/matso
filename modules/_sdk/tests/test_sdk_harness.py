"""matso_sdk 整合測試（SPEC §17 驗收）：harness 起真 gRPC server，驗基礎服務樣板。"""

from __future__ import annotations

import grpc
import pytest
from matso_sdk import HealthState, Manifest, MatsoPlugin, PluginKind, from_proto, run_plugin
from matso_sdk._generated import plugin_base_pb2


class _DummyPlugin(MatsoPlugin):
    """最小插件：無領域服務，健康/設定可由建構參數控制，測 SDK 樣板本身。"""

    def __init__(
        self, state: HealthState = HealthState.HEALTHY, configurable: bool = False
    ) -> None:
        self._state = state
        self._configurable = configurable
        self.last_config: str | None = None

    @property
    def manifest(self) -> Manifest:
        return Manifest(
            name="dummy",
            kind=PluginKind.CUSTOM,
            contract_version="1.2.3",
            capabilities=("noop",),
        )

    def register_domain_services(self, server: grpc.Server) -> None:
        return  # 無領域服務

    def health(self) -> tuple[HealthState, str]:
        return self._state, f"state={self._state}"

    def configure(self, config_json: str) -> tuple[bool, str]:
        if not self._configurable:
            return False, "not supported"
        self.last_config = config_json
        return True, "applied"


def test_manifest_roundtrip() -> None:
    with run_plugin(_DummyPlugin()) as h:
        m = h.manifest()
        assert m.name == "dummy"
        assert m.kind == "CUSTOM"
        assert m.contract_version == "1.2.3"
        assert list(m.capabilities) == ["noop"]


def test_health_healthy() -> None:
    with run_plugin(_DummyPlugin(HealthState.HEALTHY)) as h:
        resp = h.base_stub().HealthCheck(plugin_base_pb2.HealthCheckRequest())
        assert from_proto(resp.state) is HealthState.HEALTHY


def test_health_degraded_and_down() -> None:
    for state in (HealthState.DEGRADED, HealthState.DOWN):
        with run_plugin(_DummyPlugin(state)) as h:
            resp = h.base_stub().HealthCheck(plugin_base_pb2.HealthCheckRequest())
            assert from_proto(resp.state) is state
            assert resp.detail == f"state={state}"


def test_configure_default_rejected() -> None:
    with run_plugin(_DummyPlugin(configurable=False)) as h:
        resp = h.base_stub().Configure(plugin_base_pb2.ConfigureRequest(config_json="{}"))
        assert resp.ok is False


def test_configure_applied_when_supported() -> None:
    plugin = _DummyPlugin(configurable=True)
    with run_plugin(plugin) as h:
        resp = h.base_stub().Configure(plugin_base_pb2.ConfigureRequest(config_json='{"k":1}'))
        assert resp.ok is True
        assert plugin.last_config == '{"k":1}'


def test_manifest_major_version() -> None:
    assert _DummyPlugin().manifest.major == 1


def test_channel_ready_and_multiple_calls() -> None:
    # harness channel 應立即可用，且可連續多次呼叫（server 常駐）
    with run_plugin(_DummyPlugin()) as h:
        stub = h.base_stub()
        for _ in range(5):
            assert stub.GetManifest(plugin_base_pb2.GetManifestRequest()).manifest.name == "dummy"


def test_server_closed_after_context() -> None:
    with run_plugin(_DummyPlugin()) as h:
        target = h.target
    # 離開 context 後 server 已關；新 channel 連線應失敗（快速 deadline）
    channel = grpc.insecure_channel(target)
    with pytest.raises(grpc.FutureTimeoutError):
        grpc.channel_ready_future(channel).result(timeout=1.0)
    channel.close()
