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
    interval_raw = raw.get("sensor_interval_ticks")
    try:
        interval = int(interval_raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        interval = DEFAULTS.sensor_interval_ticks
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
    }


def load_sim_params(db: Session) -> SimParams:
    """由 DB 單例讀推演參數。查無設定 → 全預設（＝原硬編碼行為）。"""
    from app.models.tables import SystemConfiguration

    row = db.query(SystemConfiguration).first()
    if row is None:
        return SimParams()
    config = row.integration_config if isinstance(row.integration_config, dict) else {}
    return parse_sim_params(config.get(_CONFIG_KEY))
