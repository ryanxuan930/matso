"""單位機動能力導出 — #80 Phase A（SPEC_MOVEMENT §2.1/§2.2）。

由單位的編裝（`EquipmentInstance → EquipmentTemplate.base_stats["mobility"]`）導出其**機動 profile**
（TRACKED / WHEELED / FOOT）與速度（越野/道路 km/h）。取代執行器單一常數 40 km/h。

規則（SPEC §2.1，取「最能自走」者）：
- 有 ≥1 件 `can_self_move=true` 且 `mobility_class=TRACKED` 的裝備 → TRACKED（含搭載步兵的履帶車）。
- 否則有 WHEELED 自走裝備 → WHEELED。
- 否則（僅人攜/無自走載具）→ FOOT（速度取 params）。
- 同級多車：取**最慢**（車隊受最慢者限制，才能保持隊形）。

紅線：純讀 DB、無牆鐘/隨機；為確定性導出值（可快取，重算即可）。海空 profile（BOAT/AIR）為後續。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tables import EquipmentInstance, EquipmentTemplate
from app.movement.params import (
    FOOT_ROAD_KMH,
    FOOT_XC_KMH,
    MOVE_TICK_RATE_MS,
    TEMPO_SPEED_FACTOR,
)

_MS_PER_H = 3_600_000.0
_SELF_MOVE_CLASSES = ("TRACKED", "WHEELED")  # 優先序（前者較優先）


@dataclass(frozen=True, slots=True)
class UnitMobility:
    """單位機動能力（導出值）。"""

    profile: str  # FOOT / WHEELED / TRACKED（BOAT/AIR 後續）
    road_kmh: float
    xc_kmh: float
    # #84 油料：本單位自走載具的合計每公里油耗 / 滿油量（徒步皆 0 → 不受油料限制）。
    fuel_burn_per_km: float = 0.0
    fuel_capacity: float = 0.0

    @property
    def needs_fuel(self) -> bool:
        """是否受油料限制（有自走載具且有油耗定義）。徒步/無油耗資料 → 不限制。"""
        return self.fuel_burn_per_km > 0.0

    def speed_kmh(self, *, on_road: bool = False, tempo: str = "NORMAL") -> float:
        """有效速度（km/h）：道路/越野 × 節奏倍率（地形/坡度修正於 Phase B）。"""
        base = self.road_kmh if on_road else self.xc_kmh
        return base * TEMPO_SPEED_FACTOR.get(tempo, 1.0)

    def step_km(
        self, tick_rate_ms: int = MOVE_TICK_RATE_MS, *, on_road: bool = False, tempo: str = "NORMAL"
    ) -> float:
        """每 tick 前進距離（km）＝有效速度 × tick 時長。"""
        return self.speed_kmh(on_road=on_road, tempo=tempo) * tick_rate_ms / _MS_PER_H


FOOT = UnitMobility(profile="FOOT", road_kmh=FOOT_ROAD_KMH, xc_kmh=FOOT_XC_KMH)


def foot_mobility(xc_kmh: float | None = None, road_kmh: float | None = None) -> UnitMobility:
    """徒步機動（#93 可調）。未給 → 用 params 的預設，與過去完全相同。"""
    if xc_kmh is None and road_kmh is None:
        return FOOT
    return UnitMobility(
        profile="FOOT",
        road_kmh=road_kmh if road_kmh is not None else FOOT_ROAD_KMH,
        xc_kmh=xc_kmh if xc_kmh is not None else FOOT_XC_KMH,
    )


def mobility_from_stats(
    stats_list: list[dict[str, Any]],
    *,
    foot_xc_kmh: float | None = None,
    foot_road_kmh: float | None = None,
) -> UnitMobility:
    """由一組裝備 base_stats 導出機動能力（純函數，供批次/單筆共用）。無自走載具 → FOOT。

    `foot_*`（#93）：徒步基準速度覆寫；未給則用 params 預設（行為與過去相同）。
    """
    self_movers: list[dict[str, Any]] = []
    for stats in stats_list:
        mob = stats.get("mobility") if isinstance(stats, dict) else None
        if not isinstance(mob, dict):
            continue
        if mob.get("can_self_move") and mob.get("mobility_class") in _SELF_MOVE_CLASSES:
            self_movers.append(mob)
    if not self_movers:
        return foot_mobility(foot_xc_kmh, foot_road_kmh)
    # 取最優先可用等級（TRACKED 優先於 WHEELED）。
    chosen = next(
        cls for cls in _SELF_MOVE_CLASSES if any(m["mobility_class"] == cls for m in self_movers)
    )
    pool = [m for m in self_movers if m["mobility_class"] == chosen]
    # 車隊受最慢者限制。
    road = min(float(m.get("max_road_speed_kmh") or 0.0) for m in pool)
    xc = min(float(m.get("max_cross_country_speed_kmh") or 0.0) for m in pool)
    # 速度資料缺漏（seed 不全）→ 退回 FOOT，避免 0 速度卡死。
    if road <= 0.0 and xc <= 0.0:
        return foot_mobility(foot_xc_kmh, foot_road_kmh)
    # #84 油料：同 profile 的自走載具**合計**油耗與滿油量（整個車隊一起燒油）。
    burn = sum(_fuel_burn_per_km(m, xc) for m in pool)
    capacity = sum(float(m.get("fuel_capacity") or 0.0) for m in pool)
    return UnitMobility(
        profile=chosen,
        road_kmh=road or xc,
        xc_kmh=xc or road,
        fuel_burn_per_km=burn,
        fuel_capacity=capacity,
    )


def _fuel_burn_per_km(mob: dict[str, Any], xc_kmh: float) -> float:
    """每公里油耗：優先 `fuel_burn_per_km`；僅有舊 `fuel_burn_per_tick` 時以越野速度換算。

    換算理由（#81 起）：速度隨地形變動 → 每 tick 行進距離不再固定，per-tick 油耗不再有物理意義；
    以「該車越野速度下 1 tick 走的公里數」把舊資料轉為 per-km（fuel/tick ÷ km/tick）。
    """
    per_km = mob.get("fuel_burn_per_km")
    if isinstance(per_km, (int, float)) and per_km > 0:
        return float(per_km)
    per_tick = mob.get("fuel_burn_per_tick")
    if isinstance(per_tick, (int, float)) and per_tick > 0 and xc_kmh > 0:
        km_per_tick = xc_kmh * MOVE_TICK_RATE_MS / _MS_PER_H
        if km_per_tick > 0:
            return float(per_tick) / km_per_tick
    return 0.0


def _stats_for_units(db: Session, unit_ids: list[str]) -> dict[str, list[dict[str, Any]]]:
    """批次查一組單位的裝備 base_stats（owner_id → [base_stats, …]），避免 N+1。"""
    out: dict[str, list[dict[str, Any]]] = {uid: [] for uid in unit_ids}
    if not unit_ids:
        return out
    rows = db.execute(
        select(EquipmentInstance.owner_id, EquipmentTemplate.base_stats)
        .join(EquipmentTemplate, EquipmentTemplate.id == EquipmentInstance.template_id)
        .where(EquipmentInstance.owner_id.in_(unit_ids))
    ).all()
    for owner_id, base_stats in rows:
        if isinstance(base_stats, dict):
            out.setdefault(owner_id, []).append(base_stats)
    return out


def resolve_unit_mobility(
    db: Session,
    unit_id: str,
    *,
    foot_xc_kmh: float | None = None,
    foot_road_kmh: float | None = None,
) -> UnitMobility:
    """單一單位的機動能力（由其編裝導出）。無自走載具/無編裝 → FOOT。"""
    return mobility_from_stats(
        _stats_for_units(db, [unit_id]).get(unit_id, []),
        foot_xc_kmh=foot_xc_kmh,
        foot_road_kmh=foot_road_kmh,
    )


def resolve_session_mobility(
    db: Session,
    unit_ids: list[str],
    *,
    foot_xc_kmh: float | None = None,
    foot_road_kmh: float | None = None,
) -> dict[str, UnitMobility]:
    """批次導出多單位機動能力（供 AI context load_unit_meta，一次查詢）。"""
    stats = _stats_for_units(db, unit_ids)
    return {
        uid: mobility_from_stats(sl, foot_xc_kmh=foot_xc_kmh, foot_road_kmh=foot_road_kmh)
        for uid, sl in stats.items()
    }
