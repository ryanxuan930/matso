"""編制規模——**由編制層級導出**，不是等別人來填的欄位。

## 這個模組在補什麼洞

`cp_per_platform = authorized_strength / platform_count` 是整個「漸進消耗」模型的分母：
大部隊每次命中只損失一個平台份量，單體則整個承受。

但 `platform_count` **全系統沒有任何寫入端**：
- `loader.py` 建 `TacticalUnit` 時不寫 `attributes`、不寫 `personnel_current`
- `contracts/orbat.schema.json` 是 `additionalProperties: false` 且**沒有這個欄位**
- 前端也沒有任何地方寫它

於是每個從想定載入的單位都退回預設值 **1**，`cp_per_platform` 變成
`authorized / 1 = 100`。以出貨種子 RIFLE_556（`pk=0.70`）為例：
**一發步槍命中滿編步兵連就扣掉 70 戰力，兩發全連覆滅。**
真實化交戰 Phase 1–4 的核心在生產資料上完全不生效。

**而所有測試都是綠的**——因為每一條交戰測試都自己手塞 `platform_count`
（9/10/14/120），生產路徑的預設值 1 一次都沒被走到。這與 `armor_class` 是同一個病
（見 `adjudication/armor.py`），但後果更嚴重。

## 修法：導出，而不是要求別人填

想定裡本來就有 `unit_level`。與其等 ORBAT 契約補一個沒人會填的欄位，
不如**由編制導出一個合理的預設**，並保留明示覆寫：
`attributes.platform_count` > `personnel_current` > 依編制導出。

⚠ `PERSONNEL_BY_LEVEL` 是**通用編制常識，不是量測值**。想定若有精確的編裝資料，
這張表是第一個該被取代的東西——正確做法是讓 ORBAT 直接宣告人數。
"""

from __future__ import annotations

from typing import Any

# 各編制的典型人數（輕步兵 TO&E 量級）。⚠ 假設值，見模組說明。
PERSONNEL_BY_LEVEL: dict[str, int] = {
    "INDIVIDUAL": 1,
    "FIRETEAM": 4,
    "SQUAD": 9,
    "SECTION": 16,
    "PLATOON": 30,
    "COMPANY": 120,
    "BATTALION": 500,
    "REGIMENT": 1500,
    "BRIGADE": 3500,
    "DIVISION": 12000,
    "CORPS": 45000,
    "ARMY": 120000,
    "ARMY_GROUP": 300000,
    "THEATER": 500000,
}
# 認不得的編制 → 排級。**不是 1**——1 代表「單體」，那正是要修掉的錯誤預設。
DEFAULT_PLATFORM_COUNT = PERSONNEL_BY_LEVEL["PLATOON"]


def platform_count_for(
    unit_level: object, attributes: Any = None, personnel_current: object = None
) -> int:
    """單位的平台/建制數。**明示優先於導出**：

    1. `attributes.platform_count`——想定作者明確寫了就照他寫的算。
    2. `personnel_current`——編裝資料有人數就用人數。
    3. 依 `unit_level` 導出（本模組的重點；過去這一格是寫死的 `1`）。
    """
    if isinstance(attributes, dict):
        raw = attributes.get("platform_count")
        if isinstance(raw, (int, float)) and raw >= 1:
            return int(raw)
    if isinstance(personnel_current, int) and personnel_current >= 1:
        return personnel_current
    if isinstance(unit_level, str):
        return PERSONNEL_BY_LEVEL.get(unit_level.strip().upper(), DEFAULT_PLATFORM_COUNT)
    return DEFAULT_PLATFORM_COUNT


__all__ = ["DEFAULT_PLATFORM_COUNT", "PERSONNEL_BY_LEVEL", "platform_count_for"]
