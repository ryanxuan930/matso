"""補給執行子系統 — #85（SPEC_FULL §5.3；取代 `NoOpLogisticsSystem`）。

執行 `RESUPPLY` 指令：補給單位（載 LOGISTICS 裝備、`capacity.FUEL > 0`）對**同陣營**目標單位
就近加油，每 tick 依 `resupply_rate_per_tick` 撥交，直到目標加滿或載運油料用罄。

與 #84 對稱：油料存 `EquipmentInstance.currentState`（目標的 `fuel`、補給車的 `cargo_fuel`），
故無 DB schema 變更。這讓 #84 拋錨的單位**可被救回**（加完油重下 MOVE 令即可再動）。

紅線：純確定性（無隨機、無牆鐘）；Kernel 於 tick 內呼叫 `consume()`，DB 寫入與其他子系統同批。
"""

from __future__ import annotations

import asyncio

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.engine.clock import SimTime
from app.engine.movement import _haversine_km
from app.models import Order, OrderStatus, TacticalUnit
from app.movement.fuel import load_supply_cargo, load_unit_fuel, refuel
from app.state.hot_state import HotStateStore
from app.state.ledger import LedgerEvent

# 補給作業距離（km）：補給車須開到受補單位附近才能撥交。
RESUPPLY_RANGE_KM = 2.0
# 無 `resupply_rate_per_tick` 定義時的預設撥交速率（油量單位/tick）。
_DEFAULT_RATE = 200.0


class ResupplySystem:
    """滿足 Kernel 的 `LogisticsSystem` 介面：每 tick 推進 RESUPPLY 指令。"""

    def __init__(
        self,
        *,
        session_id: str,
        session_factory: sessionmaker,  # type: ignore[type-arg]
        hot_state: HotStateStore,
    ) -> None:
        self._session_id = session_id
        self._session_factory = session_factory
        self._hot_state = hot_state

    async def consume(self, now: SimTime) -> list[LedgerEvent]:
        return await asyncio.to_thread(self._consume_sync, now)

    def _consume_sync(self, now: SimTime) -> list[LedgerEvent]:
        events: list[LedgerEvent] = []
        with self._session_factory() as db:
            orders = (
                db.execute(
                    select(Order).where(
                        Order.session_id == self._session_id,
                        Order.order_type == "RESUPPLY",
                        Order.status.in_([OrderStatus.VALIDATED, OrderStatus.EXECUTING]),
                    )
                )
                .scalars()
                .all()
            )
            for o in orders:
                ev = self._step_order(db, o, now)
                if ev is not None:
                    events.append(ev)
            db.commit()
        return events

    def _fail(self, o: Order, now: SimTime, reason: str, **extra: object) -> LedgerEvent:
        o.status = OrderStatus.COMPLETED  # 結束本令（同 MOVE_BLOCKED/MOVE_HALTED_FUEL 機制）
        return LedgerEvent(
            event_type="RESUPPLY_FAILED",
            tick=now.tick,
            initiator_id=o.unit_id,
            detail={"order_id": o.id, "reason": reason, **extra},
        )

    def _step_order(self, db: Session, o: Order, now: SimTime) -> LedgerEvent | None:
        payload = o.payload or {}
        target_id = payload.get("target_unit_id")
        supplier = db.get(TacticalUnit, o.unit_id)
        target = db.get(TacticalUnit, target_id) if isinstance(target_id, str) else None
        if supplier is None or target is None or target.session_id != self._session_id:
            return self._fail(o, now, "NO_TARGET")
        if supplier.faction != target.faction:
            return self._fail(o, now, "NOT_SAME_FACTION")  # 只補自軍

        # 距離閘門：補給車須就近（未到位不算失敗，等它開過去；由 MOVE 令自行接近）。
        coords = (
            supplier.current_lat,
            supplier.current_lng,
            target.current_lat,
            target.current_lng,
        )
        if None in coords:
            return self._fail(o, now, "NO_POSITION")
        dist = _haversine_km(
            float(supplier.current_lat or 0.0),
            float(supplier.current_lng or 0.0),
            float(target.current_lat or 0.0),
            float(target.current_lng or 0.0),
        )
        if dist > RESUPPLY_RANGE_KM:
            if o.status == OrderStatus.VALIDATED:
                o.status = OrderStatus.EXECUTING  # 標記已受理，等補給車接近
            return None

        cargo = load_supply_cargo(db, o.unit_id)
        if not cargo.has_fuel_cargo:
            return self._fail(o, now, "NOT_A_SUPPLY_UNIT")
        fuel = load_unit_fuel(db, target.id)
        if not fuel.tanks:
            return self._fail(o, now, "TARGET_NEEDS_NO_FUEL", target_unit_id=target.id)

        room = fuel.capacity - fuel.remaining
        if room <= 0:
            o.status = OrderStatus.COMPLETED
            return LedgerEvent(
                event_type="RESUPPLY_COMPLETED",
                tick=now.tick,
                initiator_id=o.unit_id,
                target_id=target.id,
                detail={"order_id": o.id, "reason": "TARGET_FULL", "transferred": 0.0},
            )
        if cargo.remaining <= 0:
            return self._fail(o, now, "CARGO_EMPTY", target_unit_id=target.id)

        rate = cargo.rate_per_tick if cargo.rate_per_tick > 0 else _DEFAULT_RATE
        drawn = cargo.draw(min(rate, room))
        added = refuel(fuel, drawn)
        self._hot_state.update_unit(target.id, {"fuel": round(fuel.remaining, 2)})
        o.status = OrderStatus.EXECUTING

        filled = fuel.capacity - fuel.remaining <= 1e-6
        if filled or cargo.remaining <= 0:
            o.status = OrderStatus.COMPLETED
            return LedgerEvent(
                event_type="RESUPPLY_COMPLETED",
                tick=now.tick,
                initiator_id=o.unit_id,
                target_id=target.id,
                detail={
                    "order_id": o.id,
                    "reason": "TARGET_FULL" if filled else "CARGO_EMPTY",
                    "transferred": round(added, 2),
                    "target_fuel": round(fuel.remaining, 2),
                },
            )
        return LedgerEvent(
            event_type="RESUPPLY_TICK",
            tick=now.tick,
            initiator_id=o.unit_id,
            target_id=target.id,
            detail={
                "order_id": o.id,
                "transferred": round(added, 2),
                "target_fuel": round(fuel.remaining, 2),
                "cargo_remaining": round(cargo.remaining, 2),
            },
        )
