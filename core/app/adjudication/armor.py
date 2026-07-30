"""單位裝甲級別——**由編裝導出**，不是單位自己宣告的欄位。

## 這個模組在補什麼洞

`engage_wiring.seed_combat_state` 過去讀的是 `TacticalUnit.attributes["armor_class"]`，
但**沒有任何想定 schema 定義那個欄位、loader 也從不寫 `attributes`**——
於是「缺鍵 → `"INFANTRY"`」這條退路變成了實際上的唯一路徑：
從想定載進來的每一個單位（含主戰車）都以步兵裝甲被裁決，
被步槍以 `pk=0.70` 命中（`SEED_WEAPONS` 的 `pk_by_armor_class`）。
活 DB 實測 44 個單位只有 5 個帶這個鍵。

真正的資料**一直都在**：`SEED_VEHICLES["MBT"]["armor_class"] == "ARMOR"`，
掛在**裝備範本**的 `base_stats` 上。缺的只是「單位 → 編裝 → 裝甲級別」這一步。
本模組就是那一步，做法與 `movement/mobility.py` 由編裝導出機動能力完全同構。

## 為什麼取「最強的那一件」

一個單位可能同時編有主戰車與吉普車。目前的裁決模型**每個單位只有一個 armor_class**，
所以必須挑一個：
- 取最弱 → 主戰車連被步槍打死，正是我們要修的那個 bug。
- 取最強 → 以「主要平台」代表整個單位。

取最強。代價是**混編單位會被高估**（機步連的下車步兵也享有 IFV 的防護），
這是既有聚合粒度的限制而不是本模組引入的——真正的解法是逐平台的目標編成組成
（TASKS「#48 P5 目標編成組成 + 多目標火力分配」），那是另一張卡。

## 明示優先於導出

`attributes["armor_class"]` 仍然**優先**：想定作者明確寫了就照他寫的算。
導出只是在沒人明講時給出一個比「一律步兵」正確得多的答案。
"""

from __future__ import annotations

from typing import Any

# 由弱到強。`ARMOR` 最強——挑「最強的那一件」時用這個序。
ARMOR_RANK: dict[str, int] = {"INFANTRY": 0, "LIGHT_VEHICLE": 1, "ARMOR": 2}

# 沒有任何載具 → 步兵。這是**中性預設**：既有的純步兵單位行為完全不變。
DEFAULT_ARMOR_CLASS = "INFANTRY"


def armor_class_from_stats(stats_list: list[dict[str, Any]]) -> str:
    """由一組裝備 `base_stats` 導出裝甲級別（純函數）。

    無載具/無編裝 → `INFANTRY`。認不得的值一律忽略（不讓髒資料把整個單位變成無敵）。
    """
    best = DEFAULT_ARMOR_CLASS
    for stats in stats_list:
        if not isinstance(stats, dict):
            continue
        raw = stats.get("armor_class")
        if not isinstance(raw, str):
            continue
        value = raw.strip().upper()
        if value not in ARMOR_RANK:
            continue  # 認不得就當沒看到——比猜一個級別安全
        if ARMOR_RANK[value] > ARMOR_RANK[best]:
            best = value
    return best


def resolve_units_armor_class(db: Any, unit_ids: list[str]) -> dict[str, str]:
    """批次導出多個單位的裝甲級別（owner_id → armor_class）。

    `seed_combat_state` 會對整局每個單位跑一次，所以這裡**一次查完**——
    逐單位查會是 N+1，開局播狀態時特別明顯。
    """
    from app.movement.mobility import _stats_for_units

    stats = _stats_for_units(db, unit_ids)
    return {uid: armor_class_from_stats(stats.get(uid, [])) for uid in unit_ids}


__all__ = [
    "ARMOR_RANK",
    "DEFAULT_ARMOR_CLASS",
    "armor_class_from_stats",
    "resolve_units_armor_class",
]
