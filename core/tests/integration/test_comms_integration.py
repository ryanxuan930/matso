"""Comms 整合驗收（O5.4）——真 comms 插件（gRPC）算鏈路狀態 → Core 強制 §6.2 後果。

用 matso_sdk harness 起真 CommsPlugin；驗證同一佈署下 ONLINE/DEGRADED/OFFLINE 三態單位各自
承受不同的 §6.2 戰術後果。另驗 CommsClient 降級（插件不可達 → 全 ONLINE，comms 非硬依賴）。

純插件 + Core 映射（本地無關；不需 compose），CI python job 常駐。
"""

from __future__ import annotations

import grpc
from matso_sdk import run_plugin
from matso_sdk._generated import comms_pb2

from app.comms import (
    IntelGranularity,
    LinkState,
    can_receive_command,
    command_delivery,
    intel_granularity,
    position_report_frozen,
)
from app.plugins.comms_client import CommsClient
from comms import CommsPlugin


def _unit(uid: str, lng_step: int, *, hq: bool = False) -> comms_pb2.CommsUnit:
    return comms_pb2.CommsUnit(
        unit_id=uid,
        faction="BLUE",
        lat=24.0,
        lng=121.0 + 0.01 * lng_step,
        tx_power_dbm=30.0,
        antenna_gain_db=3.0,
        rx_sensitivity_dbm=-100.0,
        freq_mhz=150.0,
        is_command_node=hq,
    )


def test_end_to_end_three_states_get_distinct_consequences() -> None:
    # 佈署：hq(ONLINE) — near(直連 ONLINE) ；far 直連 hq 被遮蔽但經 degraded 中繼 → DEGRADED；
    # island 全被遮蔽 → OFFLINE。
    units = [_unit("hq", 0, hq=True), _unit("near", 1), _unit("far", 2), _unit("island", 3)]
    obstructions = [
        comms_pb2.LinkObstruction(unit_a="hq", unit_b="far", extra_loss_db=200.0),
        comms_pb2.LinkObstruction(unit_a="near", unit_b="far", extra_loss_db=57.0),  # DEGRADED 跳
        comms_pb2.LinkObstruction(unit_a="hq", unit_b="island", extra_loss_db=200.0),
        comms_pb2.LinkObstruction(unit_a="near", unit_b="island", extra_loss_db=200.0),
        comms_pb2.LinkObstruction(unit_a="far", unit_b="island", extra_loss_db=200.0),
    ]
    with run_plugin(CommsPlugin()) as h:
        state = CommsClient(h.channel).fetch_state(3, units, obstructions=obstructions)

    assert state.state_of("near") is LinkState.ONLINE
    assert state.state_of("far") is LinkState.DEGRADED
    assert state.state_of("island") is LinkState.OFFLINE

    # §6.2 後果逐態可觀測不同：
    # ONLINE near：即時收令、粒度完整、位置不凍
    assert command_delivery(state.state_of("near")).delay_ticks == 0
    assert intel_granularity(state.state_of("near")) is IntelGranularity.FULL
    assert position_report_frozen(state.state_of("near")) is False
    # DEGRADED far：收令但延遲、粒度粗化
    assert command_delivery(state.state_of("far")).delay_ticks > 0
    assert intel_granularity(state.state_of("far")) is IntelGranularity.COARSE
    # OFFLINE island：拒收新令、COP 位置凍結
    assert can_receive_command(state.state_of("island")) is False
    assert position_report_frozen(state.state_of("island")) is True


def test_client_degrades_to_all_online_on_failure() -> None:
    # 指向沒有服務的埠 → 全 ONLINE 降級（comms 非硬依賴）
    channel = grpc.insecure_channel("127.0.0.1:1")
    state = CommsClient(channel, deadline_s=0.3).fetch_state(0, [_unit("x", 0)])
    assert state.state_of("x") is LinkState.ONLINE
    assert can_receive_command(state.state_of("x")) is True
    channel.close()
