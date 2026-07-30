"""壓制與姿態（WP-C1）——純同步純函數（紅線 2）。

**為什麼這張卡存在**：`EnvSnapshot.shooter_suppression_modifier` 與
`target_posture_modifier` 從交戰真實化時代就恆為 `1.0`——掛點早就留好，系統一直缺席。

沒有壓制，**砲兵的主要戰術功能表現不出來**：真實的火力支援多半不是為了殲滅，
是為了讓對方抬不起頭。沒有姿態，防禦與掘壕的生存優勢也表現不出來。

## 中性預設

所有係數的預設值都讓行為**與現況位元相同**：`suppression=0` → 修正 1.0；
`posture=MOVING` → 修正 1.0。既有局與 golden 因此完全不動——
「加保真」與「不破壞既有局」在這裡是解耦的（SPEC_V2 §WP-C 的紀律）。
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

# 壓制上限。1.0 ＝ 完全趴下：射擊效能剩 `1 - SUPPRESSION_FIRE_PENALTY`。
MAX_SUPPRESSION = 1.0

# 每 tick 的衰減率（乘法）。1 tick = 1 分鐘，故 0.7 ⇒ **半衰期約 2 分鐘、13 分鐘完全恢復**。
# 停火後會自己恢復，這是壓制與戰損最根本的差別：**壓制是可逆的**。
#
# 選 0.7 不是 0.85：後者要 29 分鐘才清得掉，那讓一次砲擊的壓制效果長得像戰損。
# 真實的壓制在火力一停就開始鬆動——抬頭、重新據槍是分鐘級的事。
#
# **權威在這裡**：`sim_params.py` 的 `DEFAULTS.suppression_decay` 直接引用本常數，
# golden `suppression_defense_60` 也是以 0.7 錄的。契約的說明曾寫「預設 0.85」，
# 照那個數字算的 client 會全部算錯；已對齊為 0.7，並由
# `test_contract_default_matches_the_constant` 釘住兩邊不再漂開。
SUPPRESSION_DECAY = 0.7

# 命中一次累積多少壓制。砲兵高、直射低——這正是「砲兵用來壓制、步槍用來殺傷」的模型化。
SUPPRESSION_PER_HIT: dict[str, float] = {
    "ARTILLERY": 0.35,
    "MISSILE": 0.25,
    "KINETIC": 0.10,
}
_DEFAULT_PER_HIT = 0.10

# 滿壓制時射擊效能與移動速度的折減比例。
SUPPRESSION_FIRE_PENALTY = 0.6  # 滿壓制 → 命中率剩 40%
SUPPRESSION_MOVE_PENALTY = 0.5  # 滿壓制 → 速度剩 50%（趴下的部隊走不動）


class Posture(enum.StrEnum):
    """單位姿態。**轉換要時間**——宣告掘壕不等於已經挖好。"""

    MOVING = "MOVING"
    HASTY = "HASTY"  # 臨時掩蔽（即時）
    DEFENSE = "DEFENSE"  # 準備陣地（30 分鐘）
    DUG_IN = "DUG_IN"  # 掘壕（4 小時）


# 目標姿態 → 被命中率的修正。數字是 v0 校準值（SPEC_V2 明列），可由 SimParams 調。
POSTURE_MODIFIER: dict[Posture, float] = {
    Posture.MOVING: 1.0,
    Posture.HASTY: 0.85,
    Posture.DEFENSE: 0.7,
    Posture.DUG_IN: 0.5,
}

# 轉換到該姿態所需的模擬 tick（1 tick = 1 分鐘）。
POSTURE_TICKS: dict[Posture, int] = {
    Posture.MOVING: 0,
    Posture.HASTY: 0,
    Posture.DEFENSE: 30,
    Posture.DUG_IN: 240,
}


@dataclass(frozen=True, slots=True)
class PostureState:
    """單位的姿態狀態機。`target` 是正在轉換的目標，`current` 是**已經到位**的那一級。"""

    current: Posture = Posture.MOVING
    target: Posture = Posture.MOVING
    since_tick: int = 0

    def settled(self, tick: int) -> Posture:
        """此刻**實際生效**的姿態。

        轉換未完成 → 仍算前一級。宣告掘壕的那一秒就享有掘壕的防護，
        會讓「挖工事」變成一個免費的按鈕。
        """
        if self.target is self.current:
            return self.current
        needed = POSTURE_TICKS.get(self.target, 0)
        return self.target if tick - self.since_tick >= needed else self.current

    def advance(self, tick: int) -> PostureState:
        """轉換完成就把 target 收成 current。呼叫端每 tick 呼叫一次。"""
        if self.target is not self.current and self.settled(tick) is self.target:
            return PostureState(current=self.target, target=self.target, since_tick=tick)
        return self

    def order(self, target: Posture, tick: int) -> PostureState:
        """下令改姿態。**同一個目標重複下令不重置計時**——否則反覆下令會讓工事永遠挖不完。"""
        if target is self.target:
            return self
        return PostureState(current=self.current, target=target, since_tick=tick)

    def interrupted(self, tick: int) -> PostureState:
        """單位移動了——姿態打回 MOVING，正在進行的工事作廢。挖到一半的洞帶不走。"""
        if self.current is Posture.MOVING and self.target is Posture.MOVING:
            return self
        return PostureState(current=Posture.MOVING, target=Posture.MOVING, since_tick=tick)


def add_suppression(current: float, weapon_category: str, rounds: int = 1) -> float:
    """被命中 → 壓制累積。夾在 [0, 1]。

    `rounds` ＝這次落在該單位身上的發數（面射擊一次任務可能是一輪齊放）。
    **會很快飽和，那是對的**：一個 4 發齊放落在你的陣地上，你就是抬不起頭。
    """
    delta = SUPPRESSION_PER_HIT.get(weapon_category.upper(), _DEFAULT_PER_HIT)
    return min(MAX_SUPPRESSION, max(0.0, current) + delta * max(0, rounds))


def decay_suppression(current: float, decay: float = SUPPRESSION_DECAY) -> float:
    """每 tick 衰減。**低於 0.01 直接歸零**——留一個永遠除不盡的小數只會讓熱狀態每 tick 都在變，
    每個 tick 都推一次 STATE_DIFF 給所有 client。"""
    nxt = max(0.0, current) * decay
    return 0.0 if nxt < 0.01 else nxt


def fire_modifier(suppression: float, penalty: float = SUPPRESSION_FIRE_PENALTY) -> float:
    """壓制 → 射擊效能修正（`shooter_suppression_modifier`）。無壓制回 **1.0**（位元不變）。"""
    return 1.0 - penalty * min(MAX_SUPPRESSION, max(0.0, suppression))


def move_modifier(suppression: float, penalty: float = SUPPRESSION_MOVE_PENALTY) -> float:
    """壓制 → 移動速度修正。趴下的部隊走不動。"""
    return 1.0 - penalty * min(MAX_SUPPRESSION, max(0.0, suppression))


def posture_modifier(posture: Posture) -> float:
    """目標姿態 → 被命中率修正（`target_posture_modifier`）。"""
    return POSTURE_MODIFIER.get(posture, 1.0)


__all__ = [
    "MAX_SUPPRESSION",
    "POSTURE_MODIFIER",
    "POSTURE_TICKS",
    "SUPPRESSION_DECAY",
    "SUPPRESSION_PER_HIT",
    "Posture",
    "PostureState",
    "add_suppression",
    "decay_suppression",
    "fire_modifier",
    "move_modifier",
    "posture_modifier",
]
