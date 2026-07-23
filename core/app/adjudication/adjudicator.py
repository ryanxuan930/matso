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

from app.adjudication.effectiveness import interp_effectiveness
from app.adjudication.engagement import (
    EngagementResult,
    EnvSnapshot,
    Resolution,
    Shooter,
    Target,
    resolve_engagement,
)
from app.adjudication.weapon import WeaponProfile
from app.comms import order_admissible, parse_link_state
from app.engine.clock import SimTime
from app.engine.rng import DeterministicRNG
from app.models.enums import OrderStatus
from app.models.tables import Order, TacticalUnit
from app.orders.schemas import OrderType
from app.orders.state_machine import next_status
from app.state.hot_state import HotStateStore
from app.state.ledger import LedgerEvent

# (shooter_id, target_id, indirect_fire) → EnvSnapshot（indirect_fire 供天氣散佈修正區分直/間瞄）
EngageEnvLookup = Callable[[str, str, bool], EnvSnapshot]


@dataclass(frozen=True, slots=True)
class EngageCommand:
    order_id: str
    shooter_id: str
    target_id: str
    # 下令時選定的武器範本（payload.weapon_id）；None＝由 weapon_for 取單位主武器（#11）。
    weapon_template_id: str | None = None


# 武器查表：由整筆交戰命令解析（可依 weapon_template_id honor 操作員選武器，或退回單位主武器）。
WeaponLookup = Callable[[EngageCommand], WeaponProfile]
# squad 火力容量（#30）：由交戰命令解析射手選定武器的建制數量（>1 → 齊射）。
QuantityLookup = Callable[[EngageCommand], int]


class EngageOrderSource:
    """從 DB 拉本 session 的 VALIDATED ENGAGE 指令並轉 EXECUTING（確定性排序）。

    #33b 通信閘門：射手 OFFLINE 收不到新交戰指令、DEGRADED 延遲 N ticks（§6.2）——被擋者留在
    VALIDATED（不轉 EXECUTING），待通信恢復後再送達。tick 由注入的 clock 提供（決定性）。
    """

    def __init__(
        self,
        db: Session,
        session_id: str,
        hot_state: HotStateStore | None = None,
        clock: object | None = None,
    ) -> None:
        self._db = db
        self._session_id = session_id
        self._hot = hot_state
        self._clock = clock  # 具 now().tick 者（SimClock）；None → 不套通信延遲閘門

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
        now_tick = self._clock.now().tick if self._clock is not None else None  # type: ignore[attr-defined]
        commands: list[EngageCommand] = []
        for order in orders:
            if (
                self._hot is not None
                and now_tick is not None
                and not self._comms_admits(order, now_tick)
            ):
                continue  # 通信擋下 → 留 VALIDATED，本 tick 不執行
            order.status = next_status(order.status, OrderStatus.EXECUTING)
            payload = order.payload or {}
            wid = payload.get("weapon_id")
            commands.append(
                EngageCommand(
                    order_id=order.id,
                    shooter_id=order.unit_id,
                    target_id=str(payload["target_unit_id"]),
                    weapon_template_id=str(wid) if wid else None,
                )
            )
        self._db.commit()
        return commands

    def _comms_admits(self, order: Order, now_tick: int) -> bool:
        """射手通信是否允許本 tick 接收此新交戰指令（§6.2）。缺 hot/comms_state → 允許。"""
        assert self._hot is not None
        state = self._hot.get_unit(order.unit_id) or {}
        link = parse_link_state(state.get("comms_state"))
        return order_admissible(link, int(order.issued_at_tick or 0), now_tick)


class EngagementAdjudicator:
    """把 EngageCommand 交給純函數裁決引擎，並把結果落到熱狀態 + order 狀態。"""

    def __init__(
        self,
        db: Session,
        hot_state: HotStateStore,
        rng: DeterministicRNG,
        weapon_for: WeaponLookup,
        env_for: EngageEnvLookup,
        quantity_for: QuantityLookup | None = None,
    ) -> None:
        self._db = db
        self._hot = hot_state
        self._rng = rng
        self._weapon_for = weapon_for
        self._env_for = env_for
        # #30 建制數量查表（None → 一律 1，走既有單發路徑；golden replay 不變）。
        self._quantity_for = quantity_for

    def resolve(self, order: EngageCommand, now: SimTime) -> list[LedgerEvent]:
        shooter_state = self._hot.get_unit(order.shooter_id)
        target_state = self._hot.get_unit(order.target_id)
        if shooter_state is None or target_state is None:
            self._complete(order.order_id, now.tick)
            return []

        weapon = self._weapon_for(order)
        env = self._env_for(order.shooter_id, order.target_id, weapon.indirect_fire)
        # #30 射手建制數量 + 效能：quantity>1 → 齊射（全員射擊）；effectiveness 由射手戰力比導出。
        quantity = self._quantity_for(order) if self._quantity_for is not None else 1
        s_auth = float(shooter_state.get("authorized_strength") or 100.0)
        s_cur = float(shooter_state.get("strength") or shooter_state.get("health") or s_auth)
        effectiveness = interp_effectiveness(s_cur / s_auth) if s_auth > 0 else 1.0
        result = resolve_engagement(
            weapon,
            Shooter(
                unit_id=order.shooter_id,
                ammo_count=int(shooter_state.get("ammo", 0)),
                quantity=quantity,
                effectiveness=effectiveness,
            ),
            Target(
                unit_id=order.target_id,
                armor_class=str(target_state.get("armor_class", "INFANTRY")),
                health=float(target_state.get("health", 100.0)),
                current_strength=float(
                    target_state.get("strength") or target_state.get("health") or 100.0
                ),
                authorized_strength=float(target_state.get("authorized_strength") or 100.0),
                platform_count=int(target_state.get("platform_count") or 1),
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
        # 消耗實際發射彈藥（單發＝1；squad 齊射＝發射數）。
        self._hot.update_unit(order.shooter_id, {"ammo": max(0, ammo - result.ammo_spent)})
        if result.status is Resolution.HIT:
            patch: dict[str, float] = {"health": result.target_health_after}
            if result.target_strength_after is not None:
                patch["strength"] = result.target_strength_after  # 當前戰力（唯一權威量）
            self._hot.update_unit(order.target_id, patch)
            # 戰損持久化到 DB：current_strength（權威）+ healthStatus（導出效能%）——供 GET /units、
            # 重連/AAR/重啟保留；current_strength 使漸進消耗跨 tick/重啟累積。
            target = self._db.get(TacticalUnit, order.target_id)
            if target is not None:
                target.health_status = result.target_health_after
                if result.target_strength_after is not None:
                    target.current_strength = result.target_strength_after

    def _complete(self, order_id: str, tick: int) -> None:
        order = self._db.get(Order, order_id)
        if order is None:
            return
        order.status = next_status(order.status, OrderStatus.COMPLETED)
        order.resolved_at_tick = tick
        self._db.commit()
