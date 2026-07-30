"""友軍誤傷語意（WP-C9）——純同步純函數（紅線 2）。

[JCATS-A p.5–6]：成熟系統「命令照輸入執行、後果照裁定」——**錯誤的火力計畫打到自己的
補給點，照裁**。系統的公正性來自它不替下令者遮羞，而不是來自它擋住每一個蠢命令。

## 三條路徑本來就不對稱（動手前查證的結果）

| 路徑 | 誤傷現況 |
|------|----------|
| `ENGAGE`（直射） | 預檢的 ROE 分支擋住非敵對目標 |
| `FIRE_MISSION`（面射擊） | **完全沒有陣營檢查**——打自己人今天就會產生真實傷亡 |
| `MISSION` | 只透過它派生的 ENGAGE 子令間接受擋 |

只把開關接進 ENGAGE 的預檢，等於做出一個「開了才會誤傷」的假象——面射擊本來就會。
所以本模組把「誰算友軍」與「這個令要不要擋」拆成兩個問題，兩條路徑用同一份答案。

## 面射擊**不受開關影響**，那是刻意的

規格明寫「區域武器（砲兵 HE）的濺射本就該傷及半徑內友軍」。砲彈不會挑陣營，
把面射擊也關掉會讓「攻擊準備射擊落短」這種最經典的誤傷情境變成不可能發生。
開關管的是**故意瞄準友軍**，不是**砲彈落在友軍身上**。

## NEUTRAL 不在開關的範圍內

預檢原本那條是 `not is_hostile(...)`，一個分支同時涵蓋自己陣營、ALLIED 與 NEUTRAL。
`allow_fratricide` 只該打開前兩者——「攻擊中立方」是另一件完全不同的事
（那是戰略決定，不是訓練用的誤傷情境），把它一起放行是無聲的範圍擴張。
"""

from __future__ import annotations

from app.factions.relations import FactionRelations, Relation

# 開關打開時可以射擊的關係。**NEUTRAL 不在內**（見模組說明）。
FRATRICIDE_RELATIONS = frozenset({Relation.ALLIED})


def is_friendly(relations: FactionRelations, shooter: str, other: str) -> bool:
    """`other` 是不是 `shooter` 的友軍（自己陣營或盟軍）。

    ⚠ **不要用 `a == b` 字串比較**。`area_fire` 原本就是那樣判 `friendly_losses` 的，
    於是聯軍誤傷（BLUE 打到 GREEN 盟軍）不會被標成友軍傷亡——AAR 上看起來像正常戰果。
    同陣營與盟軍在誤傷語義上是同一件事，關係矩陣才是唯一的判準。
    """
    return relations.relation(shooter, other) is Relation.ALLIED


def blocks_engagement(
    relations: FactionRelations, shooter: str, target: str, *, allow_fratricide: bool
) -> tuple[bool, str]:
    """直射交戰的 ROE 判定。回 (要不要擋, 原因)。

    - 敵對 → 放行。
    - 中立 → **一律擋**，開關管不到（見模組說明）。
    - 自己陣營/盟軍 → 開關決定。

    開關打開時回 `(False, 警語)`：**不是靜靜放行**。呼叫端要把那句話送到下令者面前，
    因為這條路徑存在的意義就是「你確定嗎」，不是「隨便你」。
    """
    relation = relations.relation(shooter, target)
    if relation is Relation.HOSTILE:
        return False, ""
    if relation is not Relation.ALLIED:
        # NEUTRAL（或未來新增的關係值）：開關不涵蓋。
        return True, f"目標陣營關係為 {relation.value}，非敵對，禁止交戰"
    if not allow_fratricide:
        return True, f"目標陣營關係為 {relation.value}，非敵對，禁止交戰"
    return False, (
        f"⚠ 友軍誤傷：目標陣營關係為 {relation.value}。本局允許誤傷裁決，此令將照常執行並記入 AAR"
    )


def fratricide_victims(
    relations: FactionRelations, shooter_faction: str, losses_by_faction: dict[str, float]
) -> dict[str, float]:
    """從逐單位/逐陣營戰損裡挑出**友軍**的部分。

    輸入是 {陣營: 戰損}，回同型但只留友軍。空 dict ＝這次射擊沒有誤傷。

    ## 現況：生產環境零呼叫端（2026-07-30 查證）

    誤傷歸帳現在由 `area_fire.py` 在裁決當下做完——它以逐**單位**的量綱篩出友軍受害者，
    寫進 `AREA_FIRE_RESOLVED` 的 `ai_decision["friendly_losses"]`，
    `engine/fire_wiring.py` 只讀那個鍵並展開成獨立的 FRATRICIDE 事件。
    本函式是那條路線定案前的另一種切法（事後拿「陣營→戰損」再篩一次）。

    **留著不刪**：`test_fratricide.py` 用它釘住兩條語義——「0 戰損不算受害」與
    「盟軍算友軍」（`is_friendly` 而非字串相等）。這兩條正是 `area_fire` 曾經踩過的坑。
    要清掉請連同那支測試一起處理，不要單獨刪函式。
    """
    return {
        faction: loss
        for faction, loss in losses_by_faction.items()
        if loss > 0 and is_friendly(relations, shooter_faction, faction)
    }


__all__ = [
    "FRATRICIDE_RELATIONS",
    "blocks_engagement",
    "fratricide_victims",
    "is_friendly",
]
