"""補給類別與消耗（WP-C7.1）——純同步純函數（紅線 2）。

[JTLS-F p.1058] Class I–X 與再訂購水位；[JCATS-A p.26–27]「**絕非申請後直接恢復戰力**」。

## V2 只做四個類別，而其中兩個已經存在

| 類別 | 內容 | 現況 |
|------|------|------|
| I | 口糧/水 | **本卡新增** |
| III | 油料 | 已有（#84，走 `movement/fuel.py`） |
| V | 彈藥 | 已有（#44，走 per-weapon ammo） |
| IX | 維修件 | **本卡新增**（消費端在 C7.3 修復） |

⚠ **不把 III/V 搬進來**。它們各自已經有能用的模型與測試；為了「類別體系整齊」而重寫，
換到的是一次大改與一輪回歸風險，換不到任何行為。本模組只補真正缺的兩個，
並提供一份**共用的水位語義**（`level_of` / `is_below_reorder`）讓四個類別都能用同一套判斷。

## 中性預設：每日消耗率 0

`DAILY_CONSUMPTION` 的預設全是 **0.0**——既有局不會憑空開始餓肚子。要讓 Class I 真的消耗，
想定/SimParams 必須主動給值。這與 C1/C3/C4 的紀律相同：**加保真與不破壞既有局是解耦的**。

## 斷糧是階梯不是懸崖

`starvation_modifier` 回的是效能倍率，隨斷補天數階梯下降而不是一次歸零。
[JCATS-A p.26–27] 的重點正是「補給不足是逐步失能」——一刀切成 0 會讓後勤變成一個開關，
而開關沒有可供指揮官權衡的中間狀態。
"""

from __future__ import annotations

import enum
from dataclasses import dataclass


class SupplyClass(enum.StrEnum):
    """V2 做的四個補給類別（北約 Class 編號）。"""

    I = "I"  # noqa: E741 — 北約類別編號就是羅馬數字 I，改名會與準則脫節
    III = "III"
    V = "V"
    IX = "IX"


# 本模組**自己管**的類別。III/V 有既有模型，只借用水位語義。
OWNED_CLASSES = frozenset({SupplyClass.I, SupplyClass.IX})

# 每模擬日消耗（單位：份/人/日 的抽象量）。**預設全 0＝既有局不受影響**。
DAILY_CONSUMPTION: dict[SupplyClass, float] = {
    SupplyClass.I: 0.0,
    SupplyClass.IX: 0.0,
}

# 低於此比例即視為需要再訂購（WP-C7.2 的觸發線）。
REORDER_LEVEL = 0.3

# 斷糧效能階梯：{斷補天數下限: 效能倍率}。**0 天＝1.0（中性）**。
# 數字的重點不是精準，是讓「斷補」有一個指揮官感覺得到、但還來得及補救的斜率。
STARVATION_STEPS: tuple[tuple[float, float], ...] = (
    (0.0, 1.0),
    (1.0, 0.9),
    (2.0, 0.75),
    (3.0, 0.5),
    (5.0, 0.25),
)


@dataclass(frozen=True, slots=True)
class SupplyLevel:
    """某類別的存量與容量。`capacity <= 0` ＝**未編制該類別**（不是「空的」）。"""

    on_hand: float = 0.0
    capacity: float = 0.0

    @property
    def declared(self) -> bool:
        """有沒有編制這個類別。未編制的類別不消耗、不觸發再訂購、不扣效能。"""
        return self.capacity > 0.0

    @property
    def fraction(self) -> float:
        return 0.0 if self.capacity <= 0.0 else max(0.0, min(1.0, self.on_hand / self.capacity))


def daily_consumption(supply_class: SupplyClass, rates: dict[str, float] | None = None) -> float:
    """該類別的每模擬日消耗率。`rates` 由想定/SimParams 覆寫；未給 → 預設（0.0）。"""
    if rates and supply_class.value in rates:
        return max(0.0, float(rates[supply_class.value]))
    return DAILY_CONSUMPTION.get(supply_class, 0.0)


def consume(
    level: SupplyLevel, rate_per_day: float, elapsed_ticks: int, tick_rate_ms: int
) -> SupplyLevel:
    """依經過時間扣存量。未編制/零消耗率 → **原物件回傳**（呼叫端因此可以整段跳過）。"""
    if not level.declared or rate_per_day <= 0.0 or elapsed_ticks <= 0:
        return level
    days = elapsed_ticks * tick_rate_ms / 86_400_000
    return SupplyLevel(max(0.0, level.on_hand - rate_per_day * days), level.capacity)


def is_below_reorder(level: SupplyLevel, threshold: float = REORDER_LEVEL) -> bool:
    """是否低於再訂購水位。**未編制的類別永遠不觸發**——否則每個單位都會為它沒有的
    東西不斷申請補給。"""
    return level.declared and level.fraction < threshold


def starvation_modifier(days_without: float) -> float:
    """斷補 N 個模擬日後的效能倍率。**階梯不是懸崖**（見模組說明）。

    `days_without <= 0` → 1.0，所以既有局（沒有斷補概念）位元不變。
    """
    result = 1.0
    for threshold, modifier in STARVATION_STEPS:
        if days_without >= threshold:
            result = modifier
    return result


__all__ = [
    "DAILY_CONSUMPTION",
    "OWNED_CLASSES",
    "REORDER_LEVEL",
    "STARVATION_STEPS",
    "SupplyClass",
    "SupplyLevel",
    "consume",
    "daily_consumption",
    "is_below_reorder",
    "starvation_modifier",
]
