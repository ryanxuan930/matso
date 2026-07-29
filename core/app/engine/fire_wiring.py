"""面目標射擊在活執行期的接線（WP-C10.2）——把 VALIDATED FIRE_MISSION 接上裁決純函數。

`adjudication/area_fire.py` 是純同步純函數（紅線 2）；本模組只做 I/O 邊界：

    drain VALIDATED FIRE_MISSION → 蒐集落點附近單位（**敵我皆收**）
    → resolve_area_fire → 扣彈藥、落戰損、轉 COMPLETED

**蒐集目標為什麼不做半徑預篩**：落點是 Rayleigh 抽樣，尾巴無上界——任何「半徑 + N 倍 sigma」
的預篩都是猜的，猜小了就是把遠處的傷亡悄悄吃掉，而且不會有任何徵兆。這裡直接把
**全部有座標的單位**交給純函數依實際落點篩，寧可多算幾次 haversine（單位數量級 500，
火力任務本身稀疏）。真的成為熱點時該補的是空間索引，不是一個魔術半徑。

**射手自己也在目標清單裡**：對自己的位置叫火力（danger close / 最終保護射擊）是真實戰術，
不特判。砲彈不挑人這件事沒有例外。
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adjudication.area_fire import AreaTarget, resolve_area_fire
from app.adjudication.effectiveness import effectiveness_pct
from app.adjudication.weapon import INDIRECT_CATEGORIES
from app.comms import order_admissible, parse_link_state
from app.engine.clock import SimTime
from app.engine.engage_wiring import WeaponEntry
from app.engine.rng import DeterministicRNG
from app.models.enums import OrderStatus
from app.models.tables import EquipmentInstance, Order, TacticalUnit
from app.orders.schemas import OrderType
from app.orders.state_machine import next_status
from app.state.hot_state import HotStateStore
from app.state.ledger import LedgerEvent

_EARTH_R_M = 6_371_000.0


def _haversine_m(a_lat: float, a_lng: float, b_lat: float, b_lng: float) -> float:
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    h = (
        math.sin(math.radians(b_lat - a_lat) / 2) ** 2
        + math.cos(p1) * math.cos(p2) * math.sin(math.radians(b_lng - a_lng) / 2) ** 2
    )
    return 2 * _EARTH_R_M * math.asin(min(1.0, math.sqrt(h)))


@dataclass(frozen=True, slots=True)
class FireMissionCommand:
    """一道面目標射擊令（打座標）。與 `EngageCommand` 刻意分型——目標是點不是單位。"""

    order_id: str
    shooter_id: str
    target_lat: float
    target_lng: float
    rounds: int = 1
    # 下令時指名的武器（payload.weapon_id＝EquipmentInstance.id）；None＝取射程最遠的曲射武器。
    weapon_template_id: str | None = None


class FireMissionOrderSource:
    """從 DB 拉本 session 的 VALIDATED FIRE_MISSION 並轉 EXECUTING（確定性排序）。

    通信閘門與 `EngageOrderSource` 同紀律（§6.2）：射手 OFFLINE 收不到新令、DEGRADED 延遲
    N ticks，被擋者留在 VALIDATED 待通信恢復。**火力任務尤其不該例外**——叫不到火力
    正是通信中斷最直接的後果。
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
        self._clock = clock

    async def drain(self) -> list[FireMissionCommand]:
        orders = self._db.scalars(
            select(Order)
            .where(
                Order.session_id == self._session_id,
                Order.status == OrderStatus.VALIDATED,
                Order.order_type == OrderType.FIRE_MISSION.value,
            )
            .order_by(Order.issued_at_tick, Order.id)
        ).all()
        now_tick = self._clock.now().tick if self._clock is not None else None  # type: ignore[attr-defined]
        commands: list[FireMissionCommand] = []
        for order in orders:
            if self._hot is not None and now_tick is not None:
                state = self._hot.get_unit(order.unit_id) or {}
                link = parse_link_state(state.get("comms_state"))
                if not order_admissible(link, int(order.issued_at_tick or 0), now_tick):
                    continue  # 通信擋下 → 留 VALIDATED，本 tick 不執行
            payload = order.payload or {}
            order.status = next_status(order.status, OrderStatus.EXECUTING)
            try:
                lat = float(payload["target_lat"])
                lng = float(payload["target_lng"])
            except (KeyError, TypeError, ValueError):
                # payload 壞掉的令不能永遠卡在 VALIDATED 被反覆撈出來——就地判 REJECTED。
                # 先轉 EXECUTING 再轉 REJECTED：狀態機不容許 VALIDATED 直接跳終態。
                order.status = next_status(order.status, OrderStatus.REJECTED)
                continue
            wid = payload.get("weapon_id")
            raw_rounds = payload.get("rounds", 1)
            rounds = int(raw_rounds) if isinstance(raw_rounds, (int, float)) else 1
            commands.append(
                FireMissionCommand(
                    order_id=order.id,
                    shooter_id=order.unit_id,
                    target_lat=lat,
                    target_lng=lng,
                    rounds=max(1, rounds),
                    weapon_template_id=str(wid) if wid else None,
                )
            )
        self._db.commit()
        return commands


class AreaFireAdjudicator:
    """把 `FireMissionCommand` 交給 `resolve_area_fire`，並把結果落到熱狀態 + DB + order 狀態。

    武器選定**一律看裝備類別**（`INDIRECT_CATEGORIES`），與預檢同一份規則——見該常數的註解。
    指名了直射武器 → 視同未指名，退回該單位射程最遠的曲射武器（令能通過預檢就代表它有一把）。
    """

    def __init__(
        self,
        db: Session,
        hot_state: HotStateStore,
        rng: DeterministicRNG,
        weapons_for: Callable[[str], Sequence[WeaponEntry]],
        faction_for: Callable[[str], str] | None = None,
    ) -> None:
        self._db = db
        self._hot = hot_state
        self._rng = rng
        self._weapons_for = weapons_for
        self._faction_for = faction_for

    def resolve(self, order: FireMissionCommand, now: SimTime) -> list[LedgerEvent]:
        shooter = self._hot.get_unit(order.shooter_id)
        if shooter is None:
            self._complete(order.order_id, now.tick)
            return []

        entry = self._pick_weapon(order)
        if entry is None:
            return self._reject(order, now, "NO_INDIRECT_WEAPON", "此單位無可用的曲射武器")

        aim = (order.target_lat, order.target_lng)
        reason = self._legality(order, entry, shooter, aim)
        if reason is not None:
            return self._reject(order, now, reason[0], reason[1])

        # 彈不夠就打不夠——**不是整道令作廢**。有幾發打幾發是砲兵的真實行為，
        # 而「本來要 12 發只打了 3 發」這件事要看得見，故記在事件裡。
        available = self._live_ammo(order.shooter_id, entry)
        fired = min(order.rounds, available)
        if fired <= 0:
            return self._reject(order, now, "NO_AMMO", "彈藥不足，無法執行火力任務")

        shooter_faction = self._faction_for(order.shooter_id) if self._faction_for else None
        result = resolve_area_fire(
            entry.profile,
            aim,
            self._gather_targets(),
            self._rng,
            now.tick,
            shooter_id=order.shooter_id,
            shooter_faction=shooter_faction,
            rounds=fired,
        )
        self._spend_ammo(order.shooter_id, entry, fired)
        self._apply_losses(result.losses)
        event = result.event
        if event is not None and fired < order.rounds:
            event.ai_decision["rounds_requested"] = order.rounds
        self._complete(order.order_id, now.tick)
        return [event] if event is not None else []

    # ---- 內部 ----

    def _pick_weapon(self, order: FireMissionCommand) -> WeaponEntry | None:
        indirect = [
            e for e in self._weapons_for(order.shooter_id) if e.category in INDIRECT_CATEGORIES
        ]
        if not indirect:
            return None
        if order.weapon_template_id:
            named = [e for e in indirect if e.weapon_id == order.weapon_template_id]
            if named:
                return named[0]
        # 射程最遠者；同射程時取 weapon_id 較小者（穩定序 → 抽樣序列決定性）。
        return min(indirect, key=lambda e: (-e.profile.max_range_m, e.weapon_id))

    def _legality(
        self,
        order: FireMissionCommand,
        entry: WeaponEntry,
        shooter: dict[str, Any],
        aim: tuple[float, float],
    ) -> tuple[str, str] | None:
        """射程檢查。**下令當下過了不代表現在還過**——射手可能已經移動，故執行時重查。

        刻意不查 LOS：間瞄火力打的就是看不見的地方（與 `_precheck_fire_mission` 同一決定）。
        """
        try:
            s_lat, s_lng = float(shooter["lat"]), float(shooter["lng"])
        except (KeyError, TypeError, ValueError):
            return ("NO_POSITION", "射擊單位無座標")
        dist = _haversine_m(s_lat, s_lng, *aim)
        if dist > entry.profile.max_range_m:
            return (
                "OUT_OF_RANGE",
                f"距離 {dist / 1000:.1f} km 超出射程 {entry.profile.max_range_m / 1000:.1f} km",
            )
        return None

    def _gather_targets(self) -> list[AreaTarget]:
        """全部有座標的單位——**敵我皆收**。只收敵軍等於把友軍傷害悄悄關掉。"""
        targets: list[AreaTarget] = []
        for unit_id, state in self._hot.get_all().items():
            lat, lng = state.get("lat"), state.get("lng")
            if not isinstance(lat, (int, float)) or not isinstance(lng, (int, float)):
                continue
            auth = state.get("authorized_strength")
            cur = state.get("strength", state.get("health"))
            targets.append(
                AreaTarget(
                    unit_id=unit_id,
                    faction=self._faction_for(unit_id) if self._faction_for else "",
                    lat=float(lat),
                    lng=float(lng),
                    armor_class=str(state.get("armor_class", "INFANTRY")),
                    current_strength=float(cur) if isinstance(cur, (int, float)) else None,
                    authorized_strength=float(auth) if isinstance(auth, (int, float)) else None,
                    platform_count=int(state.get("platform_count") or 1),
                )
            )
        # 穩定序：純函數逐目標算距離不抽樣，順序不影響數值，但影響事件內字典的鍵序（hash chain）。
        targets.sort(key=lambda t: t.unit_id)
        return targets

    def _live_ammo(self, shooter_id: str, entry: WeaponEntry) -> int:
        state = self._hot.get_unit(shooter_id) or {}
        by_weapon = state.get("ammo_by_weapon")
        if isinstance(by_weapon, dict) and entry.weapon_id in by_weapon:
            raw = by_weapon[entry.weapon_id]
            return int(raw) if isinstance(raw, (int, float)) else 0
        return entry.ammo

    def _spend_ammo(self, shooter_id: str, entry: WeaponEntry, fired: int) -> None:
        state = self._hot.get_unit(shooter_id) or {}
        by_weapon = dict(state.get("ammo_by_weapon") or {})
        remaining = max(0, self._live_ammo(shooter_id, entry) - fired)
        by_weapon[entry.weapon_id] = remaining
        total = int(state.get("ammo", 0) or 0)
        self._hot.update_unit(
            shooter_id, {"ammo_by_weapon": by_weapon, "ammo": max(0, total - fired)}
        )
        # 持久化到 DB（#53 同紀律）：供 GET /weapons 顯示正確、sim 重啟續戰不回滿。
        inst = self._db.get(EquipmentInstance, entry.weapon_id)
        if inst is not None:
            inst.current_state = {**(inst.current_state or {}), "ammo": remaining}

    def _apply_losses(self, losses: dict[str, float]) -> None:
        """戰損落熱狀態 + DB。`health` 一律由戰力比導出（與交戰裁決同一條路徑）。"""
        for unit_id, loss in losses.items():
            if loss <= 0:
                continue
            state = self._hot.get_unit(unit_id) or {}
            auth = float(state.get("authorized_strength") or 100.0)
            cur = float(state.get("strength", state.get("health", auth)) or 0.0)
            after = max(0.0, cur - loss)
            health = effectiveness_pct(after / auth) if auth > 0 else 0.0
            self._hot.update_unit(unit_id, {"strength": after, "health": health})
            unit = self._db.get(TacticalUnit, unit_id)
            if unit is not None:
                unit.current_strength = after
                unit.health_status = health

    def _reject(
        self, order: FireMissionCommand, now: SimTime, reason: str, detail: str
    ) -> list[LedgerEvent]:
        """被物理擋下的火力任務**仍然落帳**——「叫了火力但沒打出去」是要能追究的事。"""
        event = LedgerEvent(
            event_type="AREA_FIRE_RESOLVED",
            tick=now.tick,
            initiator_id=order.shooter_id,
            damage_calc=0.0,
            ai_decision={
                "status": "REJECTED",
                "reason": reason,
                "reason_detail": detail,
                "aim_lat": order.target_lat,
                "aim_lng": order.target_lng,
            },
        )
        self._complete(order.order_id, now.tick)
        return [event]

    def _complete(self, order_id: str, tick: int) -> None:
        order = self._db.get(Order, order_id)
        if order is None:
            self._db.commit()
            return
        order.status = next_status(order.status, OrderStatus.COMPLETED)
        order.resolved_at_tick = tick
        self._db.commit()
