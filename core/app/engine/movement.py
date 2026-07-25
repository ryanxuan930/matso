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
from collections.abc import Callable

import h3
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
    route_distance_m,
)
from app.movement.mobility import UnitMobility, resolve_unit_mobility
from app.movement.mobility_matrix import step_cost as _terrain_step_cost
from app.movement.params import TEMPO_ATTRITION_FACTOR, march_attrition_per_km
from app.movement.router import PathFn, plan_route
from app.state.hot_state import HotStateStore
from app.state.ledger import LedgerEvent

# 單次行軍的耗損上限（佔進入時戰力）——避免長途一次歸零（強穿另有其上限）。
_MARCH_LOSS_CAP_PCT = 0.30
# #81 Phase B 地形調速：取樣單位所在 hex（與戰術格同解析度）→ terrain_class + slope。
_TERRAIN_RES = 8
_UNSET = object()  # _terrain_cost_cache 的哨兵（None＝不可通行，是合法快取值）。

# h3_list → {h3: (terrain_class, slope_deg)}；建不出/失敗回空 → 不調速（Phase A 行為）。
TerrainSampler = Callable[[list[str]], dict[str, tuple[str, float]]]

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
        terrain_sampler: TerrainSampler | None = None,
        weather_mobility: float = 1.0,
        path_fn: PathFn | None = None,
    ) -> None:
        self._session_id = session_id
        self._session_factory = session_factory
        self._hot_state = hot_state
        self._tick_rate_ms = tick_rate_ms
        # 後備每 tick 步距（無法由編裝導出機動時）；#80 起改為 per-unit（見 _step_sync admit）。
        self._step_km = speed_kmh * tick_rate_ms / _MS_PER_H
        # 強穿耗損的隨機來源（stream="movement"）；None 則停用（既有測試不注入）。
        self._rng = rng
        # #81 Phase B：地形取樣器（None＝不調速）+ 天氣機動修正 + 每格成本快取（地形靜態）。
        self._terrain_sampler = terrain_sampler
        self._weather_mobility = weather_mobility if weather_mobility > 0 else 1.0
        self._terrain_cost_cache: dict[tuple[str, str], float | None] = {}
        # #82 Phase C：地形 A* 路徑查詢（None＝不規劃，維持 Phase A/B 直線）。
        self._path_fn = path_fn

    async def step(self, now: SimTime) -> list[LedgerEvent]:
        # 同步 DB/H3 計算移到執行緒，避免阻塞 event loop（HOW_TO §3.1）。
        return await asyncio.to_thread(self._step_sync, now)

    def _step_sync(self, now: SimTime) -> list[LedgerEvent]:
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
                # admit（首見，status 仍 VALIDATED）：一次性解析機動速度 + 行軍/強穿耗損。
                if o.status == OrderStatus.VALIDATED:
                    # #33b 通信閘門：OFFLINE 收不到新指令、DEGRADED 延遲 N ticks（§6.2）。僅擋新
                    # 指令；已在執行者續行。靜默保留（不逐 tick 記事件避免洗版）。
                    if not self._comms_admits(o, now):
                        continue
                    # #80：per-unit 機動速度（由編裝導出）→ 存 payload._step_km（跨 tick 沿用）；
                    # #81：另存 _mobility_profile 供地形成本查表。
                    tempo = str(p.get("tempo") or "NORMAL")
                    mob = resolve_unit_mobility(db, o.unit_id)
                    p = {
                        **p,
                        "_step_km": mob.step_km(self._tick_rate_ms, tempo=tempo),
                        "_mobility_profile": mob.profile,
                    }
                    # #82 Phase C：規劃地形路徑（繞開不可通行）→ 存 _route_wp；規劃後重算 targets，
                    # 使行軍耗損依**實際繞行距離**計（繞遠路更耗）。使用者自訂 waypoints 不覆寫。
                    route_ev = None
                    if self._path_fn is not None and not _waypoints_of(p):
                        p, route_ev = self._plan_route(o, unit, p, mob.profile, now)
                        o.payload = p
                        targets = self._targets(p, dest_h3=p.get("to_h3"), h3mod=h3)
                        if not targets:
                            continue
                    else:
                        o.payload = p
                    if route_ev is not None:
                        events.append(route_ev)
                    # #80 行軍耗損：依總路徑距離 × per-km 磨耗 × tempo（確定性；地形難度 Phase B）。
                    ev_m = self._apply_march_attrition(unit, targets, mob, tempo, now)
                    if ev_m is not None:
                        events.append(ev_m)
                    # 強穿障礙額外耗損（既有；RNG stream="movement"）。
                    if self._rng is not None:
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
        """回傳依序前進的目標點 [(lng,lat), …]（不含起點）。

        優先序：使用者自訂 waypoints（刻意畫的路線，尊重之）→ `_route_wp`（#82 地形規劃路徑）
        → 單一目的地（精確經緯或格心）。
        """
        wps = _waypoints_of(payload)
        if wps:
            return wps
        routed = _waypoints_of({"waypoints": payload.get("_route_wp")})
        if routed:
            return routed
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
        # #80 per-unit 步距：admit 時已由編裝導出存入 payload._step_km；缺則用後備常數。
        raw_step = payload.get("_step_km")
        if isinstance(raw_step, (int, float)) and raw_step > 0:
            step_km = float(raw_step)
        else:
            step_km = self._step_km
        tgt_lng, tgt_lat = targets[leg]
        cur_lat, cur_lng = float(unit.current_lat or 0.0), float(unit.current_lng or 0.0)
        # #81 Phase B：以目前所在格地形類別+坡度調速；不可通行→停在此 + MOVE_BLOCKED。
        step_km *= self._weather_mobility
        if self._terrain_sampler is not None:
            prof = payload.get("_mobility_profile")
            prof = prof if isinstance(prof, str) and prof else "FOOT"
            cell = h3.latlng_to_cell(cur_lat, cur_lng, _TERRAIN_RES)
            cost = self._terrain_cost(cell, prof)
            if cost is None:
                return self._block_impassable(o, unit, cell, prof, now)
            step_km /= cost  # 成本↑＝越難走＝步距縮短
        remaining = _haversine_km(cur_lat, cur_lng, tgt_lat, tgt_lng)
        if remaining <= step_km:
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
        nlat, nlng = _step_towards(cur_lat, cur_lng, tgt_lat, tgt_lng, step_km)
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

    def _plan_route(
        self,
        o: Order,
        unit: TacticalUnit,
        payload: dict,  # type: ignore[type-arg]
        profile: str,
        now: SimTime,
    ) -> tuple[dict, LedgerEvent | None]:  # type: ignore[type-arg]
        """規劃地形路徑存入 payload._route_wp（#82）。回 (payload, 事件|None)。

        任意點位：由單位**當前精確座標**出發、以**精確目的地**作結（見 movement/router）。
        規劃失敗/不可達 → 退回直線（payload 不變）並記 MOVE_ROUTE_FALLBACK 供觀測。
        """
        dest = self._dest_latlng(payload)
        if dest is None:
            return payload, None
        route = plan_route(
            self._path_fn,  # type: ignore[arg-type]
            start_lat=float(unit.current_lat or 0.0),
            start_lng=float(unit.current_lng or 0.0),
            dest_lat=dest[0],
            dest_lng=dest[1],
            profile=profile,
        )
        if not route.routed:
            # 退回直線：不阻擋移動（避免超出地形快取範圍的長距離誤拒），但留下可觀測記錄。
            if route.reason != "same_cell":
                return payload, LedgerEvent(
                    event_type="MOVE_ROUTE_FALLBACK",
                    tick=now.tick,
                    initiator_id=o.unit_id,
                    detail={"order_id": o.id, "reason": route.reason, "profile": profile},
                )
            return payload, None
        return {**payload, "_route_wp": [[lng, lat] for lng, lat in route.waypoints]}, LedgerEvent(
            event_type="MOVE_ROUTE_PLANNED",
            tick=now.tick,
            initiator_id=o.unit_id,
            detail={"order_id": o.id, "legs": route.hops, "profile": profile},
        )

    @staticmethod
    def _dest_latlng(payload: dict) -> tuple[float, float] | None:  # type: ignore[type-arg]
        """由 payload 取精確目的地 (lat,lng)：優先 to_lat/to_lng，否則 to_h3 格心。"""
        to_lat, to_lng = payload.get("to_lat"), payload.get("to_lng")
        if isinstance(to_lat, (int, float)) and isinstance(to_lng, (int, float)):
            return float(to_lat), float(to_lng)
        dest_h3 = payload.get("to_h3")
        if isinstance(dest_h3, str) and dest_h3:
            lat, lng = h3.cell_to_latlng(dest_h3)
            return float(lat), float(lng)
        return None

    def _terrain_cost(self, cell: str, profile: str) -> float | None:
        """該格對此 profile 的通行成本倍率（快取；不可通行回 None）。

        地形靜態 → 以 (cell, profile) 快取跨 tick/單位共用。取樣失敗（terrain 服務暫不可用）→ 回 1.0
        不快取（下次重試）——與交戰「地形服務中斷不凍結戰鬥」同紀律，且保持決定性退化。
        """
        key = (cell, profile)
        cached = self._terrain_cost_cache.get(key, _UNSET)
        if cached is not _UNSET:
            return cached  # type: ignore[return-value]
        assert self._terrain_sampler is not None
        try:
            info = self._terrain_sampler([cell])
        except Exception:
            return 1.0
        ct = info.get(cell)
        cost = _terrain_step_cost(profile, ct[0], ct[1]) if ct is not None else 1.0
        self._terrain_cost_cache[key] = cost
        return cost

    def _block_impassable(
        self, o: Order, unit: TacticalUnit, cell: str, profile: str, now: SimTime
    ) -> LedgerEvent:
        """單位進入不可通行地形 → 停在此、移動中止（COMPLETED）並記 MOVE_BLOCKED（#81）。"""
        o.status = OrderStatus.COMPLETED
        return LedgerEvent(
            event_type="MOVE_BLOCKED",
            tick=now.tick,
            initiator_id=o.unit_id,
            detail={
                "order_id": o.id,
                "reason": "IMPASSABLE_TERRAIN",
                "profile": profile,
                "cell": cell,
                "lat": float(unit.current_lat or 0.0),
                "lng": float(unit.current_lng or 0.0),
            },
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

    def _apply_march_attrition(
        self,
        unit: TacticalUnit,
        targets: list[tuple[float, float]],
        mob: UnitMobility,
        tempo: str,
        now: SimTime,
    ) -> LedgerEvent | None:
        """行軍耗損（#80）：總路徑距離 × per-km 磨耗 × tempo 扣戰力（確定性；地形難度待 Phase B）。

        於 admit 一次性套用（比照強穿）；march 先於強穿，兩者對剩餘戰力依序扣減、各記事件。
        """
        before = float(unit.current_strength)
        if before <= 0.0 or not targets:
            return None
        route = [(float(unit.current_lng or 0.0), float(unit.current_lat or 0.0)), *targets]
        dist_km = route_distance_m(route) / 1000.0
        if dist_km <= 0.0:
            return None
        per_km = march_attrition_per_km(mob.profile)
        tempo_mult = TEMPO_ATTRITION_FACTOR.get(tempo, 1.0)
        loss = min(dist_km * per_km * tempo_mult, before * _MARCH_LOSS_CAP_PCT)
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
            damage_calc=round(loss, 4),
            detail={
                "reason": "MARCH",
                "profile": mob.profile,
                "tempo": tempo,
                "distance_km": round(dist_km, 3),
                "strength_before": before,
                "strength_after": after,
            },
        )

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
