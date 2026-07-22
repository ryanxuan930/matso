"""通訊與電磁模組（SPEC §6）。

- consequences（O5.4）：§6.2 通訊狀態的戰術後果（指令傳遞 / 位置回報 / 敵情粒度）+ CommsState。
- link_budget（#33）：§6.1 鏈路預算 + 網狀 multi-hop 連通 → 每單位鏈路狀態。

`LinkState` 為兩者共用（consequences 定義、link_budget 沿用），避免重複列舉。
"""

from app.comms.consequences import (
    DEFAULT_DEGRADED_DELAY_TICKS,
    DEFAULT_DEGRADED_REPORT_MULTIPLIER,
    CommandDelivery,
    CommsState,
    IntelGranularity,
    LinkState,
    can_receive_command,
    command_delivery,
    intel_granularity,
    position_report_frozen,
    position_report_interval,
)
from app.comms.link_budget import (
    CommsNode,
    CommsProfile,
    fspl_db,
    link_margin_db,
    link_state,
    mesh_states,
)

__all__ = [
    # consequences（O5.4）
    "DEFAULT_DEGRADED_DELAY_TICKS",
    "DEFAULT_DEGRADED_REPORT_MULTIPLIER",
    "CommandDelivery",
    # link_budget（#33）
    "CommsNode",
    "CommsProfile",
    "CommsState",
    "IntelGranularity",
    "LinkState",
    "can_receive_command",
    "command_delivery",
    "fspl_db",
    "intel_granularity",
    "link_margin_db",
    "link_state",
    "mesh_states",
    "position_report_frozen",
    "position_report_interval",
]
