"""乘駐車與隊形（WP-C3）——純同步純函數（紅線 2）。

[JCATS-A p.12,25]：**Mount 是操作要點**——單兵未上車行軍速率過慢，一個機步連
下車走跟上車開差了近一個數量級。p.7,26：五種隊形影響受損與火力發揚。

## 兩個正交的軸

- **乘駐車**（`mounted`）：決定用誰的速度（載具 vs 徒步）、以及被打到時傷亡怎麼算。
- **隊形**（`formation`）：決定行軍速度倍率、受彈暴露、火力正面。

兩者相乘。一個 COLUMN 隊形的乘車連走得最快、挨砲最慘；LINE 下車連火力最全、走得最慢。

## 中性預設

`formation` 缺鍵讀作 COLUMN**且 COLUMN 的三個係數都是 1.0**；
`mounted` 缺鍵讀作 **`None`（從未宣告）而不是 `False`**，係數一律 1.0
——既有局的位元完全不動。這與 WP-C1 的紀律相同：加保真與不破壞既有局是解耦的。

⚠ COLUMN 當中性值是刻意的：它是「沒有特別展開」的預設隊形，而不是「最好的隊形」。
把 LINE 當 1.0 會讓所有既有局憑空獲得火力加成。
"""

from __future__ import annotations

import enum
from dataclasses import dataclass


class Formation(enum.StrEnum):
    """[JCATS-A p.7,26] 的五種隊形。"""

    COLUMN = "COLUMN"  # 縱隊：行軍快、正面窄、挨砲慘
    LINE = "LINE"  # 橫隊：火力全開、機動慢
    WEDGE = "WEDGE"  # 楔形：攻擊隊形，火力與機動的折衷
    VEE = "VEE"  # V 形：預期正面接敵
    HERRINGBONE = "HERRINGBONE"  # 魚骨：行軍暫停時的環形警戒


@dataclass(frozen=True, slots=True)
class FormationCoeffs:
    """一種隊形的三個係數。

    `march_speed_mult`：行軍速度倍率。
    `exposure_mult`：**面殺傷**的暴露倍率（砲擊/空襲）——縱隊擠在一條線上最慘。
    `fire_frontage_mult`：可發揚火力的正面倍率（直射命中率修正）。
    """

    march_speed_mult: float
    exposure_mult: float
    fire_frontage_mult: float


# v0 校準值。**COLUMN 全 1.0** ＝中性預設（見模組說明）。
FORMATION_COEFFS: dict[Formation, FormationCoeffs] = {
    Formation.COLUMN: FormationCoeffs(1.0, 1.0, 1.0),
    # 橫隊：正面最寬，火力全額，但機動最慢、散開後面殺傷吃得少。
    Formation.LINE: FormationCoeffs(0.6, 0.7, 1.3),
    Formation.WEDGE: FormationCoeffs(0.8, 0.8, 1.15),
    Formation.VEE: FormationCoeffs(0.75, 0.8, 1.2),
    # 魚骨是**停下來**的警戒隊形——不是行軍隊形，故速度倍率極低。
    Formation.HERRINGBONE: FormationCoeffs(0.3, 0.75, 1.0),
}

# 乘車 vs 下車的被命中暴露。**乘車目標更大**（車比人大）；下車受彈面小。
# 規格明列 dismounted target modifier × 0.8。
#
# ⚠ **`mounted` 是三態，不是布林**：`None` ＝從未宣告（既有局），必須是 1.0。
# 把 `None` 當 False 的話，既有局的每一次交戰都憑空吃到 0.8——
# **那是我第一版真的做錯的事**：所有既有局的命中率無聲下降 20%。
# golden 抓不到（沒有一個案例跑直射交戰），交戰單元測試也抓不到
# （它們直接建 `EnvSnapshot`，用的是欄位預設 1.0）——錯在**接線**那一層。
MOUNTED_EXPOSURE = 1.0
DISMOUNTED_EXPOSURE = 0.8
UNDECLARED_EXPOSURE = 1.0  # 未宣告＝維持原狀

# 載具毀損 → 乘員傷亡折算（[JTLS-F p.1058]）。
# 車被打掉時，車上的人**不是全滅也不是沒事**——這個係數是那一刀。
CREW_CASUALTY_FRACTION = 0.5

# 乘車射擊的火力折減（車內射孔受限）。
MOUNTED_FIRE_PENALTY = 0.7


def formation_of(raw: object) -> Formation:
    """字串 → 隊形。認不得/缺值一律 COLUMN（中性）——裁決層不因資料髒而爆掉。"""
    try:
        return Formation(str(raw)) if raw else Formation.COLUMN
    except ValueError:
        return Formation.COLUMN


def coeffs_of(formation: Formation) -> FormationCoeffs:
    return FORMATION_COEFFS.get(formation, FORMATION_COEFFS[Formation.COLUMN])


def march_speed_modifier(formation: Formation) -> float:
    return coeffs_of(formation).march_speed_mult


def area_exposure_modifier(formation: Formation) -> float:
    """面殺傷的暴露倍率。**大於 1 代表更慘**——與 posture 的「小於 1 代表更安全」方向相反，
    故命名為 exposure 而非 modifier，避免呼叫端把兩者當成同一種東西相乘錯方向。"""
    return coeffs_of(formation).exposure_mult


def direct_fire_target_modifier(formation: Formation, mounted: bool | None) -> float:
    """目標被直射命中的修正（乘進 `EnvSnapshot.target_exposure_modifier`）。

    隊形的正面倍率**不進這裡**——那是射手能發揚多少火力，不是目標多好打。
    兩者放同一個數字會讓「我方展開成橫隊」同時變成「敵人比較好打我」。

    `mounted=None`（未宣告）→ 1.0。見 `DISMOUNTED_EXPOSURE` 的警語。
    """
    if mounted is None:
        exposure = UNDECLARED_EXPOSURE
    else:
        exposure = MOUNTED_EXPOSURE if mounted else DISMOUNTED_EXPOSURE
    return exposure * coeffs_of(formation).exposure_mult


def shooter_frontage_modifier(formation: Formation, mounted: bool | None) -> float:
    """射手可發揚的火力正面。**乘車時打不出全額火力**（車內射擊受限）。

    `mounted=None`（未宣告）→ 不套乘車折減。同 `direct_fire_target_modifier` 的理由。
    """
    base = coeffs_of(formation).fire_frontage_mult
    return base * MOUNTED_FIRE_PENALTY if mounted else base


def crew_casualties(vehicle_loss: float, fraction: float = CREW_CASUALTY_FRACTION) -> float:
    """載具毀損 → 乘員傷亡（[JTLS-F p.1058]）。"""
    return max(0.0, vehicle_loss) * max(0.0, fraction)


__all__ = [
    "CREW_CASUALTY_FRACTION",
    "DISMOUNTED_EXPOSURE",
    "FORMATION_COEFFS",
    "MOUNTED_EXPOSURE",
    "MOUNTED_FIRE_PENALTY",
    "UNDECLARED_EXPOSURE",
    "Formation",
    "FormationCoeffs",
    "area_exposure_modifier",
    "coeffs_of",
    "crew_casualties",
    "direct_fire_target_modifier",
    "formation_of",
    "march_speed_modifier",
    "shooter_frontage_modifier",
]
