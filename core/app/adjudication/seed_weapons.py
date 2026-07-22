"""種子 KINETIC 武器模板（O3.2）——EquipmentTemplate.baseStats 格式（kinetic $def）。

**v0 佔位值**（同 mobility_matrix：由想定/校準覆寫）。每筆對 weaponeering.schema.json 驗證
（見測試）。ph_by_range_band 依 range 遞增、base_ph 非遞增（供 P_hit 對距離的單調性）。

裝甲類別：INFANTRY / LIGHT_VEHICLE / ARMOR。
"""

from __future__ import annotations

from typing import Any

# name → EquipmentTemplate.baseStats（category=KINETIC）
SEED_WEAPONS: dict[str, dict[str, Any]] = {
    "RIFLE_556": {
        "max_range_m": 600,
        "min_range_m": 0,
        "indirect_fire": False,
        "ph_by_range_band": [[100, 0.80], [300, 0.50], [600, 0.20]],
        "damage_by_armor_class": {"INFANTRY": 35, "LIGHT_VEHICLE": 5, "ARMOR": 0},
        "pk_by_armor_class": {"INFANTRY": 0.70, "LIGHT_VEHICLE": 0.10, "ARMOR": 0.0},
        "ammo_types": ["AMMO_556"],
        "rate_per_tick": 3,
    },
    "AUTOCANNON_30": {
        "max_range_m": 3000,
        "min_range_m": 0,
        "indirect_fire": False,
        "ph_by_range_band": [[500, 0.70], [1500, 0.50], [3000, 0.25]],
        "damage_by_armor_class": {"INFANTRY": 60, "LIGHT_VEHICLE": 45, "ARMOR": 15},
        "pk_by_armor_class": {"INFANTRY": 0.80, "LIGHT_VEHICLE": 0.50, "ARMOR": 0.15},
        "ammo_types": ["AMMO_30MM"],
        "rate_per_tick": 2,
    },
    "ATGM": {
        "max_range_m": 4000,
        "min_range_m": 200,
        "indirect_fire": False,
        "ph_by_range_band": [[500, 0.90], [2000, 0.80], [4000, 0.60]],
        "damage_by_armor_class": {"INFANTRY": 50, "LIGHT_VEHICLE": 90, "ARMOR": 80},
        "pk_by_armor_class": {"INFANTRY": 0.50, "LIGHT_VEHICLE": 0.90, "ARMOR": 0.80},
        "ammo_types": ["MISSILE_AT"],
        "rate_per_tick": 1,
    },
}
