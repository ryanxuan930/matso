"""Kernel 裁決接線（O3.6）——把 VALIDATED ENGAGE 指令接上純函數裁決引擎。

- `EngageOrderSource`（OrderSource）：drain VALIDATED ENGAGE → EngageCommand，轉 EXECUTING。
- `EngagementAdjudicator`（Adjudicator）：resolve 每筆 → 呼叫 resolve_engagement（O3.2，純函數）
  → 依結果更新熱狀態（彈藥 −1、目標血量）→ 轉 COMPLETED → 回事件。

**紅線**：物理裁決仍是純函數；本層只做 I/O 邊界（讀熱狀態、寫回、狀態轉移），AI 不介入。
武器/環境（射程、LOS、天氣係數）以 callable 注入——由 Kernel 事先收集（terrain client + O5）。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adjudication.engagement import (
    EngagementResult,
    EnvSnapshot,
    Resolution,
    Shooter,
    Target,
    resolve_engagement,
)
from app.adjudication.weapon import WeaponProfile
from app.engine.clock import SimTime
from app.engine.rng import DeterministicRNG
from app.models.enums import OrderStatus
from app.models.tables import Order
from app.orders.schemas import OrderType
from app.orders.state_machine import next_status
from app.state.hot_state import HotStateStore
from app.state.ledger import LedgerEvent

WeaponLookup = Callable[[str], WeaponProfile]
EngageEnvLookup = Callable[[str, str], EnvSnapshot]  # (shooter_id, target_id) → EnvSnapshot


@dataclass(frozen=True, slots=True)
class EngageCommand:
    order_id: str
    shooter_id: str
    target_id: str


class EngageOrderSource:
    """從 DB 拉本 session 的 VALIDATED ENGAGE 指令並轉 EXECUTING（確定性排序）。"""

    def __init__(self, db: Session, session_id: str) -> None:
        self._db = db
        self._session_id = session_id

    async def drain(self) -> list[EngageCommand]:
        orders = self._db.scalars(
            select(Order)
            .where(
                Order.session_id == self._session_id,
                Order.status == OrderStatus.VALIDATED,
                Order.order_type == OrderType.ENGAGE.value,
            )
            .order_by(Order.issued_at_tick, Order.id)
        ).all()
        commands: list[EngageCommand] = []
        for order in orders:
            order.status = next_status(order.status, OrderStatus.EXECUTING)
            commands.append(
                EngageCommand(
                    order_id=order.id,
                    shooter_id=order.unit_id,
                    target_id=str(order.payload["target_unit_id"]),
                )
            )
        self._db.commit()
        return commands


class EngagementAdjudicator:
    """把 EngageCommand 交給純函數裁決引擎，並把結果落到熱狀態 + order 狀態。"""

    def __init__(
        self,
        db: Session,
        hot_state: HotStateStore,
        rng: DeterministicRNG,
        weapon_for: WeaponLookup,
        env_for: EngageEnvLookup,
    ) -> None:
        self._db = db
        self._hot = hot_state
        self._rng = rng
        self._weapon_for = weapon_for
        self._env_for = env_for

    def resolve(self, order: EngageCommand, now: SimTime) -> list[LedgerEvent]:
        shooter_state = self._hot.get_unit(order.shooter_id)
        target_state = self._hot.get_unit(order.target_id)
        if shooter_state is None or target_state is None:
            self._complete(order.order_id, now.tick)
            return []

        weapon = self._weapon_for(order.shooter_id)
        env = self._env_for(order.shooter_id, order.target_id)
        result = resolve_engagement(
            weapon,
            Shooter(unit_id=order.shooter_id, ammo_count=int(shooter_state.get("ammo", 0))),
            Target(
                unit_id=order.target_id,
                armor_class=str(target_state.get("armor_class", "INFANTRY")),
                health=float(target_state.get("health", 100.0)),
            ),
            env,
            self._rng,
            now.tick,
        )
        self._apply(order, result)
        self._complete(order.order_id, now.tick)
        return result.events

    def _apply(self, order: EngageCommand, result: EngagementResult) -> None:
        if result.status is Resolution.REJECTED:
            return  # 合法性未過（彈藥/射程/LOS）→ 不消耗、不變更
        ammo = int(self._hot.get_unit(order.shooter_id).get("ammo", 0))  # type: ignore[union-attr]
        self._hot.update_unit(order.shooter_id, {"ammo": max(0, ammo - 1)})
        if result.status is Resolution.HIT:
            self._hot.update_unit(order.target_id, {"health": result.target_health_after})

    def _complete(self, order_id: str, tick: int) -> None:
        order = self._db.get(Order, order_id)
        if order is None:
            return
        order.status = next_status(order.status, OrderStatus.COMPLETED)
        order.resolved_at_tick = tick
        self._db.commit()
