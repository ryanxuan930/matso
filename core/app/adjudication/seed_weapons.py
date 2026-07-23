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
        "kinetic_kind": "SMALL_ARMS",
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
        "kinetic_kind": "AUTOCANNON",
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
        "kinetic_kind": "ATGM",
        "guided": True,
        "ph_by_range_band": [[500, 0.90], [2000, 0.80], [4000, 0.60]],
        "damage_by_armor_class": {"INFANTRY": 50, "LIGHT_VEHICLE": 90, "ARMOR": 80},
        "pk_by_armor_class": {"INFANTRY": 0.50, "LIGHT_VEHICLE": 0.90, "ARMOR": 0.80},
        "ammo_types": ["MISSILE_AT"],
        "rate_per_tick": 1,
    },
    "TANK_MAIN_GUN_120": {
        "max_range_m": 4000,
        "min_range_m": 0,
        "indirect_fire": False,
        "kinetic_kind": "TANK_MAIN_GUN",
        "penetration_type": "KE",
        "ph_by_range_band": [[1000, 0.90], [2500, 0.70], [4000, 0.45]],
        "damage_by_armor_class": {"INFANTRY": 40, "LIGHT_VEHICLE": 85, "ARMOR": 95},
        "pk_by_armor_class": {"INFANTRY": 0.40, "LIGHT_VEHICLE": 0.85, "ARMOR": 0.90},
        "ammo_types": ["AMMO_120_APFSDS", "AMMO_120_HEAT"],
        "rate_per_tick": 1,
    },
}

# 火砲類（間瞄）種子——category=ARTILLERY。allOf kinetic + 火砲諸元（散布/致命半徑/自走機動）。
SEED_ARTILLERY: dict[str, dict[str, Any]] = {
    "MORTAR_120": {
        "artillery_kind": "MORTAR",
        "indirect_fire": True,
        "max_range_m": 7000,
        "min_range_m": 200,
        "ph_by_range_band": [[3000, 0.55], [7000, 0.35]],
        "damage_by_armor_class": {"INFANTRY": 70, "LIGHT_VEHICLE": 40, "ARMOR": 5},
        "pk_by_armor_class": {"INFANTRY": 0.55, "LIGHT_VEHICLE": 0.25, "ARMOR": 0.02},
        "ammo_types": ["AMMO_120_HE", "AMMO_120_SMOKE"],
        "dispersion_cep_m": 90,
        "lethal_radius_m": 35,
        "rounds_per_mission": 6,
        "reload_ticks": 1,
        "emplace_ticks": 1,
        "mobility": {"can_self_move": False, "mobility_class": "MAN_PORTABLE"},
    },
    "HOWITZER_155_SP": {
        "artillery_kind": "SP_GUN",
        "indirect_fire": True,
        "max_range_m": 30000,
        "min_range_m": 2000,
        "ph_by_range_band": [[15000, 0.50], [30000, 0.30]],
        "damage_by_armor_class": {"INFANTRY": 85, "LIGHT_VEHICLE": 60, "ARMOR": 20},
        "pk_by_armor_class": {"INFANTRY": 0.70, "LIGHT_VEHICLE": 0.40, "ARMOR": 0.08},
        "ammo_types": ["AMMO_155_HE", "AMMO_155_DPICM"],
        "dispersion_cep_m": 150,
        "lethal_radius_m": 60,
        "rounds_per_mission": 8,
        "reload_ticks": 2,
        "emplace_ticks": 0,
        "mobility": {
            "can_self_move": True,
            "mobility_class": "TRACKED",
            "max_road_speed_kmh": 60,
            "max_cross_country_speed_kmh": 30,
        },
    },
    "MLRS_227": {
        "artillery_kind": "MLRS",
        "indirect_fire": True,
        "max_range_m": 70000,
        "min_range_m": 8000,
        "ph_by_range_band": [[40000, 0.45], [70000, 0.30]],
        "damage_by_armor_class": {"INFANTRY": 90, "LIGHT_VEHICLE": 75, "ARMOR": 35},
        "pk_by_armor_class": {"INFANTRY": 0.80, "LIGHT_VEHICLE": 0.55, "ARMOR": 0.15},
        "ammo_types": ["ROCKET_227_HE"],
        "dispersion_cep_m": 200,
        "lethal_radius_m": 120,
        "rounds_per_mission": 12,
        "reload_ticks": 6,
        "emplace_ticks": 1,
        "mobility": {
            "can_self_move": True,
            "mobility_class": "WHEELED",
            "max_road_speed_kmh": 85,
            "max_cross_country_speed_kmh": 40,
        },
    },
}

# 載具類種子——category=VEHICLE。組員/載員 + 各面裝甲 + 機動性。
SEED_VEHICLES: dict[str, dict[str, Any]] = {
    "IFV_TRACKED": {
        "crew": 3,
        "passenger_capacity": 7,
        "armor_class": "LIGHT_VEHICLE",
        "armor_by_aspect_mm": {"front": 40, "side": 25, "rear": 20, "top": 15},
        "mobility": {
            "can_self_move": True,
            "mobility_class": "TRACKED",
            "max_road_speed_kmh": 65,
            "max_cross_country_speed_kmh": 40,
            "fuel_burn_per_tick": 0.5,
        },
    },
    "MBT": {
        "crew": 4,
        "passenger_capacity": 0,
        "armor_class": "ARMOR",
        "armor_by_aspect_mm": {"front": 600, "side": 300, "rear": 60, "top": 40},
        "mobility": {
            "can_self_move": True,
            "mobility_class": "TRACKED",
            "max_road_speed_kmh": 70,
            "max_cross_country_speed_kmh": 45,
            "fuel_burn_per_tick": 0.8,
        },
    },
}
