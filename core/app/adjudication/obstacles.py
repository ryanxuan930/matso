"""障礙工事的裁決語意（WP-C2）——純同步純函數（紅線 2）。

[JCATS-A p.5–6] 的「公正性」範例有一半在講障礙：雷區阻機動、斷橋改道、爆破需合理工時。

## 動手前先查證：規格說「完全無視」並不精確

SPEC_V2 寫「移動 A* 與交戰完全無視它」。**實際上不是**——`movement/attrition.py` 的
`classify_crossings` + `_apply_forced_attrition`（#28）已經讓「強穿阻礙」付出隨機額外耗損。

真正缺的是**型別語意**：對引擎而言，一片雷區與一圈鐵絲網是同一個東西。
沒有觸雷、沒有減速、沒有破障、沒有工兵。本模組補的是這一層。

## 中性預設：沒有 `obstacle_type` 的既有標註行為完全不變

既有局的 `MapFeature` 都沒有 `attributes.obstacle_type`。`effect_of(None)` 回**全中性**
（速度倍率 1.0、觸雷機率 0），於是那些標註仍然只走既有的強穿耗損路徑——一個位元都不差。

⚠ 這條要**在接線層測**，不是在這裡測。純函數的預設參數天生就是中性的；
會出事的是「接線怎麼把 `attributes` 翻譯成型別」——WP-C3 就是在那一層栽的
（`mounted` 缺鍵被 `bool()` 收成 False，讓既有局命中率無聲掉了 20%）。
"""

from __future__ import annotations

import enum
import math
from dataclasses import dataclass

# 換算函式**共用 WP-C1 那一份**而不是再寫一個：兩份同義的「一 tick 幾分鐘」必然漂開，
# 而這正是本卡要修的那種 bug 的溫床。suppression 不 import 本模組，無循環。
from app.adjudication.suppression import DEFAULT_TICK_RATE_MS, minutes_per_tick


class ObstacleType(enum.StrEnum):
    """障礙型別。`MapFeature.attributes.obstacle_type` 的合法值。"""

    MINEFIELD = "MINEFIELD"  # 雷區：觸雷造成戰損 + 停止 + 壓制
    WIRE = "WIRE"  # 鐵絲網：非工兵幾乎過不去
    TANK_DITCH = "TANK_DITCH"  # 戰車壕：同上，對輪履更嚴重
    ABATIS = "ABATIS"  # 鹿砦（伐木障礙）
    BRIDGE_DEMO = "BRIDGE_DEMO"  # 斷橋：道路加速失效


@dataclass(frozen=True, slots=True)
class ObstacleEffect:
    """一種障礙對通過者的效果。

    `speed_mult`：通過時的速度倍率（1.0 ＝不減速）。
    `mine_strike_p_per_km`：每公里觸雷機率（僅雷區 > 0）。
    `breach_time_minutes`：工兵破障所需的**模擬分鐘數**（實際 tick 數見 `breach_ticks()`）。
    """

    speed_mult: float
    mine_strike_p_per_km: float
    breach_time_minutes: int


# **全中性**：沒有宣告型別的既有標註走這一份（見模組說明）。
NEUTRAL_EFFECT = ObstacleEffect(speed_mult=1.0, mine_strike_p_per_km=0.0, breach_time_minutes=0)

# v0 校準值。破障工時參考 [JCATS-A p.13]「工事構築須符合實際工時」——
# 這些數字的重點不是精準，是**讓破障成為一個要付出時間的決定**而不是一個按鈕。
#
# ⚠ 第三欄的單位曾經是「tick」，註解寫死「1 tick = 1 分鐘」——與 WP-C1 的掘壕工時
# 同一個 bug（commit d67fe61 修了那一半，這一半漏掉）。`tick_rate_ms` 是想定可調的，
# 官方 demo 與使用者的想定都寫 1000（1 tick ＝ 1 模擬秒），於是破一片雷區只要 45 **秒**、
# 炸一座橋 2 分鐘——「要付出時間的決定」退化回一個按鈕，整個工兵子系統形同不存在。
#
# 現在存的是**分鐘**，實際 tick 數由 `breach_ticks()` 依該局的 tick 長度換算。
OBSTACLE_EFFECTS: dict[ObstacleType, ObstacleEffect] = {
    # 雷區不減速——它靠的是傷亡與心理效果，不是物理阻擋。
    ObstacleType.MINEFIELD: ObstacleEffect(1.0, 0.35, 45),
    # 鐵絲網/戰車壕是**實質阻擋**：規格明列非工兵單位速度 × 0.1。
    ObstacleType.WIRE: ObstacleEffect(0.1, 0.0, 20),
    ObstacleType.TANK_DITCH: ObstacleEffect(0.1, 0.0, 60),
    ObstacleType.ABATIS: ObstacleEffect(0.25, 0.0, 30),
    # 斷橋不「減速」——它讓道路加速失效並強迫涉水，那是路徑層的事（見 `blocks_road`）。
    ObstacleType.BRIDGE_DEMO: ObstacleEffect(1.0, 0.0, 120),
}

# 工兵通過障礙的優勢（規格：工兵通過機率減半）。
ENGINEER_MINE_STRIKE_MULT = 0.5
# 工兵不受鐵絲網/戰車壕的實質阻擋（他們有破障器材）。
ENGINEER_SPEED_MULT = 1.0

# 觸雷的後果。**停止 + 壓制**是重點——雷區真正的價值是把進攻縱隊釘在原地。
MINE_STRIKE_STRENGTH_LOSS = 3.0
MINE_STRIKE_SUPPRESSION = 0.5


def obstacle_type_of(raw: object) -> ObstacleType | None:
    """`attributes.obstacle_type` → 型別。

    **缺值/認不得一律回 `None`（未宣告）而不是某個預設型別**——
    回 MINEFIELD 之類的話，既有局每一片沒標型別的障礙都會突然開始炸人。
    """
    if not raw:
        return None
    try:
        return ObstacleType(str(raw))
    except ValueError:
        return None


def effect_of(otype: ObstacleType | None) -> ObstacleEffect:
    """型別 → 效果。`None`（未宣告）→ 全中性。"""
    return NEUTRAL_EFFECT if otype is None else OBSTACLE_EFFECTS[otype]


def speed_multiplier(otype: ObstacleType | None, *, is_engineer: bool, breached: bool) -> float:
    """通過該障礙的速度倍率。已破障 / 工兵 → 不減速。"""
    if breached or is_engineer:
        return ENGINEER_SPEED_MULT
    return effect_of(otype).speed_mult


def mine_strike_probability(
    otype: ObstacleType | None,
    distance_km: float,
    *,
    is_engineer: bool,
    breached: bool,
    density: float = 1.0,
    p_per_km: float | None = None,
    engineer_mult: float | None = None,
) -> float:
    """本段行程的觸雷機率。夾在 [0, 1]。

    `density` 是雷區密度倍率（`attributes.density`），預設 1.0。
    `p_per_km` / `engineer_mult` 為 None → 用出貨常數（既有行為位元不變）；
    活執行期由該局的 `SimParams` 傳入——**過去這兩個係數在 SimParams 裡但沒有讀取端**。
    """
    if breached or otype is not ObstacleType.MINEFIELD:
        return 0.0
    base = effect_of(otype).mine_strike_p_per_km if p_per_km is None else max(0.0, p_per_km)
    p = base * max(0.0, distance_km) * max(0.0, density)
    if is_engineer:
        p *= ENGINEER_MINE_STRIKE_MULT if engineer_mult is None else max(0.0, engineer_mult)
    return min(1.0, max(0.0, p))


def breach_minutes(otype: ObstacleType | None) -> int:
    """破障所需的**模擬分鐘數**。未宣告型別 → 0（沒有東西可破）。"""
    return effect_of(otype).breach_time_minutes


def breach_ticks(otype: ObstacleType | None, tick_rate_ms: int = DEFAULT_TICK_RATE_MS) -> int:
    """破障所需 tick（依該局的 tick 長度換算）。未宣告型別 → 0（沒有東西可破）。

    **無條件進位且至少 1 tick**（除非本來就沒東西可破）：破障是「工作」不是「宣告」，
    tick 再長也不能讓它在下令的同一個 tick 完工——那就回到按鈕了。

    預設 `tick_rate_ms` 等同 1 分鐘/tick，故不傳的呼叫端拿到的仍是表上那個數字。
    """
    minutes = breach_minutes(otype)
    if minutes <= 0:
        return 0
    return max(1, math.ceil(minutes / minutes_per_tick(tick_rate_ms)))


def blocks_road(otype: ObstacleType | None, *, breached: bool) -> bool:
    """是否讓道路加速失效（斷橋）。"""
    return not breached and otype is ObstacleType.BRIDGE_DEMO


__all__ = [
    "DEFAULT_TICK_RATE_MS",
    "ENGINEER_MINE_STRIKE_MULT",
    "ENGINEER_SPEED_MULT",
    "MINE_STRIKE_STRENGTH_LOSS",
    "MINE_STRIKE_SUPPRESSION",
    "NEUTRAL_EFFECT",
    "OBSTACLE_EFFECTS",
    "ObstacleEffect",
    "ObstacleType",
    "blocks_road",
    "breach_minutes",
    "breach_ticks",
    "effect_of",
    "mine_strike_probability",
    "obstacle_type_of",
    "speed_multiplier",
]
