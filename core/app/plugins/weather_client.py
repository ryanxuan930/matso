"""Weather 插件 gRPC 客戶端（O5.3）——GetWeather → WeatherState。

weather **非硬依賴**（不像 terrain）：插件不可達 / stale → 降級為 CLEAR（所有 modifier=1.0，
無天氣影響），不 PAUSE session。Core 每天氣 tick 呼叫一次，更新當前 WeatherState。
"""

from __future__ import annotations

import grpc
from matso_sdk._generated import weather_pb2, weather_pb2_grpc

from app.weather import CellEffects, WeatherState

_DEFAULT_DEADLINE_S = 0.2


class WeatherClient:
    def __init__(self, channel: grpc.Channel, deadline_s: float = _DEFAULT_DEADLINE_S) -> None:
        self._stub = weather_pb2_grpc.WeatherServiceStub(channel)
        self._deadline = deadline_s

    def fetch_state(self, sim_tick: int) -> WeatherState:
        """取當前天氣狀態；插件不可達 → CLEAR（降級，無天氣影響）。"""
        try:
            resp = self._stub.GetWeather(
                weather_pb2.GetWeatherRequest(sim_tick=sim_tick), timeout=self._deadline
            )
        except grpc.RpcError:
            return WeatherState.clear()
        cells = {cell.h3_index: _cell_from_proto(cell.effects) for cell in resp.cells}
        return WeatherState(cells, stale=resp.stale)


def _cell_from_proto(effects: weather_pb2.WeatherEffects) -> CellEffects:
    return CellEffects(
        rf_attenuation_db=effects.rf_attenuation_db,
        mobility_modifier=effects.mobility_modifier,
        sensor_optical_modifier=effects.sensor_optical_modifier,
        sensor_ir_modifier=effects.sensor_ir_modifier,
        uav_operability=effects.uav_operability,
        rotary_wing_operability=effects.rotary_wing_operability,
        artillery_dispersion_modifier=effects.artillery_dispersion_modifier,
    )
