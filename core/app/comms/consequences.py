"""通訊狀態的戰術後果（O5.4，SPEC §6.2「MUST enforce」）——Core 端消費 comms 模組算出的
每單位鏈路狀態，映射為指令傳遞 / 位置回報 / 敵情粒度的後果。

分工（延續 O5.3）：comms 模組（`modules/comms/`）算物理鏈路狀態（鏈路預算 + mesh）；Core
在此**強制** §6.2 表格的三種戰術後果。純函數；CommsState 由 comms gRPC client 每通訊 tick
更新（見 app.plugins.comms_client）。

comms 非硬依賴（不像 terrain）：插件不可達 → 全 ONLINE（無通訊限制，不因基礎設施故障懲罰
玩家），不 PAUSE session。與 weather 的 CLEAR 降級同理。

SPEC §6.2 對照：
| ONLINE   | 正常接收指令、即時回報位置 |
| DEGRADED | 指令延遲 N ticks 送達；位置回報降頻；AI 敵情摘要粒度下降 |
| OFFLINE  | 無法接收新指令（執行最後有效指令 / doctrine fallback）；位置對己方 COP 凍結 |
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

# SPEC §6.2「延遲 N ticks」的 v0 預設 N；「位置回報降頻」的 v0 倍率。
DEFAULT_DEGRADED_DELAY_TICKS = 3
DEFAULT_DEGRADED_REPORT_MULTIPLIER = 3


class LinkState(enum.StrEnum):
    """Core 端鏡像 comms 模組的鏈路狀態（proto LinkState）。"""

    ONLINE = "ONLINE"
    DEGRADED = "DEGRADED"
    OFFLINE = "OFFLINE"


class IntelGranularity(enum.StrEnum):
    FULL = "FULL"  # ONLINE：即時完整
    COARSE = "COARSE"  # DEGRADED：粒度下降
    FROZEN = "FROZEN"  # OFFLINE：凍結於最後回報


class CommsState:
    """某通訊 tick 的每單位鏈路狀態快照。查無單位 → ONLINE（樂觀預設，見降級哲學）。"""

    def __init__(self, unit_states: dict[str, LinkState] | None = None) -> None:
        self._states = unit_states or {}

    @classmethod
    def all_online(cls) -> CommsState:
        """全 ONLINE（comms 不可用時的降級預設，無通訊限制）。"""
        return cls({})

    def state_of(self, unit_id: str) -> LinkState:
        return self._states.get(unit_id, LinkState.ONLINE)


@dataclass(frozen=True, slots=True)
class CommandDelivery:
    """一則指令對某單位的傳遞結果。"""

    accepted: bool  # 是否能接收新指令（OFFLINE → False，走 doctrine fallback）
    delay_ticks: int  # 送達延遲（ONLINE=0；DEGRADED=N）


# ---------------- §6.2 戰術後果（純函數，MUST enforce） ----------------


def command_delivery(
    state: LinkState, degraded_delay_ticks: int = DEFAULT_DEGRADED_DELAY_TICKS
) -> CommandDelivery:
    """指令傳遞：ONLINE 即時、DEGRADED 延遲 N ticks、OFFLINE 拒收（執行層走 doctrine fallback）。"""
    if state is LinkState.ONLINE:
        return CommandDelivery(accepted=True, delay_ticks=0)
    if state is LinkState.DEGRADED:
        return CommandDelivery(accepted=True, delay_ticks=degraded_delay_ticks)
    return CommandDelivery(accepted=False, delay_ticks=0)


def can_receive_command(state: LinkState) -> bool:
    """OFFLINE 無法接收新指令（SPEC §6.2）。"""
    return state is not LinkState.OFFLINE


def order_admissible(
    state: LinkState,
    issued_tick: int,
    now_tick: int,
    degraded_delay_ticks: int = DEFAULT_DEGRADED_DELAY_TICKS,
) -> bool:
    """「新指令」是否於此 tick 送達可執行（供執行期 admit 閘門，§6.2）。

    OFFLINE → 收不到（False，指令保留待通信恢復）；DEGRADED → 延遲 N ticks 後才送達；
    ONLINE → 即時。以 issued_tick 與 now_tick 差判延遲（決定性，不用時鐘）。
    """
    delivery = command_delivery(state, degraded_delay_ticks)
    if not delivery.accepted:
        return False
    return (now_tick - issued_tick) >= delivery.delay_ticks


def parse_link_state(value: object) -> LinkState:
    """把熱狀態的 comms_state 字串轉 LinkState；缺失/非法 → ONLINE（樂觀預設，見降級哲學）。"""
    if isinstance(value, LinkState):
        return value
    try:
        return LinkState(str(value)) if value else LinkState.ONLINE
    except ValueError:
        return LinkState.ONLINE


def position_report_frozen(state: LinkState) -> bool:
    """OFFLINE 單位位置對己方 COP 凍結為最後回報點（fog of war 對己方也成立）。"""
    return state is LinkState.OFFLINE


def position_report_interval(
    state: LinkState,
    base_interval_ticks: int,
    degraded_multiplier: int = DEFAULT_DEGRADED_REPORT_MULTIPLIER,
) -> int | None:
    """位置回報間隔（ticks）。ONLINE base；DEGRADED 降頻（×倍率）；OFFLINE 凍結（None）。"""
    if state is LinkState.ONLINE:
        return base_interval_ticks
    if state is LinkState.DEGRADED:
        return base_interval_ticks * degraded_multiplier
    return None


def intel_granularity(state: LinkState) -> IntelGranularity:
    """AI/COP 收到的敵情摘要粒度：ONLINE 完整、DEGRADED 粗化、OFFLINE 凍結。"""
    if state is LinkState.ONLINE:
        return IntelGranularity.FULL
    if state is LinkState.DEGRADED:
        return IntelGranularity.COARSE
    return IntelGranularity.FROZEN
