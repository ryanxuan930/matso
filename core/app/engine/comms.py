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
import logging
from collections.abc import Callable

import h3
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
from app.weather import WeatherState

_LOG = logging.getLogger("engine.comms")

# 指揮節點門檻：BATTALION（含）以上視為指揮/中繼錨點（單位規模排名越小越大）。
_SIZE_RANK = {level: rank for rank, level in enumerate(UnitLevel)}
_COMMAND_RANK = _SIZE_RANK[UnitLevel.BATTALION]

# 天線離地高（公尺）——與交戰/偵測的觀測高同量級（車裝鞭狀天線約 3m、架設桿約 10m）。
_ANTENNA_HEIGHT_M = 10.0

# 地形遮蔽快取的格網解析度。**這是本設計的關鍵**：兩點間的視線是地形的靜態函數，
# 只要雙方還在同一對格子裡答案就不變。單位每 tick 只走幾百公尺、多數時候根本沒動，
# 於是穩態下遮蔽查詢近乎零次——同 `movement._terrain_cost_cache` 的招式。
# res 9（邊長約 175m）：夠細到不把山另一頭的位置當成同一格，又夠粗到讓靜止部隊全數命中快取。
_LOS_CACHE_RES = 9


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
    """滿足 Kernel 的 `CommsSystem` 介面。每 interval tick 重算各陣營通訊網狀狀態。

    ## 過去這裡只有距離

    `mesh_states` 一直收得下地形遮蔽與天氣衰減兩個參數，但活執行期呼叫的是
    **`mesh_states(nodes)`**——兩個都沒傳。於是山稜線後面的部隊與平原上同距離的部隊
    通聯完全一樣，`CellEffects.rf_attenuation_db` 在整個程式裡一個消費者都沒有。
    無線電在兵推裡最重要的兩件事（擋在哪、天氣多壞）就這樣不存在。

    兩者都改為**注入式**：不注入 → 逐位元維持舊行為（既有測試/golden 不動）。
    """

    def __init__(
        self,
        *,
        session_id: str,
        session_factory: sessionmaker,  # type: ignore[type-arg]
        hot_state: HotStateStore,
        interval_ticks: int = 5,
        gateway: object | None = None,
        weather_for: Callable[[], WeatherState | None] | None = None,
    ) -> None:
        self._session_id = session_id
        self._session_factory = session_factory
        self._hot = hot_state
        self._interval = max(1, interval_ticks)
        # 地形 gateway（有 `has_los` 即可）。None → 不查遮蔽（舊行為）。
        self._gateway = gateway
        # WP-C4b：逐 tick 的天氣。回呼而非值——傳快照進來整局就停在那一份。
        self._weather_for = weather_for
        # 格對 → 是否遮蔽。靜止部隊穩態命中率接近 100%，見 `_LOS_CACHE_RES` 說明。
        self._los_cache: dict[tuple[str, str], bool] = {}

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
        weather = self._weather_for() if self._weather_for is not None else None
        for _faction, nodes in by_faction.items():
            states = mesh_states(
                nodes,
                obstructed=self._obstructions(nodes),
                attenuation_db=self._attenuations(nodes, weather),
                # 干擾（EW）尚無來源：沒有干擾機單位、契約也沒有這個欄位。
                # **刻意留 0 而不是編一個值**——假的干擾比沒有干擾更難察覺。
            )
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

    def _obstructions(self, nodes: list[CommsNode]) -> dict[tuple[str, str], bool]:
        """逐鏈路地形遮蔽。無 gateway → 空 dict（＝舊行為，全部視為通視）。

        gateway 掛掉時**退回通視**而不是遮蔽：地形服務抖一下就讓全軍失聯，
        比慢一拍嚴重得多（同 `make_detect_env` 的退化紀律）。
        """
        gateway = self._gateway
        has_los = getattr(gateway, "has_los", None)
        if has_los is None:
            return {}
        cells = {n.unit_id: h3.latlng_to_cell(n.lat, n.lng, _LOS_CACHE_RES) for n in nodes}
        out: dict[tuple[str, str], bool] = {}
        ids = [n.unit_id for n in nodes]
        by_id = {n.unit_id: n for n in nodes}
        for i, a in enumerate(ids):
            for b in ids[i + 1 :]:
                key = (cells[a], cells[b]) if cells[a] <= cells[b] else (cells[b], cells[a])
                if key[0] == key[1]:
                    continue  # 同一格 → 必然通視，不必問
                cached = self._los_cache.get(key)
                if cached is None:
                    na, nb = by_id[a], by_id[b]
                    try:
                        outcome = has_los(
                            (na.lat, na.lng, _ANTENNA_HEIGHT_M),
                            (nb.lat, nb.lng, _ANTENNA_HEIGHT_M),
                        )
                        cached = not bool(getattr(outcome, "visible", True))
                    except Exception:
                        _LOG.warning("通聯遮蔽查詢失敗，該鏈路退回通視")
                        cached = False
                    self._los_cache[key] = cached
                if cached:
                    out[(a, b)] = True
        return out

    def _attenuations(
        self, nodes: list[CommsNode], weather: WeatherState | None
    ) -> dict[tuple[str, str], float]:
        """逐鏈路天氣 RF 衰減（dB）。取**兩端較大者**：一端在雷雨裡整條鏈路就受罰。

        無天氣快照 → 空 dict（舊行為）。`rf_attenuation_db` 在契約與 `CellEffects` 裡
        一直都有，只是全系統沒有任何消費者。
        """
        if weather is None:
            return {}
        res = weather.resolution()
        per_unit = {
            n.unit_id: weather.effects_at(h3.latlng_to_cell(n.lat, n.lng, res)).rf_attenuation_db
            for n in nodes
        }
        out: dict[tuple[str, str], float] = {}
        ids = [n.unit_id for n in nodes]
        for i, a in enumerate(ids):
            for b in ids[i + 1 :]:
                worst = max(per_unit[a], per_unit[b])
                if worst > 0:
                    out[(a, b)] = worst
        return out

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
