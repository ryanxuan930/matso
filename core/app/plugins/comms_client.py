"""Comms 插件 gRPC 客戶端（O5.4）——ComputeLinks → CommsState。

comms **非硬依賴**（不像 terrain）：插件不可達 → 降級為全 ONLINE（無通訊限制，不因基礎設施
故障懲罰玩家），不 PAUSE session。Core 每通訊 tick 呼叫一次，更新當前 CommsState。

地形遮蔽附加損耗、天氣 RF 衰減由 Core 從 terrain/weather client 蒐集後隨 request 攜入（保持
comms 模組純）——真實接線於部署層（同 O3.6/O5.3 注入假件）。
"""

from __future__ import annotations

from collections.abc import Iterable

import grpc
from matso_sdk._generated import comms_pb2, comms_pb2_grpc

from app.comms import CommsState, LinkState

_DEFAULT_DEADLINE_S = 0.2

_STATE_FROM_PROTO = {
    comms_pb2.LINK_STATE_ONLINE: LinkState.ONLINE,
    comms_pb2.LINK_STATE_DEGRADED: LinkState.DEGRADED,
    comms_pb2.LINK_STATE_OFFLINE: LinkState.OFFLINE,
}


class CommsClient:
    def __init__(self, channel: grpc.Channel, deadline_s: float = _DEFAULT_DEADLINE_S) -> None:
        self._stub = comms_pb2_grpc.CommsServiceStub(channel)
        self._deadline = deadline_s

    def fetch_state(
        self,
        sim_tick: int,
        units: Iterable[comms_pb2.CommsUnit],
        *,
        obstructions: Iterable[comms_pb2.LinkObstruction] = (),
        weather: Iterable[comms_pb2.WeatherAttenuation] = (),
        jamming_db: float = 0.0,
    ) -> CommsState:
        """取當前每單位鏈路狀態；插件不可達 → 全 ONLINE（降級，無通訊限制）。"""
        req = comms_pb2.ComputeLinksRequest(
            sim_tick=sim_tick,
            units=list(units),
            obstructions=list(obstructions),
            weather=list(weather),
            jamming_db=jamming_db,
        )
        try:
            resp = self._stub.ComputeLinks(req, timeout=self._deadline)
        except grpc.RpcError:
            return CommsState.all_online()
        states = {u.unit_id: _STATE_FROM_PROTO.get(u.state, LinkState.ONLINE) for u in resp.units}
        return CommsState(states)
