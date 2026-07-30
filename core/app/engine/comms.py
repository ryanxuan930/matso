"""通訊子系統（#33 / SPEC §6）——每通訊 tick 重算各單位鏈路狀態，寫熱狀態 + 記狀態轉移事件。

取代 NoOpCommsSystem：以 link_budget 純模型算每陣營網狀連通（multi-hop 中繼），把
`comms_state`（ONLINE/DEGRADED/OFFLINE）寫入熱狀態（供 COP 顯示 + 後續指令延遲/凍結後果），
狀態改變時記 `COMMS_STATE_CHANGED`。決定性：純公式，不用 RNG；每 N tick 重算一次（省算）。

WP-C5 起本系統同時是**位置回報**的產出端：依 `position_report_interval` 把 `report_lat/lng/tick`
寫入熱狀態（OFFLINE 不再更新＝凍結、DEGRADED 降頻＝落後）。真實 lat/lng 照常由 movement 演進；
「指揮所看得到什麼」由投影層（`/units`、STATE_DIFF、AI context）依這三欄決定。

紅線：Kernel 為熱狀態唯一寫入者（本系統經 hot_state.update_unit 累積 diff）；同步 DB 移到執行緒。
"""

from __future__ import annotations

import asyncio

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.comms import (
    REPORT_LAT_KEY,
    REPORT_LNG_KEY,
    REPORT_TICK_KEY,
    CommsNode,
    CommsProfile,
    LinkState,
    last_position_report,
    mesh_states,
    position_report_due,
)
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

    def gain(default: float) -> float:
        """天線增益：**契約寫 `antenna_gain_dbi`**（dBi 才是天線增益的正確單位）。

        這裡過去只讀 `antenna_gain_db`——差一個 `i`，而軍械庫 UI 是照契約寫
        `antenna_gain_dbi` 的（`armory.vue`）。結果是**經 UI 建立的每一組通信裝備，
        天線增益都被靜默忽略、永遠吃預設值**。契約名優先，舊名保留為退路。
        """
        for key in ("antenna_gain_dbi", "antenna_gain_db"):
            v = stats.get(key)
            if isinstance(v, (int, float)):
                return float(v)
        return default

    d = CommsProfile()
    return CommsProfile(
        tx_power_dbm=f("tx_power_dbm", d.tx_power_dbm),
        antenna_gain_db=gain(d.antenna_gain_db),
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
            positions = {n.unit_id: (n.lat, n.lng) for n in nodes}
            for uid, st in states.items():
                prev = self._hot.get_unit(uid) or {}
                changes = self._position_report(prev, uid, st, positions, now)
                prev_state = prev.get("comms_state")
                if prev_state != st.value:
                    changes["comms_state"] = st.value
                if changes:
                    self._hot.update_unit(uid, changes)
                if prev_state != st.value and prev_state is not None:
                    events.append(self._event(uid, prev_state, st, now))  # 首次播種不記事件
        return events

    def _position_report(
        self,
        prev: dict,  # type: ignore[type-arg]
        unit_id: str,
        state: LinkState,
        positions: dict[str, tuple[float, float]],
        now: SimTime,
    ) -> dict[str, object]:
        """WP-C5：到期則落一筆位置回報（`report_*`）。回傳要寫入熱狀態的變更。

        這是 SPEC §6.2「位置回報」的產出端；消費端在投影層（`/units`、STATE_DIFF、AI context）。
        **真實 lat/lng 一概不動**——凍結的是「指揮所看得到什麼」，不是單位本身。

        兩種情況會寫：回報週期到期（ONLINE 每 interval、DEGRADED ×3、OFFLINE 永不），
        或**該單位還沒有任何回報**。後者是刻意的：開局即失聯的單位若連一筆回報都沒有，
        己方 COP 會變成「自己的部隊位置不明」——但部署位置本來就是指揮所知道的。
        """
        pos = positions.get(unit_id)
        if pos is None:
            return {}
        seeded = last_position_report(prev) is not None
        if seeded and not position_report_due(state, now.tick, self._interval):
            return {}
        lat, lng = pos
        return {REPORT_LAT_KEY: lat, REPORT_LNG_KEY: lng, REPORT_TICK_KEY: now.tick}

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
