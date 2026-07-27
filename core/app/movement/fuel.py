"""油料存取與消耗 — #84（SPEC_FULL §5.3「油料耗盡無法移動」MUST）。

**存放位置**：`EquipmentInstance.currentState["fuel"]`（SPEC_FULL §5.3 明定攜行量記於此），
與彈藥（`currentState["ammo"]`）同一模式，故不需 DB schema 變更。

**惰性滿油**：instance 尚無 `fuel` 鍵 → 視為**滿油**（取範本 `mobility.fuel_capacity`）。
如此無須額外 seed pass，且日後由裝備管理面板新增的載具自動帶滿油。

**扣油**：一個單位的自走載具視為共同油池；依各車**自身油耗比例**分攤（各車燒各車的油），
剩餘不足時扣到 0（不為負）。徒步/無油耗資料 → `needs_fuel=False`，完全不受限。

紅線：純確定性（無隨機、無牆鐘）；由移動執行器於 tick 內以既有 DB session 寫回（與位置/戰力同批）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tables import EquipmentInstance, EquipmentTemplate

_SELF_MOVE_CLASSES = ("TRACKED", "WHEELED")


@dataclass
class _Tank:
    """單一自走載具的油箱（instance + 其油耗/容量）。"""

    instance: EquipmentInstance
    burn_per_km: float
    capacity: float
    remaining: float


@dataclass
class UnitFuel:
    """一個單位的油料現況（僅計自走載具）。"""

    tanks: list[_Tank] = field(default_factory=list)

    @property
    def needs_fuel(self) -> bool:
        """是否受油料限制（有自走載具且有油耗定義）。"""
        return any(t.burn_per_km > 0 for t in self.tanks)

    @property
    def remaining(self) -> float:
        return sum(t.remaining for t in self.tanks)

    @property
    def capacity(self) -> float:
        return sum(t.capacity for t in self.tanks)

    @property
    def burn_per_km(self) -> float:
        return sum(t.burn_per_km for t in self.tanks)

    def range_km(self) -> float:
        """以現有油量還能走的公里數（無油耗＝無限，回 inf）。"""
        return self.remaining / self.burn_per_km if self.burn_per_km > 0 else float("inf")


def _mobility_of(base_stats: Any) -> dict[str, Any]:
    mob = base_stats.get("mobility") if isinstance(base_stats, dict) else None
    return mob if isinstance(mob, dict) else {}


def _num(v: Any) -> float:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else 0.0


def load_unit_fuel(db: Session, unit_id: str) -> UnitFuel:
    """讀取單位的自走載具油箱（惰性滿油）。無自走載具 → 空 UnitFuel（needs_fuel=False）。"""
    from app.movement.mobility import _fuel_burn_per_km  # 共用 per-tick→per-km 換算

    rows = db.execute(
        select(EquipmentInstance, EquipmentTemplate.base_stats)
        .join(EquipmentTemplate, EquipmentTemplate.id == EquipmentInstance.template_id)
        .where(EquipmentInstance.owner_id == unit_id)
    ).all()
    tanks: list[_Tank] = []
    for inst, base_stats in rows:
        mob = _mobility_of(base_stats)
        if not mob.get("can_self_move") or mob.get("mobility_class") not in _SELF_MOVE_CLASSES:
            continue
        capacity = _num(mob.get("fuel_capacity"))
        burn = _fuel_burn_per_km(mob, _num(mob.get("max_cross_country_speed_kmh")))
        if burn <= 0 and capacity <= 0:
            continue  # 無油料模型的載具 → 不納入（不限制其移動）
        state = inst.current_state if isinstance(inst.current_state, dict) else {}
        raw = state.get("fuel")
        # 惰性滿油：尚無 fuel 鍵 → 視為滿油。
        remaining = _num(raw) if isinstance(raw, (int, float)) else capacity
        # 每 instance 代表 N 件同型裝備（#30 quantity）→ 油量/油耗按數量放大。
        qty = max(1, int(inst.quantity or 1))
        tanks.append(
            _Tank(
                instance=inst,
                burn_per_km=burn * qty,
                capacity=capacity * qty,
                remaining=min(remaining, capacity * qty) if capacity > 0 else remaining,
            )
        )
    return UnitFuel(tanks=tanks)


def burn_fuel(fuel: UnitFuel, distance_km: float) -> float:
    """依行進距離扣油（各車燒各車的油），**寫回 instance.current_state**。回實際扣除總量。

    油不足時扣到 0（不為負）——呼叫端據 `fuel.remaining <= 0` 判定油盡停駛。
    """
    if distance_km <= 0 or not fuel.needs_fuel:
        return 0.0
    burned = 0.0
    for t in fuel.tanks:
        if t.burn_per_km <= 0 or t.remaining <= 0:
            continue
        want = t.burn_per_km * distance_km
        take = min(want, t.remaining)
        t.remaining -= take
        burned += take
        state = t.instance.current_state if isinstance(t.instance.current_state, dict) else {}
        t.instance.current_state = {**state, "fuel": round(t.remaining, 4)}
    return burned


# ---------------- #85 補給（加油） ----------------

_CARGO_FUEL_KEY = "cargo_fuel"


def refuel(fuel: UnitFuel, amount: float) -> float:
    """把 `amount` 注入單位油箱（各車按缺口比例補到滿為止），寫回 DB。回實際加注量。"""
    if amount <= 0:
        return 0.0
    added = 0.0
    for t in fuel.tanks:
        if amount - added <= 0:
            break
        room = max(0.0, t.capacity - t.remaining)
        take = min(room, amount - added)
        if take <= 0:
            continue
        t.remaining += take
        added += take
        state = t.instance.current_state if isinstance(t.instance.current_state, dict) else {}
        t.instance.current_state = {**state, "fuel": round(t.remaining, 4)}
    return added


@dataclass
class SupplyCargo:
    """一個補給單位所載的油料（LOGISTICS 裝備的 capacity.FUEL；惰性滿載）。"""

    instances: list[tuple[EquipmentInstance, float]] = field(default_factory=list)  # (inst, 容量)
    rate_per_tick: float = 0.0

    @property
    def remaining(self) -> float:
        return sum(
            _num((i.current_state or {}).get(_CARGO_FUEL_KEY, cap)) for i, cap in self.instances
        )

    @property
    def has_fuel_cargo(self) -> bool:
        return bool(self.instances)

    def draw(self, amount: float) -> float:
        """自載運油料中提取（不足則提可提之量），寫回 DB。回實際提取量。"""
        if amount <= 0:
            return 0.0
        drawn = 0.0
        for inst, cap in self.instances:
            if amount - drawn <= 0:
                break
            state = inst.current_state if isinstance(inst.current_state, dict) else {}
            have = _num(state.get(_CARGO_FUEL_KEY, cap))
            take = min(have, amount - drawn)
            if take <= 0:
                continue
            drawn += take
            inst.current_state = {**state, _CARGO_FUEL_KEY: round(have - take, 4)}
        return drawn


def load_supply_cargo(db: Session, unit_id: str) -> SupplyCargo:
    """讀補給單位的載運油料（category=LOGISTICS 且 capacity.FUEL>0；惰性滿載）。"""
    rows = db.execute(
        select(EquipmentInstance, EquipmentTemplate.base_stats)
        .join(EquipmentTemplate, EquipmentTemplate.id == EquipmentInstance.template_id)
        .where(EquipmentInstance.owner_id == unit_id)
    ).all()
    out: list[tuple[EquipmentInstance, float]] = []
    rate = 0.0
    for inst, base_stats in rows:
        log = base_stats.get("logistics") if isinstance(base_stats, dict) else None
        if not isinstance(log, dict):
            continue
        cap_block = log.get("capacity")
        cap = _num(cap_block.get("FUEL")) if isinstance(cap_block, dict) else 0.0
        if cap <= 0:
            continue
        qty = max(1, int(inst.quantity or 1))
        out.append((inst, cap * qty))
        rate += _num(log.get("resupply_rate_per_tick")) * qty
    return SupplyCargo(instances=out, rate_per_tick=rate)
