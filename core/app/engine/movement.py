"""單位移動系統（O10.1 + #28）——執行 MOVE 指令：每 tick 讓單位朝目標/沿自訂路徑前進。

紅線遵循：決定性——只用注入的 `SimTime`、固定速度、與注入的 `DeterministicRNG`（stream=
"movement"）推進，**不碰牆鐘、不用裸 random**。Kernel 為熱狀態唯一寫入者：本系統經
`hot_state.update_unit` 累積 per-unit diff，由 Kernel 於 tick 末 `drain_diff` 廣播 STATE_DIFF。
DB 位置一併更新，讓 GET /units 反映最新位置、且斷線重連正確。

#28 強化：
  * 自訂路徑：payload.waypoints（[[lng,lat],…]）逐段（leg）前進；進度存 payload._leg。
  * 強穿耗損：admit（VALIDATED→首見）時，若整條路徑穿越不可通行標註（障礙/建築/不可通行地形），
    以注入的 rng 擲一次額外隨機耗損 → 扣 current_strength（DB + 熱狀態）並記 MOVE_ATTRITION。
"""

from __future__ import annotations

import asyncio
import math

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.adjudication.effectiveness import effectiveness_pct
from app.comms import order_admissible, parse_link_state
from app.engine.clock import SimTime
from app.engine.rng import DeterministicRNG
from app.models import MapFeature, Order, OrderStatus, TacticalUnit
from app.movement.attrition import (
    Obstacle,
    classify_crossings,
    forced_extra_attrition,
    obstacle_from_feature,
)
from app.state.hot_state import HotStateStore
from app.state.ledger import LedgerEvent

# 每小時公里 → 每 tick 公里 = speed_kmh × tick_rate_ms / 3_600_000
_MS_PER_H = 3_600_000.0


def _haversine_km(a_lat: float, a_lng: float, b_lat: float, b_lng: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    dphi = math.radians(b_lat - a_lat)
    dlmb = math.radians(b_lng - a_lng)
    h = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(h)))


def _step_towards(
    lat: float, lng: float, dlat: float, dlng: float, step_km: float
) -> tuple[float, float]:
    """由 (lat,lng) 朝 (dlat,dlng) 前進 step_km；剩餘距離 ≤ step 則直接到點（呼叫端判定）。"""
    dist = _haversine_km(lat, lng, dlat, dlng)
    if dist <= 1e-9:
        return dlat, dlng
    frac = step_km / dist
    return lat + (dlat - lat) * frac, lng + (dlng - lng) * frac


def _waypoints_of(payload: dict) -> list[tuple[float, float]]:  # type: ignore[type-arg]
    """payload.waypoints → [(lng,lat), …]（過濾壞點）；無則空清單。"""
    raw = payload.get("waypoints")
    if not isinstance(raw, list):
        return []
    out: list[tuple[float, float]] = []
    for p in raw:
        if isinstance(p, (list, tuple)) and len(p) >= 2:
            try:
                out.append((float(p[0]), float(p[1])))
            except (TypeError, ValueError):
                continue
    return out


class UnitMovementSystem:
    """滿足 Kernel 的 `MovementSystem` 介面。每 tick：撿起 VALIDATED MOVE → EXECUTING，
    朝目標/沿自訂路徑推進；到終點則標 COMPLETED。位置寫 DB + 熱狀態。"""

    def __init__(
        self,
        *,
        session_id: str,
        session_factory: sessionmaker,  # type: ignore[type-arg]
        hot_state: HotStateStore,
        tick_rate_ms: int,
        speed_kmh: float = 40.0,
        rng: DeterministicRNG | None = None,
    ) -> None:
        self._session_id = session_id
        self._session_factory = session_factory
        self._hot_state = hot_state
        self._step_km = speed_kmh * tick_rate_ms / _MS_PER_H
        # 強穿耗損的隨機來源（stream="movement"）；None 則停用（既有測試不注入）。
        self._rng = rng

    async def step(self, now: SimTime) -> list[LedgerEvent]:
        # 同步 DB/H3 計算移到執行緒，避免阻塞 event loop（HOW_TO §3.1）。
        return await asyncio.to_thread(self._step_sync, now)

    def _step_sync(self, now: SimTime) -> list[LedgerEvent]:
        import h3

        events: list[LedgerEvent] = []
        with self._session_factory() as db:
            orders = (
                db.execute(
                    select(Order).where(
                        Order.session_id == self._session_id,
                        Order.order_type == "MOVE",
                        Order.status.in_([OrderStatus.VALIDATED, OrderStatus.EXECUTING]),
                    )
                )
                .scalars()
                .all()
            )
            # 只有本 tick 有新 admit（VALIDATED）的指令才載入阻礙標註（省 query）。
            obstacles: list[Obstacle] | None = None
            for o in orders:
                unit = db.get(TacticalUnit, o.unit_id)
                p = o.payload or {}
                if unit is None or unit.current_lat is None or unit.current_lng is None:
                    continue
                targets = self._targets(p, dest_h3=p.get("to_h3"), h3mod=h3)
                if not targets:
                    continue
                # #33b 通信閘門：OFFLINE 收不到新指令、DEGRADED 延遲 N ticks（§6.2）。
                # 僅擋「新指令」（VALIDATED）；已在執行（EXECUTING）者續行——收不到＝繼續原任務。
                # 靜默保留（不逐 tick 記事件避免洗版）；玩家由 Unit 卡通聯狀態研判。
                if o.status == OrderStatus.VALIDATED and not self._comms_admits(o, now):
                    continue
                # 強穿耗損：僅於 admit（首見，status 仍 VALIDATED）擲一次。
                if o.status == OrderStatus.VALIDATED and self._rng is not None:
                    if obstacles is None:
                        obstacles = self._load_obstacles(db)
                    ev = self._apply_forced_attrition(db, unit, targets, obstacles, now)
                    if ev is not None:
                        events.append(ev)
                ev = self._advance_unit(o, unit, p, targets, now)
                if ev is not None:
                    events.append(ev)
            db.commit()
        return events

    def _comms_admits(self, o: Order, now: SimTime) -> bool:
        """通信閘門（§6.2）：OFFLINE 收不到新指令、DEGRADED 延遲送達。缺 comms_state → ONLINE。"""
        state = self._hot_state.get_unit(o.unit_id) or {}
        link = parse_link_state(state.get("comms_state"))
        return order_admissible(link, int(o.issued_at_tick or 0), now.tick)

    def _targets(
        self,
        payload: dict[str, object],
        *,
        dest_h3: object,
        h3mod: object,
    ) -> list[tuple[float, float]]:
        """回傳依序前進的目標點 [(lng,lat), …]（不含起點）。waypoints 優先；否則單一目的地。"""
        wps = _waypoints_of(payload)
        if wps:
            return wps
        to_lat, to_lng = payload.get("to_lat"), payload.get("to_lng")
        if isinstance(to_lat, (int, float)) and isinstance(to_lng, (int, float)):
            return [(float(to_lng), float(to_lat))]
        if isinstance(dest_h3, str) and dest_h3:
            lat, lng = h3mod.cell_to_latlng(dest_h3)  # type: ignore[attr-defined]
            return [(float(lng), float(lat))]
        return []

    def _advance_unit(
        self,
        o: Order,
        unit: TacticalUnit,
        payload: dict,  # type: ignore[type-arg]
        targets: list[tuple[float, float]],
        now: SimTime,
    ) -> LedgerEvent | None:
        leg = payload.get("_leg", 0)
        leg = int(leg) if isinstance(leg, (int, float)) else 0
        leg = max(0, min(leg, len(targets) - 1))
        tgt_lng, tgt_lat = targets[leg]
        cur_lat, cur_lng = float(unit.current_lat or 0.0), float(unit.current_lng or 0.0)
        remaining = _haversine_km(cur_lat, cur_lng, tgt_lat, tgt_lng)
        if remaining <= self._step_km:
            # 抵達此段終點。
            unit.current_lat, unit.current_lng = float(tgt_lat), float(tgt_lng)
            self._hot_state.update_unit(o.unit_id, {"lat": tgt_lat, "lng": tgt_lng})
            if leg >= len(targets) - 1:
                o.status = OrderStatus.COMPLETED
                return LedgerEvent(
                    event_type="UNIT_ARRIVED",
                    tick=now.tick,
                    initiator_id=o.unit_id,
                    detail={"order_id": o.id, "lat": tgt_lat, "lng": tgt_lng},
                )
            # 續往下一段（進度存回 payload）。
            o.payload = {**payload, "_leg": leg + 1}
            o.status = OrderStatus.EXECUTING
            return LedgerEvent(
                event_type="UNIT_MOVED",
                tick=now.tick,
                initiator_id=o.unit_id,
                detail={"order_id": o.id, "lat": tgt_lat, "lng": tgt_lng, "leg": leg + 1},
            )
        nlat, nlng = _step_towards(cur_lat, cur_lng, tgt_lat, tgt_lng, self._step_km)
        unit.current_lat, unit.current_lng = float(nlat), float(nlng)
        if o.status != OrderStatus.EXECUTING:
            o.status = OrderStatus.EXECUTING
        self._hot_state.update_unit(o.unit_id, {"lat": nlat, "lng": nlng})
        return LedgerEvent(
            event_type="UNIT_MOVED",
            tick=now.tick,
            initiator_id=o.unit_id,
            detail={"order_id": o.id, "lat": nlat, "lng": nlng},
        )

    def _load_obstacles(self, db: object) -> list[Obstacle]:
        rows = (
            db.execute(  # type: ignore[attr-defined]
                select(MapFeature).where(MapFeature.session_id == self._session_id)
            )
            .scalars()
            .all()
        )
        out: list[Obstacle] = []
        for f in rows:
            obs = obstacle_from_feature(
                {
                    "id": f.id,
                    "kind": f.kind,
                    "geometry_type": f.geometry_type,
                    "geometry": f.geometry,
                    "label": f.label,
                    "influence_radius_m": f.influence_radius_m,
                    "attributes": f.attributes,
                }
            )
            if obs is not None:
                out.append(obs)
        return out

    def _apply_forced_attrition(
        self,
        db: object,
        unit: TacticalUnit,
        targets: list[tuple[float, float]],
        obstacles: list[Obstacle],
        now: SimTime,
    ) -> LedgerEvent | None:
        """整條路徑（起點 + targets）若穿越阻礙 → 擲一次額外隨機耗損，扣戰力並記事件。"""
        if not obstacles or self._rng is None:
            return None
        route = [(float(unit.current_lng or 0.0), float(unit.current_lat or 0.0)), *targets]
        crossings = classify_crossings(route, obstacles)
        if not crossings:
            return None
        before = float(unit.current_strength)
        loss = forced_extra_attrition(crossings, before, self._rng)
        if loss <= 0.0:
            return None
        after = max(0.0, before - loss)
        unit.current_strength = after
        authorized = float(unit.authorized_strength) or 100.0
        health = effectiveness_pct(after / authorized)
        self._hot_state.update_unit(unit.id, {"strength": after, "health": health})
        return LedgerEvent(
            event_type="MOVE_ATTRITION",
            tick=now.tick,
            initiator_id=unit.id,
            damage_calc=loss,
            detail={
                "reason": "FORCED_CROSSING",
                "crossings": [{"feature_id": c.feature_id, "kind": c.kind} for c in crossings],
                "strength_before": before,
                "strength_after": after,
            },
        )
