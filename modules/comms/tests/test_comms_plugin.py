"""Comms 插件整合測試（O5.4）：harness 起 gRPC server，ComputeLinks 回鏈路狀態。"""

from __future__ import annotations

from comms.plugin import CommsPlugin
from matso_sdk import HealthState, from_proto, run_plugin
from matso_sdk._generated import comms_pb2, comms_pb2_grpc, plugin_base_pb2


def _unit(
    uid: str, lng_step: int, *, hq: bool = False, faction: str = "BLUE"
) -> comms_pb2.CommsUnit:
    return comms_pb2.CommsUnit(
        unit_id=uid,
        faction=faction,
        lat=24.0,
        lng=121.0 + 0.01 * lng_step,
        tx_power_dbm=30.0,
        antenna_gain_db=3.0,
        rx_sensitivity_dbm=-100.0,
        freq_mhz=150.0,
        is_command_node=hq,
    )


def test_manifest_and_health() -> None:
    with run_plugin(CommsPlugin()) as h:
        m = h.manifest()
        assert m.name == "comms"
        assert m.kind == "COMMS"
        resp = h.base_stub().HealthCheck(plugin_base_pb2.HealthCheckRequest())
        assert from_proto(resp.state) is HealthState.HEALTHY


def test_compute_links_multi_hop() -> None:
    with run_plugin(CommsPlugin()) as h:
        stub = comms_pb2_grpc.CommsServiceStub(h.channel)
        req = comms_pb2.ComputeLinksRequest(
            sim_tick=7,
            units=[_unit("hq", 0, hq=True), _unit("a", 1), _unit("b", 2)],
            obstructions=[comms_pb2.LinkObstruction(unit_a="hq", unit_b="b", extra_loss_db=200.0)],
        )
        resp = stub.ComputeLinks(req)
        assert resp.issued_at_sim_tick == 7
        states = {u.unit_id: u.state for u in resp.units}
        # b 直接與 hq 斷，但經 a 中繼 → ONLINE
        assert states["b"] == comms_pb2.LINK_STATE_ONLINE
        assert states["hq"] == comms_pb2.LINK_STATE_ONLINE


def test_compute_links_island_offline() -> None:
    with run_plugin(CommsPlugin()) as h:
        stub = comms_pb2_grpc.CommsServiceStub(h.channel)
        req = comms_pb2.ComputeLinksRequest(
            units=[_unit("hq", 0, hq=True), _unit("c", 1)],
            weather=[comms_pb2.WeatherAttenuation(unit_id="c", rf_attenuation_db=200.0)],
        )
        resp = stub.ComputeLinks(req)
        states = {u.unit_id: u.state for u in resp.units}
        assert states["c"] == comms_pb2.LINK_STATE_OFFLINE
