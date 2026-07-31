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

## 中性靠「有沒有宣告」，不靠「消耗率是不是 0」

消耗率曾經預設全 0，於是這條鏈在任何真實的一局裡一次都跑不到。**中性不該用「把物理關掉」
換取**——真正的保證在接線層而且是結構性的：熱狀態沒有 `supply` 鍵 → `read_levels` 回空 dict
→ `tick_supply` 一個鍵都不寫（見 `engine/supply_wiring`）。既有想定沒有宣告補給，
所以它們照樣位元不變；有宣告的單位則從第一個 tick 起就真的在吃飯。

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

# ## 校準錨點（2026-07-31）——存量的單位是什麼，逐類寫死在這裡以免後人用猜的
#
# | 類別 | `on_hand`/`capacity` 的單位 | 每模擬日消耗 |
# |------|---------------------------|------------|
# | I    | **補給日（DOS, days of supply）** | 1.0（**單位定義，不是自由參數**） |
# | IX   | **維修件點**，由 `refit_wiring.PARTS_PER_POINT` 定義（修 1 點戰力吃 0.5 點） | 0.5 |
# | III/V | 不在本表 | **刻意留 0**（見下） |
#
# ### Class I ＝ 1.0 DOS/日
#
# **錨點：一個連的口糧/水基本攜行量是 3 個補給日**（「三日份」是步兵編裝的通則）。
# 做法是把存量的**單位**就定義成「補給日」，於是消耗率依定義 ＝ 1.0，而想定宣告的
# `capacity` 直接讀作「這支部隊斷補之後還撐得了幾天」——那正是指揮官要看的數字，
# 也讓 `REORDER_LEVEL`（0.3）自動讀作「剩不到一天份就該叫補給」。
# 滿載 3 DOS 被切斷 → **第 3 個模擬日耗盡** → 之後才開始走 `STARVATION_STEPS` 的階梯，
# 對上 SPEC 驗收條文「斷補的裝甲連 3 模擬日後（口糧盡）效能階梯下降」。
#
# ⚠ **這個錨點是假設，不是量測**（同 `seed_weapons.SEED_ARTILLERY` 的 155mm 錨點）。
# 要調「多久餓垮」請改**想定宣告的 `capacity`**（攜行天數），**不要動這個 1.0**——
# 動了它，`capacity` 就不再讀作天數，整張表就失去參照。真要調率（例如熱帶用水加倍），
# 耗盡時間與率**嚴格反比**：率 ×2 → 耗盡時間 ÷2。
#
# ### Class IX ＝ 0.5 點/日
#
# **錨點：例行保養的料件損耗，約等於「每模擬日修回 1 點戰力」所需的量**
# （1 點 × `PARTS_PER_POINT` 0.5）。帶一個維修基數（20 點＝把 60% 修回滿編的量）的連隊，
# 光靠保養要 40 個模擬日才耗盡——數日的推演裡不會喧賓奪主，長時程演習才逼得出補給需求。
#
# ⚠ **保養與修復共用同一份 Class IX**，規劃攜行量時兩邊要一起算：想把損失的 X 點戰力修回來
# 需要 `X × PARTS_PER_POINT` **外加**整補期間每日 0.5 的保養。所以「20 點修回 60%→100%」
# 只在忽略保養時剛好夠——真的跑生產接線，20 點會在第 4 日前見底、卡在 NO_PARTS 停在 96.4。
# 要 4 日修滿得帶 22 點。`test_supply_calibration` 的整補錨點測試因此**必須**
# 與 `_supply_tick` 交錯跑，只跑 `_refit_tick` 會得到一個在真實推演裡不成立的綠燈。
# ⚠ 與 `PARTS_PER_POINT` **綁定**：那個常數改了這個要等比例跟著改，否則
# 「日常保養 ＝ 1 點戰力的料」這個錨點就不成立。
#
# ### III/V 留 0 是刻意的
#
# 油料（#84）與彈藥（#44）各有能用的模型與**各自的熱狀態鍵**。在這裡給它們非 0 值，
# 只是讓單位多養一池沒有任何消費端會讀的幽靈庫存——**同一件事有兩份帳就一定會漂**。
DAILY_CONSUMPTION: dict[SupplyClass, float] = {
    SupplyClass.I: 1.0,
    SupplyClass.IX: 0.5,
}

# LOGISTICS 的 `capacity` 與補給點的 `stock` 講的是**商品名**（`contracts/weaponeering.schema.json`
# 的 `$defs.logistics.capacity`、軍械庫 UI 的下拉、`movement/fuel.py` 的 `load_supply_cargo`
# 都是這套字彙），本模組講的是**北約 Class 編號**。兩套都不能廢：商品名已經是使用者資料庫裡
# 落地的鍵（改名要資料遷移），Class 編號則是準則語言。於是在**邊界**做一次對映，
# 而不是要求任何一邊改口——認不得的鍵原本會被**靜靜丟掉**（補給點寫 `{"FUEL": 1000}`
# 就變成空庫存、`usable` 是 False，沒有任何錯誤訊息）。
COMMODITY_ALIASES: dict[str, SupplyClass] = {
    "WATER_FOOD": SupplyClass.I,
    "FUEL": SupplyClass.III,
    "AMMO": SupplyClass.V,
    "BATTERY": SupplyClass.IX,
}


def parse_class(key: object) -> SupplyClass | None:
    """字串 → 類別。Class 編號與商品名兩套字彙都認得；認不得回 **None**（呼叫端跳過該筆）。"""
    text = str(key)
    try:
        return SupplyClass(text)
    except ValueError:
        return COMMODITY_ALIASES.get(text.upper())


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
    """該類別的每模擬日消耗率。`rates` 由想定/SimParams 覆寫；**未給 → 上面校準過的預設**
    （不是 0——`SimParams.supply_daily_rates` 的空表意思是「沒覆寫」不是「關掉」）。"""
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
    "COMMODITY_ALIASES",
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
