"""Kernel 裁決接線（O3.6）——把 VALIDATED ENGAGE 指令接上純函數裁決引擎。

- `EngageOrderSource`（OrderSource）：drain VALIDATED ENGAGE → EngageCommand，轉 EXECUTING。
- `EngagementAdjudicator`（Adjudicator）：resolve 每筆 → 呼叫 resolve_engagement（O3.2，純函數）
  → 依結果更新熱狀態（彈藥 −1、目標血量）→ 轉 COMPLETED → 回事件。

**紅線**：物理裁決仍是純函數；本層只做 I/O 邊界（讀熱狀態、寫回、狀態轉移），AI 不介入。
武器/環境（射程、LOS、天氣係數）以 callable 注入——由 Kernel 事先收集（terrain client + O5）。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adjudication.aggregate import (
    AggregateEnv,
    AggregateForce,
    resolve_aggregate_tick,
    should_aggregate,
)
from app.adjudication.combined import CombinedWeapon, resolve_combined_engagement
from app.adjudication.effectiveness import effectiveness_pct, interp_effectiveness
from app.adjudication.engagement import (
    EngagementResult,
    EnvSnapshot,
    Resolution,
    Shooter,
    Target,
    _legality_reason,
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
    # 聯合兵種火力政策（SPEC_EXTEND P3，payload.fire_policy）；None＝FREE。指定 weapon_id 時忽略。
    fire_policy: str | None = None


# 武器查表：由整筆交戰命令解析（可依 weapon_template_id honor 操作員選武器，或退回單位主武器）。
WeaponLookup = Callable[[EngageCommand], WeaponProfile]
# squad 火力容量（#30）：由交戰命令解析射手選定武器的建制數量（>1 → 齊射）。
QuantityLookup = Callable[[EngageCommand], int]
# 聯合兵種（SPEC_EXTEND P2）：由 shooter_id 取單位當前武器組合（帶活彈藥）；≥2 → combined 加總。
CombinedWeaponsLookup = Callable[[str], Sequence[CombinedWeapon]]

# 聚合裁決（#33a）攻擊係數：射手 lethality＝武器 pk × 尺度（每 tick 對敵戰力的殺傷率）；
# 目標返火用固定小係數；變異度給隨機化。皆為 v0 校準值，可由想定/平衡調整。
_AGG_LETH_SCALE = 0.02
_AGG_MIN_LETH = 0.005
_AGG_RETURN_FIRE_LETH = 0.01
_AGG_VARIANCE = 0.1


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
            fp = payload.get("fire_policy")
            commands.append(
                EngageCommand(
                    order_id=order.id,
                    shooter_id=order.unit_id,
                    target_id=str(payload["target_unit_id"]),
                    weapon_template_id=str(wid) if wid else None,
                    fire_policy=str(fp) if fp else None,
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
        combined_weapons_for: CombinedWeaponsLookup | None = None,
    ) -> None:
        self._db = db
        self._hot = hot_state
        self._rng = rng
        self._weapon_for = weapon_for
        self._env_for = env_for
        # #30 建制數量查表（None → 一律 1，走既有單發路徑；golden replay 不變）。
        self._quantity_for = quantity_for
        # SPEC_EXTEND P2 聯合兵種：武器組合查表（None 或 <2 武器 → 走既有單/齊射；golden 不變）。
        self._combined_weapons_for = combined_weapons_for

    def resolve(self, order: EngageCommand, now: SimTime) -> list[LedgerEvent]:
        shooter_state = self._hot.get_unit(order.shooter_id)
        target_state = self._hot.get_unit(order.target_id)
        if shooter_state is None or target_state is None:
            self._complete(order.order_id, now.tick)
            return []

        weapon = self._weapon_for(order)
        env = self._env_for(order.shooter_id, order.target_id, weapon.indirect_fire)

        # #33a 聚合裁決：射手為營級以上 → 走 Lanchester（雙方同時消耗），取代逐平台/齊射。
        # 平台級（連/排以下）維持既有路徑（golden replay 不變）。
        shooter_unit = self._db.get(TacticalUnit, order.shooter_id)
        if shooter_unit is not None and should_aggregate(shooter_unit.unit_level):
            return self._resolve_aggregate(order, weapon, env, shooter_state, target_state, now)

        # SPEC_EXTEND P2/P3 聯合兵種：**未指定單一武器**且射手持 ≥2 種武器系統 → 武器組合加總。
        # 指定 weapon_id（操作員選單一武器）或單武器單位 → 落回既有單/齊射路徑（golden 不變）。
        if self._combined_weapons_for is not None and order.weapon_template_id is None:
            cweapons = self._combined_weapons_for(order.shooter_id)
            if len(cweapons) >= 2:
                return self._resolve_combined(order, cweapons, shooter_state, target_state, now)

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

    def _resolve_aggregate(
        self,
        order: EngageCommand,
        weapon: WeaponProfile,
        env: EnvSnapshot,
        shooter_state: dict[str, object],
        target_state: dict[str, object],
        now: SimTime,
    ) -> list[LedgerEvent]:
        """營級以上聚合裁決（#33a）：Lanchester 雙方同時消耗，套 should_aggregate 門檻後呼叫。"""
        # 合法性（射程/LOS/彈道）——以探子 shooter（彈藥視為足）判定；不過即 REJECTED。
        reason = _legality_reason(weapon, Shooter(order.shooter_id, ammo_count=1), env)
        if reason is not None:
            event = LedgerEvent(
                event_type="ENGAGEMENT_RESOLVED",
                tick=now.tick,
                initiator_id=order.shooter_id,
                target_id=order.target_id,
                damage_calc=0.0,
                ai_decision={"status": "REJECTED", "reason": reason, "mode": "AGGREGATE"},
            )
            self._complete(order.order_id, now.tick)
            return [event]

        shooter_unit = self._db.get(TacticalUnit, order.shooter_id)
        target_unit = self._db.get(TacticalUnit, order.target_id)
        if shooter_unit is None or target_unit is None:
            self._complete(order.order_id, now.tick)
            return []

        s_auth = float(shooter_state.get("authorized_strength") or 100.0)  # type: ignore[arg-type]
        t_auth = float(target_state.get("authorized_strength") or 100.0)  # type: ignore[arg-type]
        s_str = float(shooter_state.get("strength") or s_auth)  # type: ignore[arg-type]
        t_str = float(target_state.get("strength") or t_auth)  # type: ignore[arg-type]
        t_armor = str(target_state.get("armor_class", "INFANTRY"))
        # 攻擊係數：射手武器對目標裝甲的 pk × 尺度（下限保底）；目標返火用固定小係數。
        s_leth = max(_AGG_MIN_LETH, weapon.expected_casualties(t_armor) * _AGG_LETH_SCALE)
        force_a = AggregateForce(order.shooter_id, shooter_unit.faction, s_str, s_leth)
        force_b = AggregateForce(order.target_id, target_unit.faction, t_str, _AGG_RETURN_FIRE_LETH)
        agg_env = AggregateEnv(
            terrain_modifier=env.terrain_cover_modifier,
            weather_modifier=env.weather_modifier,
            variance=_AGG_VARIANCE,
        )
        result = resolve_aggregate_tick(force_a, force_b, agg_env, self._rng, now.tick)
        self._apply_agg_force(shooter_unit, result.a_strength_after, s_auth)
        self._apply_agg_force(target_unit, result.b_strength_after, t_auth)
        self._complete(order.order_id, now.tick)
        return result.events

    def _resolve_combined(
        self,
        order: EngageCommand,
        cweapons: Sequence[CombinedWeapon],
        shooter_state: dict[str, Any],
        target_state: dict[str, Any],
        now: SimTime,
    ) -> list[LedgerEvent]:
        """聯合兵種加總裁決（SPEC_EXTEND P2）：單位武器組合逐件 volley 加總。"""
        s_auth = float(shooter_state.get("authorized_strength") or 100.0)
        s_cur = float(shooter_state.get("strength") or shooter_state.get("health") or s_auth)
        effectiveness = interp_effectiveness(s_cur / s_auth) if s_auth > 0 else 1.0
        target = Target(
            unit_id=order.target_id,
            armor_class=str(target_state.get("armor_class", "INFANTRY")),
            health=float(target_state.get("health", 100.0)),
            current_strength=float(
                target_state.get("strength") or target_state.get("health") or 100.0
            ),
            authorized_strength=float(target_state.get("authorized_strength") or 100.0),
            platform_count=int(target_state.get("platform_count") or 1),
        )

        # 每武器環境依飛行剖面（直/間瞄）——同一射手/目標座標，僅天氣/LOS 判定方式因武器而異。
        def env_for(profile: WeaponProfile) -> EnvSnapshot:
            return self._env_for(order.shooter_id, order.target_id, profile.indirect_fire)

        result = resolve_combined_engagement(
            cweapons,
            order.shooter_id,
            effectiveness,
            target,
            env_for,
            self._rng,
            now.tick,
            fire_policy=order.fire_policy or "FREE",
        )
        self._apply(order, result)
        self._complete(order.order_id, now.tick)
        return result.events

    def _apply_agg_force(
        self, unit: TacticalUnit, strength_after: float, authorized: float
    ) -> None:
        """把聚合裁決後戰力寫回熱狀態 + DB（health＝由戰力比導出的效能%）。"""
        health = effectiveness_pct(strength_after / authorized) if authorized > 0 else 0.0
        self._hot.update_unit(unit.id, {"strength": strength_after, "health": health})
        unit.current_strength = strength_after
        unit.health_status = health

    def _apply(self, order: EngageCommand, result: EngagementResult) -> None:
        if result.status is Resolution.REJECTED:
            return  # 合法性未過（彈藥/射程/LOS）→ 不消耗、不變更
        shooter = self._hot.get_unit(order.shooter_id) or {}
        if result.ammo_spent_by_weapon is not None:
            # 聯合兵種（P2）：逐武器扣熱狀態 ammo_by_weapon；純量 ammo 同步扣總量（供 COP 概覽）。
            abw = dict(shooter.get("ammo_by_weapon") or {})
            for wid, spent in result.ammo_spent_by_weapon.items():
                abw[wid] = max(0, int(abw.get(wid, 0)) - spent)
            ammo = int(shooter.get("ammo", 0))
            self._hot.update_unit(
                order.shooter_id,
                {"ammo_by_weapon": abw, "ammo": max(0, ammo - result.ammo_spent)},
            )
        else:
            ammo = int(shooter.get("ammo", 0))
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
