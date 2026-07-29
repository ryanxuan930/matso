"""投影層用的**唯讀**通聯視圖（WP-C5；SPEC_FULL §6.2）。

REST 端點（`/units`、`/intel`，以及複用它們的 `/state` 快照）要套用通聯後果——位置凍結與
敵情粗化——就得知道每個單位當下的鏈路狀態與最後一次位置回報。那些資料只存在**熱狀態**
（Kernel 每 comms tick 寫入 Redis），DB 沒有：`TacticalUnit.comms_status` 自播種後從未被寫過。

本模組是 Kernel 之外唯一讀單位熱狀態的地方，且**只讀**（single-writer 原則不受影響）。
不走 `RedisHotState`：那個類別帶 in-process mirror cache（為 Kernel 的單寫者情境設計），
在請求生命週期裡建一個只為讀一次，白白多一層；且它的 `get_all()` 用 SCAN 掃 keyspace，
而這裡單位 id 是已知的 → 一次 MGET 就夠。

降級哲學（延續 `comms/consequences` 的註解）：Redis 不可達 → 回空視圖 → 一切照真實資料呈現。
基礎設施故障不該讓玩家看不到自己的部隊；而這裡「放寬」的極限是**自己陣營的自己單位**，
敵方可見性完全不經本模組（那是 intel/units 的 faction 過濾在管）。
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from app.comms import (
    IntelGranularity,
    LinkState,
    ProjectedPosition,
    faction_link_state,
    intel_granularity,
    parse_link_state,
    project_position,
)
from app.state.hot_state import UnitState, unit_key


@dataclass(frozen=True, slots=True)
class CommsView:
    """一個 session 的單位熱狀態切片（查無單位＝該局沒在跑，一律 ONLINE 樂觀預設）。"""

    units: Mapping[str, UnitState]

    def link_of(self, unit_id: str) -> LinkState:
        return parse_link_state(self.units.get(unit_id, {}).get("comms_state"))

    def position_of(self, unit_id: str) -> ProjectedPosition | None:
        """該單位在**己方視角**應呈現的位置；None ＝ ONLINE，照用真實位置。"""
        state = self.units.get(unit_id)
        return project_position(state) if state else None

    def posture(self, unit_ids: Iterable[str]) -> LinkState:
        """一群單位（＝某陣營）的整體通聯姿態。"""
        return faction_link_state(self.link_of(uid) for uid in unit_ids)

    def granularity(self, unit_ids: Iterable[str]) -> IntelGranularity:
        """該陣營的敵情粒度（FULL / COARSE / FROZEN）。"""
        return intel_granularity(self.posture(unit_ids))


EMPTY_COMMS_VIEW = CommsView(units={})


def load_comms_view(client: Any, session_id: str, unit_ids: Sequence[str]) -> CommsView:
    """以單次 MGET 讀取這些單位的熱狀態。Redis 不可達/資料壞 → 空視圖（見模組 docstring）。"""
    if not unit_ids:
        return EMPTY_COMMS_VIEW
    try:
        raw = client.mget([unit_key(session_id, uid) for uid in unit_ids])
    except Exception:
        return EMPTY_COMMS_VIEW
    units: dict[str, UnitState] = {}
    for uid, blob in zip(unit_ids, raw or [], strict=False):
        if not blob:
            continue
        try:
            loaded = json.loads(blob)
        except (TypeError, ValueError):
            continue
        if isinstance(loaded, dict):
            units[uid] = loaded
    return CommsView(units=units)
