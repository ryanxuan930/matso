"""WeatherService gRPC 實作（O5.1）——把 SYNTHETIC 引擎的 payload 映射為 proto。"""

from __future__ import annotations

import grpc
from matso_sdk._generated import weather_pb2, weather_pb2_grpc

from weather.payload import WeatherMode, WeatherPayload
from weather.synthetic import SyntheticWeather

_MODE_TO_PROTO = {
    WeatherMode.LIVE: weather_pb2.WEATHER_MODE_LIVE,
    WeatherMode.SYNTHETIC: weather_pb2.WEATHER_MODE_SYNTHETIC,
    WeatherMode.REPLAY: weather_pb2.WEATHER_MODE_REPLAY,
}


class WeatherService(weather_pb2_grpc.WeatherServiceServicer):
    def __init__(self, engine: SyntheticWeather) -> None:
        self._engine = engine

    def GetWeather(  # noqa: N802 (gRPC 產生的方法名)
        self, request: weather_pb2.GetWeatherRequest, context: grpc.ServicerContext
    ) -> weather_pb2.GetWeatherResponse:
        return _to_proto(self._engine.payload_at(request.sim_tick))


def _to_proto(payload: WeatherPayload) -> weather_pb2.GetWeatherResponse:
    return weather_pb2.GetWeatherResponse(
        issued_at_sim_tick=payload.issued_at_sim_tick,
        mode=_MODE_TO_PROTO[payload.mode],
        stale=payload.stale,
        cells=[
            weather_pb2.WeatherCell(
                h3_index=c.h3_index,
                precipitation_mmhr=c.raw.precipitation_mmhr,
                wind_ms=c.raw.wind_ms,
                wind_dir_deg=c.raw.wind_dir_deg,
                visibility_m=c.raw.visibility_m,
                cloud_base_m=c.raw.cloud_base_m,
                effects=weather_pb2.WeatherEffects(
                    rf_attenuation_db=c.effects.rf_attenuation_db,
                    mobility_modifier=c.effects.mobility_modifier,
                    sensor_optical_modifier=c.effects.sensor_optical_modifier,
                    sensor_ir_modifier=c.effects.sensor_ir_modifier,
                    uav_operability=c.effects.uav_operability,
                    rotary_wing_operability=c.effects.rotary_wing_operability,
                    artillery_dispersion_modifier=c.effects.artillery_dispersion_modifier,
                ),
            )
            for c in payload.cells
        ],
    )
