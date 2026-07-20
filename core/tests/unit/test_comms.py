"""通訊狀態的戰術後果（O5.4，SPEC §6.2「MUST enforce」逐條）。

§6.2 表格三列各有測試：ONLINE / DEGRADED / OFFLINE 的指令傳遞、位置回報、敵情粒度後果。
"""

from __future__ import annotations

from app.comms import (
    DEFAULT_DEGRADED_DELAY_TICKS,
    CommsState,
    IntelGranularity,
    LinkState,
    can_receive_command,
    command_delivery,
    intel_granularity,
    position_report_frozen,
    position_report_interval,
)

# ---------------- §6.2 列 1：ONLINE「正常接收指令、即時回報位置」 ----------------


def test_online_receives_command_immediately() -> None:
    d = command_delivery(LinkState.ONLINE)
    assert d.accepted is True
    assert d.delay_ticks == 0
    assert can_receive_command(LinkState.ONLINE) is True


def test_online_reports_position_live_and_not_frozen() -> None:
    assert position_report_frozen(LinkState.ONLINE) is False
    assert position_report_interval(LinkState.ONLINE, base_interval_ticks=1) == 1


def test_online_intel_full() -> None:
    assert intel_granularity(LinkState.ONLINE) is IntelGranularity.FULL


# ---------------- §6.2 列 2：DEGRADED「指令延遲 N ticks；回報降頻；敵情粒度下降」 ----------------


def test_degraded_command_delayed_n_ticks() -> None:
    d = command_delivery(LinkState.DEGRADED)
    assert d.accepted is True  # 仍能收，只是延遲
    assert d.delay_ticks == DEFAULT_DEGRADED_DELAY_TICKS
    assert d.delay_ticks > 0
    assert can_receive_command(LinkState.DEGRADED) is True


def test_degraded_command_delay_configurable_n() -> None:
    assert command_delivery(LinkState.DEGRADED, degraded_delay_ticks=5).delay_ticks == 5


def test_degraded_position_report_downrated() -> None:
    online = position_report_interval(LinkState.ONLINE, base_interval_ticks=2)
    degraded = position_report_interval(LinkState.DEGRADED, base_interval_ticks=2)
    assert degraded is not None and online is not None
    assert degraded > online  # 降頻 → 間隔變長
    assert position_report_frozen(LinkState.DEGRADED) is False


def test_degraded_intel_coarser() -> None:
    assert intel_granularity(LinkState.DEGRADED) is IntelGranularity.COARSE


# ---------------- §6.2 列 3：OFFLINE「無法接收新指令；位置對己方 COP 凍結」 ----------------


def test_offline_cannot_receive_command() -> None:
    d = command_delivery(LinkState.OFFLINE)
    assert d.accepted is False  # 執行最後有效指令 / doctrine fallback
    assert can_receive_command(LinkState.OFFLINE) is False


def test_offline_position_frozen_in_cop() -> None:
    assert position_report_frozen(LinkState.OFFLINE) is True
    assert position_report_interval(LinkState.OFFLINE, base_interval_ticks=2) is None  # 不回報


def test_offline_intel_frozen() -> None:
    assert intel_granularity(LinkState.OFFLINE) is IntelGranularity.FROZEN


# ---------------- CommsState 快照 ----------------


def test_comms_state_lookup() -> None:
    state = CommsState({"u1": LinkState.OFFLINE, "u2": LinkState.DEGRADED})
    assert state.state_of("u1") is LinkState.OFFLINE
    assert state.state_of("u2") is LinkState.DEGRADED
    assert state.state_of("unknown") is LinkState.ONLINE  # 樂觀預設


def test_comms_state_all_online_degrade() -> None:
    state = CommsState.all_online()
    assert state.state_of("anything") is LinkState.ONLINE


def test_full_consequence_chain_differs_by_state() -> None:
    # 三態的後果整體可觀測地不同（驗收核心：§6.2 逐列後果彼此有別）
    def _summary(s: LinkState) -> tuple[bool, int, bool, IntelGranularity]:
        d = command_delivery(s)
        return (d.accepted, d.delay_ticks, position_report_frozen(s), intel_granularity(s))

    online = _summary(LinkState.ONLINE)
    degraded = _summary(LinkState.DEGRADED)
    offline = _summary(LinkState.OFFLINE)
    assert online != degraded != offline
    assert online != offline
