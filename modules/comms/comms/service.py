"""CommsService gRPC 實作（O5.4）——ComputeLinks：proto → 純解算 → proto。

模組保持純：地形遮蔽附加損耗、天氣 RF 衰減皆由 request 攜入（Core 從 terrain/weather 填）。
"""

from __future__ import annotations

import grpc
from matso_sdk._generated import comms_pb2, comms_pb2_grpc

from comms.link_budget import LinkState, Radio
from comms.mesh import CommsUnitInput, UnitResult, resolve_comms

_STATE_TO_PROTO = {
    LinkState.ONLINE: comms_pb2.LINK_STATE_ONLINE,
    LinkState.DEGRADED: comms_pb2.LINK_STATE_DEGRADED,
    LinkState.OFFLINE: comms_pb2.LINK_STATE_OFFLINE,
}


class CommsService(comms_pb2_grpc.CommsServiceServicer):
    def ComputeLinks(  # noqa: N802 (gRPC 產生的方法名)
        self, request: comms_pb2.ComputeLinksRequest, context: grpc.ServicerContext
    ) -> comms_pb2.ComputeLinksResponse:
        units = [
            CommsUnitInput(
                unit_id=u.unit_id,
                faction=u.faction,
                lat=u.lat,
                lng=u.lng,
                radio=Radio(
                    tx_power_dbm=u.tx_power_dbm,
                    antenna_gain_db=u.antenna_gain_db,
                    rx_sensitivity_dbm=u.rx_sensitivity_dbm,
                    freq_mhz=u.freq_mhz,
                ),
                is_command_node=u.is_command_node,
            )
            for u in request.units
        ]
        obstruction = {
            frozenset((o.unit_a, o.unit_b)): o.extra_loss_db for o in request.obstructions
        }
        weather = {w.unit_id: w.rf_attenuation_db for w in request.weather}
        results = resolve_comms(
            units,
            obstruction_db=lambda a, b: obstruction.get(frozenset((a, b)), 0.0),
            weather_attenuation_db=lambda u: weather.get(u, 0.0),
            jamming_db=request.jamming_db,
        )
        return comms_pb2.ComputeLinksResponse(
            issued_at_sim_tick=request.sim_tick,
            units=[_unit_to_proto(r) for r in results],
        )


def _unit_to_proto(r: UnitResult) -> comms_pb2.UnitComms:
    return comms_pb2.UnitComms(
        unit_id=r.unit_id,
        state=_STATE_TO_PROTO[r.state],
        links=[
            comms_pb2.UnitLink(
                peer_id=lr.peer_id, margin_db=lr.margin_db, state=_STATE_TO_PROTO[lr.state]
            )
            for lr in r.links
        ],
    )
