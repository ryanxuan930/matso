"""推演參數（#93 P1）——可由系統設定頁調整的**兵推行為**參數。

存於 `SystemConfiguration.integrationConfig["sim"]`（JSON 欄位 → 免 migration），
與 AI/LLM 設定同一個 DB 單例。

## 三條紀律

1. **預設值＝原本的模組常數**：未設定（欄位不存在/為空）時 `SimParams()` 與硬編碼行為**位元相同**，
   故既有推演局、既有測試、**golden replay 全不受影響**。
2. **預覽與執行讀同一份**：`movement/params.py` 的註解寫著「單一真相——預覽端與執行端共用，
   確保估計與實跑一致」。若只讓執行端可調，預覽就會與實跑不一致——那正是 SPEC_MOVEMENT
   當初要消滅的 bug。故本模組同時餵給 `api/movement`（預覽）與 `engine/movement`（執行）。
3. **不做全域可變狀態**：以明確傳遞的 dataclass 承載，不用 module-level 可變 dict。
   讀取點固定（runner 啟動 / 每次預覽請求），行為可預測、可測試。

## 生效時機
執行端於 **session runner 啟動時**讀取一次 → **進行中的推演局不受影響**（改設定不會在半場
改變物理規則）；要套用新值需該局重跑（封存/複製）。預覽端每次請求讀取，故預覽會**立刻**
反映新值——這是刻意的：預覽本就是「如果現在下令會怎樣」。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from app.adjudication import formation as _formation
from app.adjudication import obstacles as _obs
from app.adjudication import suppression as _sup
from app.engine import refit_wiring as _refit
from app.engine import weather_wiring as _wx
from app.movement import params as _mp

_CONFIG_KEY = "sim"


def _positive(raw: Any, fallback: float) -> float:
    """設定值 → 正數；非數/非正/壞值一律退回預設（壞設定不該讓整個推演跑不動）。"""
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return fallback
    return value if value > 0 else fallback


def _non_negative(raw: Any, fallback: float) -> float:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return fallback
    return value if value >= 0 else fallback


@dataclass(frozen=True, slots=True)
class SimParams:
    """一份推演參數快照。欄位預設＝`movement/params.py` 等處的既有常數。"""

    # --- 移動速度（km/h）---
    foot_xc_kmh: float = _mp.FOOT_XC_KMH
    foot_road_kmh: float = _mp.FOOT_ROAD_KMH
    vehicle_fallback_kmh: float = _mp.MOVE_SPEED_KMH
    # --- 行軍耗損（戰力點/km，乘 tempo）---
    march_attrition: dict[str, float] = field(
        default_factory=lambda: dict(_mp.MARCH_ATTRITION_PER_KM)
    )
    # --- 補給 ---
    resupply_range_km: float = 2.0
    # --- 偵測 ---
    intrinsic_optical_range_m: float = 4000.0
    sensor_interval_ticks: int = 5
    # --- 節奏（R 層：runner 啟動時綁定）---
    tick_rate_ms: int = _mp.MOVE_TICK_RATE_MS
    pace_compression: float = 120.0
    comms_interval_ticks: int = 5
    # --- AI 自主推演 ---
    ai_heartbeat_s: float = 45.0
    ai_max_orders: int = 500
    # --- 壓制與姿態（WP-C1）---
    # SPEC_V2 §WP-C 的紀律：保真係數 MUST 進 SimParams，預設＝中性/現況，
    # 讓「加保真」與「不破壞既有局」解耦。
    suppression_decay: float = _sup.SUPPRESSION_DECAY
    suppression_fire_penalty: float = _sup.SUPPRESSION_FIRE_PENALTY
    suppression_move_penalty: float = _sup.SUPPRESSION_MOVE_PENALTY
    # --- 乘駐車與隊形（WP-C3）---
    # 載具毀損 → 乘員傷亡折算（[JTLS-F p.1058]）。車被打掉時車上的人不是全滅也不是沒事。
    crew_casualty_fraction: float = _formation.CREW_CASUALTY_FRACTION
    # 下車的受彈面（乘車＝1.0 為基準）。
    dismounted_exposure: float = _formation.DISMOUNTED_EXPOSURE
    # --- 障礙與工兵（WP-C2）---
    # 每公里觸雷機率（雷區）。**這是本卡唯一會擲骰的係數**——調它等於調雷區的殺傷力。
    mine_strike_p_per_km: float = _obs.OBSTACLE_EFFECTS[
        _obs.ObstacleType.MINEFIELD
    ].mine_strike_p_per_km
    # 觸雷戰損（戰力點）。
    mine_strike_strength_loss: float = _obs.MINE_STRIKE_STRENGTH_LOSS
    # 工兵通過雷區的機率倍率（規格：減半）。
    engineer_mine_strike_mult: float = _obs.ENGINEER_MINE_STRIKE_MULT
    # --- 環境演進（WP-C4b）---
    # 天氣刷新間隔（tick）。**0 ＝永不刷新 ＝既有的「整局一份啟動快照」**，
    # 這是中性預設：既有局位元不變、golden 不必重錄。
    weather_refresh_ticks: int = _wx.DEFAULT_REFRESH_TICKS
    # --- 後勤（WP-C7.1）---
    # 每模擬日消耗率 {類別: 份/日}。**空 dict ＝全 0 ＝既有局不會憑空開始餓肚子。**
    supply_daily_rates: dict[str, float] = field(default_factory=dict)
    # WP-C7.3 每模擬日恢復的戰力點。**0 ＝不修復（中性）**——想定要主動給。
    repair_per_day: float = _refit.REPAIR_PER_DAY
    # --- 崩潰復原（WP-E1）---
    # 以 tick 計而非牆鐘秒：快照點必須落在模擬時間的確定位置（Kernel 判 `tick % interval == 0`），
    # 而牆鐘會隨 TickPacer 的過載降頻漂移。600 tick ≈ 5 分鐘牆鐘（@ 預設 0.5s/tick）。
    checkpoint_interval_ticks: int = 600

    def attrition_for(self, profile: str) -> float:
        """該機動 profile 的每公里基礎磨耗（未定義的 profile 退回 params 的預設）。"""
        return self.march_attrition.get(profile, _mp._MARCH_ATTRITION_DEFAULT)


DEFAULTS = SimParams()


def parse_sim_params(raw: object) -> SimParams:
    """`integrationConfig["sim"]` → SimParams。**任何壞值只影響該欄**，其餘照常用預設。"""
    if not isinstance(raw, dict):
        return SimParams()
    attrition = dict(_mp.MARCH_ATTRITION_PER_KM)
    raw_attr = raw.get("march_attrition")
    if isinstance(raw_attr, dict):
        for profile, default in _mp.MARCH_ATTRITION_PER_KM.items():
            attrition[profile] = _non_negative(raw_attr.get(profile), default)

    def _int(key: str, fallback: int, minimum: int = 1) -> int:
        try:
            return max(minimum, int(raw[key]))
        except (KeyError, TypeError, ValueError):
            return fallback

    interval = _int("sensor_interval_ticks", DEFAULTS.sensor_interval_ticks)
    return SimParams(
        foot_xc_kmh=_positive(raw.get("foot_xc_kmh"), DEFAULTS.foot_xc_kmh),
        foot_road_kmh=_positive(raw.get("foot_road_kmh"), DEFAULTS.foot_road_kmh),
        vehicle_fallback_kmh=_positive(
            raw.get("vehicle_fallback_kmh"), DEFAULTS.vehicle_fallback_kmh
        ),
        march_attrition=attrition,
        resupply_range_km=_positive(raw.get("resupply_range_km"), DEFAULTS.resupply_range_km),
        intrinsic_optical_range_m=_positive(
            raw.get("intrinsic_optical_range_m"), DEFAULTS.intrinsic_optical_range_m
        ),
        sensor_interval_ticks=max(1, interval),
        tick_rate_ms=_int("tick_rate_ms", DEFAULTS.tick_rate_ms, minimum=1000),
        pace_compression=_positive(raw.get("pace_compression"), DEFAULTS.pace_compression),
        comms_interval_ticks=_int("comms_interval_ticks", DEFAULTS.comms_interval_ticks),
        ai_heartbeat_s=_positive(raw.get("ai_heartbeat_s"), DEFAULTS.ai_heartbeat_s),
        ai_max_orders=_int("ai_max_orders", DEFAULTS.ai_max_orders),
        checkpoint_interval_ticks=_int(
            "checkpoint_interval_ticks", DEFAULTS.checkpoint_interval_ticks
        ),
    )


def to_config(p: SimParams) -> dict[str, Any]:
    """SimParams → 可存 JSON 的 dict（設定頁回寫用）。"""
    return {
        "foot_xc_kmh": p.foot_xc_kmh,
        "foot_road_kmh": p.foot_road_kmh,
        "vehicle_fallback_kmh": p.vehicle_fallback_kmh,
        "march_attrition": dict(p.march_attrition),
        "resupply_range_km": p.resupply_range_km,
        "intrinsic_optical_range_m": p.intrinsic_optical_range_m,
        "sensor_interval_ticks": p.sensor_interval_ticks,
        "tick_rate_ms": p.tick_rate_ms,
        "pace_compression": p.pace_compression,
        "comms_interval_ticks": p.comms_interval_ticks,
        "ai_heartbeat_s": p.ai_heartbeat_s,
        "ai_max_orders": p.ai_max_orders,
        "checkpoint_interval_ticks": p.checkpoint_interval_ticks,
    }


def load_sim_params(db: Session) -> SimParams:
    """由 DB 單例讀推演參數。查無設定 → 全預設（＝原硬編碼行為）。"""
    from app.models.tables import SystemConfiguration

    row = db.query(SystemConfiguration).first()
    if row is None:
        return SimParams()
    config = row.integration_config if isinstance(row.integration_config, dict) else {}
    return parse_sim_params(config.get(_CONFIG_KEY))
