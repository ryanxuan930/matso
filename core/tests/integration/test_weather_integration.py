"""天氣效果整合驗收（O5.3）——同一交戰/偵測在暴雨 vs 晴天結果分佈可觀測地不同。

純 adjudication + Core 天氣映射（本地 SQLite 無關；不需 compose），CI python job 常駐。
另驗 WeatherClient（gRPC → WeatherState；插件不可達 → CLEAR 降級）。
"""

from __future__ import annotations

import grpc
from matso_sdk import Manifest, MatsoPlugin, PluginKind, run_plugin
from matso_sdk._generated import weather_pb2, weather_pb2_grpc

from app.adjudication.aggregate import AggregateEnv, AggregateForce, resolve_aggregate_tick
from app.adjudication.engagement import EnvSnapshot, Resolution, Shooter, Target, resolve_engagement
from app.adjudication.seed_weapons import SEED_WEAPONS
from app.adjudication.weapon import WeaponProfile
from app.engine.rng import DeterministicRNG
from app.intel.seed_sensors import SEED_SENSORS
from app.intel.sensor import DetectionEnv, SensorProfile, detect_probability
from app.plugins.weather_client import WeatherClient
from app.weather import (
    CellEffects,
    WeatherState,
    aggregate_weather_modifier,
    detection_weather_modifier,
    engagement_weather_modifier,
)

_AUTOCANNON = WeaponProfile.from_base_stats(SEED_WEAPONS["AUTOCANNON_30"])
_EO = SensorProfile.from_base_stats(SEED_SENSORS["EO_DAY"])
_STORM = CellEffects(sensor_optical_modifier=0.4, mobility_modifier=0.6, uav_operability=False)
_CLEAR = CellEffects()  # 晴天


def _rng() -> DeterministicRNG:
    return DeterministicRNG(42, "adjudication")


def _engage_env(effects: CellEffects, range_m: float = 500.0) -> EnvSnapshot:
    return EnvSnapshot(
        range_m=range_m,
        los_clear=True,
        weather_modifier=engagement_weather_modifier(effects, indirect_fire=False),
    )


# ---------------- 交戰：暴雨 vs 晴天（驗收核心） ----------------


def test_engagement_phit_lower_in_storm() -> None:
    shooter, target = Shooter("s", 100), Target("t", "INFANTRY", 100.0)
    p_clear = resolve_engagement(_AUTOCANNON, shooter, target, _engage_env(_CLEAR), _rng(), 0).p_hit
    p_storm = resolve_engagement(_AUTOCANNON, shooter, target, _engage_env(_STORM), _rng(), 0).p_hit
    assert p_storm < p_clear  # 暴雨降低命中率
    assert p_storm == p_clear * 0.4  # 恰為 optical 係數（固定 seed 比係數）


def test_engagement_hit_distribution_observably_different() -> None:
    # 200 次交戰固定 seed：暴雨命中數應顯著少於晴天（可觀測的分佈差異）
    def _hits(effects: CellEffects) -> int:
        rng = _rng()
        shooter = Shooter("s", 1000)
        return sum(
            resolve_engagement(
                _AUTOCANNON, shooter, Target("t", "INFANTRY", 100.0), _engage_env(effects), rng, i
            ).status
            is Resolution.HIT
            for i in range(200)
        )

    hits_clear, hits_storm = _hits(_CLEAR), _hits(_STORM)
    assert hits_storm < hits_clear
    assert hits_clear - hits_storm > 30  # 差異顯著（base_ph~0.7：晴~0.7 vs 暴雨~0.28）


# ---------------- 偵測：暴雨 vs 晴天 ----------------


def test_detection_probability_lower_in_storm() -> None:
    def _env(effects: CellEffects) -> DetectionEnv:
        return DetectionEnv(
            los_clear=True, weather_modifier=detection_weather_modifier(effects, _EO.sensor_kind)
        )

    p_clear = detect_probability(_EO, 1000, _env(_CLEAR))
    p_storm = detect_probability(_EO, 1000, _env(_STORM))
    assert p_storm < p_clear  # 光學感測在暴雨偵測率下降


# ---------------- 聚合裁決：暴雨 vs 晴天 ----------------


def test_aggregate_losses_differ_by_weather() -> None:
    def _loss(effects: CellEffects) -> float:
        blue, red = AggregateForce("b", "BLUE", 500, 1.0), AggregateForce("r", "RED", 500, 1.0)
        env = AggregateEnv(variance=0.0, weather_modifier=aggregate_weather_modifier(effects))
        return resolve_aggregate_tick(blue, red, env, _rng(), 0).b_loss

    assert _loss(_STORM) < _loss(_CLEAR)  # 暴雨降低戰鬥效率 → 戰損較低


# ---------------- WeatherClient（gRPC → WeatherState） ----------------


class _FakeWeatherServicer(weather_pb2_grpc.WeatherServiceServicer):
    def GetWeather(  # noqa: N802
        self, request: weather_pb2.GetWeatherRequest, context: grpc.ServicerContext
    ) -> weather_pb2.GetWeatherResponse:
        return weather_pb2.GetWeatherResponse(
            issued_at_sim_tick=request.sim_tick,
            mode=weather_pb2.WEATHER_MODE_LIVE,
            stale=False,
            cells=[
                weather_pb2.WeatherCell(
                    h3_index="storm_cell",
                    effects=weather_pb2.WeatherEffects(
                        sensor_optical_modifier=0.4,
                        mobility_modifier=0.6,
                        uav_operability=False,
                        artillery_dispersion_modifier=1.2,
                        sensor_ir_modifier=0.8,
                        rotary_wing_operability=True,
                        rf_attenuation_db=5.0,
                    ),
                )
            ],
        )


class _FakeWeatherPlugin(MatsoPlugin):
    @property
    def manifest(self) -> Manifest:
        return Manifest("fakeweather", PluginKind.WEATHER, "0.1.0")

    def register_domain_services(self, server: grpc.Server) -> None:
        weather_pb2_grpc.add_WeatherServiceServicer_to_server(_FakeWeatherServicer(), server)


def test_weather_client_fetches_state() -> None:
    with run_plugin(_FakeWeatherPlugin()) as h:
        state = WeatherClient(h.channel).fetch_state(5)
        eff = state.effects_at("storm_cell")
        assert eff.sensor_optical_modifier == 0.4
        assert eff.uav_operability is False
        assert state.effects_at("unknown").sensor_optical_modifier == 1.0  # 查無 → CLEAR


def test_weather_client_degrades_to_clear_on_failure() -> None:
    # 指向沒有服務的埠 → CLEAR 降級（weather 非硬依賴）
    channel = grpc.insecure_channel("127.0.0.1:1")
    state = WeatherClient(channel, deadline_s=0.3).fetch_state(0)
    assert isinstance(state, WeatherState)
    assert state.effects_at("any").sensor_optical_modifier == 1.0  # 全晴
    channel.close()
