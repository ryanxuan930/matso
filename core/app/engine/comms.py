"""通訊子系統（#33 / SPEC §6）——每通訊 tick 重算各單位鏈路狀態，寫熱狀態 + 記狀態轉移事件。

取代 NoOpCommsSystem：以 link_budget 純模型算每陣營網狀連通（multi-hop 中繼），把
`comms_state`（ONLINE/DEGRADED/OFFLINE）寫入熱狀態（供 COP 顯示 + 後續指令延遲/凍結後果），
狀態改變時記 `COMMS_STATE_CHANGED`。決定性：純公式，不用 RNG；每 N tick 重算一次（省算）。

紅線：Kernel 為熱狀態唯一寫入者（本系統經 hot_state.update_unit 累積 diff）；同步 DB 移到執行緒。
"""

from __future__ import annotations

import asyncio

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.comms import CommsNode, CommsProfile, LinkState, mesh_states
from app.engine.clock import SimTime
from app.models import EquipmentInstance, EquipmentTemplate, TacticalUnit
from app.models.enums import UnitLevel
from app.state.hot_state import HotStateStore
from app.state.ledger import LedgerEvent

# 指揮節點門檻：BATTALION（含）以上視為指揮/中繼錨點（單位規模排名越小越大）。
_SIZE_RANK = {level: rank for rank, level in enumerate(UnitLevel)}
_COMMAND_RANK = _SIZE_RANK[UnitLevel.BATTALION]


def _profile_from_stats(stats: dict) -> CommsProfile:  # type: ignore[type-arg]
    def f(key: str, default: float) -> float:
        v = stats.get(key)
        return float(v) if isinstance(v, (int, float)) else default

    d = CommsProfile()
    return CommsProfile(
        tx_power_dbm=f("tx_power_dbm", d.tx_power_dbm),
        antenna_gain_db=f("antenna_gain_db", d.antenna_gain_db),
        freq_mhz=f("freq_mhz", d.freq_mhz),
        rx_sensitivity_dbm=f("rx_sensitivity_dbm", d.rx_sensitivity_dbm),
    )


class CommsSystem:
    """滿足 Kernel 的 `CommsSystem` 介面。每 interval tick 重算各陣營通訊網狀狀態。"""

    def __init__(
        self,
        *,
        session_id: str,
        session_factory: sessionmaker,  # type: ignore[type-arg]
        hot_state: HotStateStore,
        interval_ticks: int = 5,
    ) -> None:
        self._session_id = session_id
        self._session_factory = session_factory
        self._hot = hot_state
        self._interval = max(1, interval_ticks)

    async def evaluate(self, now: SimTime) -> list[LedgerEvent]:
        if now.tick % self._interval != 0:
            return []  # 省算：只在每 interval tick 重算通訊
        return await asyncio.to_thread(self._evaluate_sync, now)

    def _evaluate_sync(self, now: SimTime) -> list[LedgerEvent]:
        with self._session_factory() as db:
            units = db.scalars(
                select(TacticalUnit).where(TacticalUnit.session_id == self._session_id)
            ).all()
            # 每陣營一張網（僅本軍中繼）。
            by_faction: dict[str, list[CommsNode]] = {}
            for u in units:
                state = self._hot.get_unit(u.id) or {}
                lat = state.get("lat", u.current_lat)
                lng = state.get("lng", u.current_lng)
                if lat is None or lng is None:
                    continue
                profile = self._comms_profile(db, u.id)
                is_cmd = _SIZE_RANK.get(u.unit_level, 99) <= _COMMAND_RANK or bool(
                    (u.attributes or {}).get("is_command")
                )
                by_faction.setdefault(u.faction, []).append(
                    CommsNode(u.id, float(lng), float(lat), profile, is_command=is_cmd)
                )

        events: list[LedgerEvent] = []
        for _faction, nodes in by_faction.items():
            states = mesh_states(nodes)
            for uid, st in states.items():
                prev = self._hot.get_unit(uid) or {}
                prev_state = prev.get("comms_state")
                if prev_state != st.value:
                    self._hot.update_unit(uid, {"comms_state": st.value})
                    if prev_state is not None:  # 首次播種不記事件，僅記真正轉移
                        events.append(self._event(uid, prev_state, st, now))
        return events

    def _comms_profile(self, db: object, unit_id: str) -> CommsProfile:
        """單位的通訊裝備（category=COMMS）→ profile；無則預設手持 VHF。"""
        insts = db.scalars(  # type: ignore[attr-defined]
            select(EquipmentInstance).where(EquipmentInstance.owner_id == unit_id)
        ).all()
        for inst in insts:
            tmpl = db.get(EquipmentTemplate, inst.template_id)  # type: ignore[attr-defined]
            if tmpl is not None and tmpl.category == "COMMS":
                return _profile_from_stats(dict(tmpl.base_stats or {}))
        return CommsProfile()

    def _event(self, uid: str, prev: str, st: LinkState, now: SimTime) -> LedgerEvent:
        return LedgerEvent(
            event_type="COMMS_STATE_CHANGED",
            tick=now.tick,
            initiator_id=uid,
            ai_decision={"from": prev, "to": st.value},
        )
