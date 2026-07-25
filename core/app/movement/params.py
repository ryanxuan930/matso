"""移動參數（單一真相）——預覽端（API）與執行端（sim_runtime）共用，確保估計與實跑一致。"""

from __future__ import annotations

# sim time：1 分 / tick（與 sim_runtime._TICK_RATE_MS 對齊）。
MOVE_TICK_RATE_MS: int = 60_000
# 單位地面移動速度（公里/時）——**後備值**：無法由編裝導出機動時的預設（見 movement/mobility.py）。
MOVE_SPEED_KMH: float = 40.0

# --- #80 Phase A：per-unit 機動速度 + 行軍耗損（SPEC_MOVEMENT §2.2/§2.4） ---
# 徒步單位基準速度（km/h）：無自走載具的單位。越野/沿道路（道路加速待 Phase C 接道路網）。
FOOT_XC_KMH: float = 5.0
FOOT_ROAD_KMH: float = 6.5

# 行軍耗損：每公里基礎磨耗（戰力點/km），乘地形難度（Phase A 固定 1.0）與節奏 tempo。
# 徒步與機械化磨耗率不同（人員行軍疲勞 > 車輛機件磨耗，於此以係數表達）。
MARCH_ATTRITION_PER_KM: dict[str, float] = {
    "FOOT": 0.05,  # 100 km 徒步 → ~5 戰力點（疲勞/掉隊）
    "WHEELED": 0.02,
    "TRACKED": 0.03,
}
_MARCH_ATTRITION_DEFAULT: float = 0.03

# 行軍節奏：速度倍率 × 磨耗倍率（強行軍更快但更耗）。
TEMPO_SPEED_FACTOR: dict[str, float] = {"NORMAL": 1.0, "FORCED_MARCH": 1.5}
TEMPO_ATTRITION_FACTOR: dict[str, float] = {"NORMAL": 1.0, "FORCED_MARCH": 2.5}


def march_attrition_per_km(profile: str) -> float:
    """該機動 profile 的每公里基礎磨耗（戰力點/km）。"""
    return MARCH_ATTRITION_PER_KM.get(profile, _MARCH_ATTRITION_DEFAULT)
