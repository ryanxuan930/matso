"""插件健康狀態（SPEC §16.3；對映 plugin_base.proto HealthState）。"""

from __future__ import annotations

import enum

from matso_sdk._generated import plugin_base_pb2


class HealthState(enum.StrEnum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"  # 例：weather stale、terrain 無 DTED 但有 hex 快取
    DOWN = "DOWN"


# proto HealthState 為 int 子類；保留其型別供訊息建構子，反向對映則以純 int 為鍵。
_TO_PROTO: dict[HealthState, plugin_base_pb2.HealthState] = {
    HealthState.HEALTHY: plugin_base_pb2.HEALTH_STATE_HEALTHY,
    HealthState.DEGRADED: plugin_base_pb2.HEALTH_STATE_DEGRADED,
    HealthState.DOWN: plugin_base_pb2.HEALTH_STATE_DOWN,
}
_FROM_PROTO: dict[int, HealthState] = {int(v): k for k, v in _TO_PROTO.items()}


def to_proto(state: HealthState) -> plugin_base_pb2.HealthState:
    return _TO_PROTO[state]


def from_proto(value: int) -> HealthState:
    """未知/UNSPECIFIED 一律視為 DOWN（保守：無法確認健康即當故障處理）。"""
    return _FROM_PROTO.get(int(value), HealthState.DOWN)
