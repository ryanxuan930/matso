"""單位移動系統（O10.1）——執行 MOVE 指令：每 tick 讓單位朝目標 H3 前進，更新 DB + 熱狀態。

紅線遵循：決定性——只用注入的 `SimTime` 與固定速度推進，**不碰牆鐘、不用 random**。
Kernel 為熱狀態唯一寫入者：本系統經 `hot_state.update_unit` 累積 per-unit diff，由 Kernel
於 tick 末 `drain_diff` 廣播 STATE_DIFF（→ WS → 前端移動圖標）。DB 位置一併更新，讓 GET /units
反映最新位置、且斷線重連正確。
"""

from __future__ import annotations

import asyncio
import math

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.engine.clock import SimTime
from app.models import Order, OrderStatus, TacticalUnit
from app.state.hot_state import HotStateStore
from app.state.ledger import LedgerEvent

# 每小時公里 → 每 tick 公里 = speed_kmh × tick_rate_ms / 3_600_000
_MS_PER_H = 3_600_000.0
# MOVE 目標判定的 H3 解析度（與下令端一致，SPEC 預設 8）——僅用於解目標座標。


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


class UnitMovementSystem:
    """滿足 Kernel 的 `MovementSystem` 介面。每 tick：撿起 VALIDATED MOVE → EXECUTING，
    朝目標推進；到點則標 COMPLETED。位置寫 DB + 熱狀態。"""

    def __init__(
        self,
        *,
        session_id: str,
        session_factory: sessionmaker,  # type: ignore[type-arg]
        hot_state: HotStateStore,
        tick_rate_ms: int,
        speed_kmh: float = 40.0,
    ) -> None:
        self._session_id = session_id
        self._session_factory = session_factory
        self._hot_state = hot_state
        self._step_km = speed_kmh * tick_rate_ms / _MS_PER_H

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
            for o in orders:
                unit = db.get(TacticalUnit, o.unit_id)
                p = o.payload or {}
                dest = p.get("to_h3")
                if unit is None or unit.current_lat is None or unit.current_lng is None or not dest:
                    continue
                # 精確移動（#2）：payload 帶 to_lat/to_lng → 精確落點；否則吸附六角格心。
                to_lat, to_lng = p.get("to_lat"), p.get("to_lng")
                if isinstance(to_lat, (int, float)) and isinstance(to_lng, (int, float)):
                    dlat, dlng = float(to_lat), float(to_lng)
                else:
                    dlat, dlng = h3.cell_to_latlng(dest)
                remaining = _haversine_km(unit.current_lat, unit.current_lng, dlat, dlng)
                if remaining <= self._step_km:
                    unit.current_lat, unit.current_lng = float(dlat), float(dlng)
                    o.status = OrderStatus.COMPLETED
                    events.append(
                        LedgerEvent(
                            event_type="UNIT_ARRIVED",
                            tick=now.tick,
                            initiator_id=o.unit_id,
                            detail={"order_id": o.id, "h3": dest, "lat": dlat, "lng": dlng},
                        )
                    )
                else:
                    nlat, nlng = _step_towards(
                        unit.current_lat, unit.current_lng, dlat, dlng, self._step_km
                    )
                    unit.current_lat, unit.current_lng = float(nlat), float(nlng)
                    if o.status != OrderStatus.EXECUTING:
                        o.status = OrderStatus.EXECUTING
                    events.append(
                        LedgerEvent(
                            event_type="UNIT_MOVED",
                            tick=now.tick,
                            initiator_id=o.unit_id,
                            detail={"order_id": o.id, "lat": nlat, "lng": nlng},
                        )
                    )
                # 熱狀態：累積 per-unit 位置 diff → Kernel 廣播 STATE_DIFF（前端移動圖標）。
                self._hot_state.update_unit(
                    o.unit_id, {"lat": unit.current_lat, "lng": unit.current_lng}
                )
            db.commit()
        return events
