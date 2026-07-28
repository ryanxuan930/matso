"""Kernel 偵測接線（O3.6）——每 tick 由熱狀態建感測/目標清單，跑 sweep，落 intel store。

**紅線**：faction-scope 在 intel store/service 強制（本層只寫入 per-faction contacts）。
SENSOR_CONTACT 事件寫入 Ledger（ground truth 記錄，White Cell/AAR 可讀）；前端投影的
去識別化仍由 IntelService 負責。sensor/faction/env（LOS/天氣）以 callable 注入。
"""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy.orm import Session

from app.engine.clock import SimTime
from app.engine.rng import DeterministicRNG
from app.factions import FactionRelations
from app.intel import store
from app.intel.sensor import DetectionEnv, SensorProfile
from app.intel.sweep import Contact, SensorUnit, TargetUnit, sweep
from app.models.enums import IntelFidelity
from app.state.hot_state import HotStateStore
from app.state.ledger import LedgerEvent

SensorLookup = Callable[[str], SensorProfile | None]
FactionLookup = Callable[[str], str]
DetectEnvLookup = Callable[[SensorUnit, TargetUnit], DetectionEnv]


_FIDELITY_RANK = {
    IntelFidelity.DETECTED: 0,
    IntelFidelity.CLASSIFIED: 1,
    IntelFidelity.IDENTIFIED: 2,
}


def _best_per_target(contacts: list[Contact]) -> list[Contact]:
    """同陣營多名觀測者看到同一目標 → 收斂成一筆（取最佳 fidelity）。

    store 每筆 contact 都是一次 SELECT + 寫；一個陣營十幾個單位同時看到同一個敵人時，
    十幾次寫入落在**同一列**上，純屬浪費。位置對同一目標而言各觀測者一致（皆取自熱狀態
    ground truth），故收斂後語義不變——且改為「取最佳」而非「看誰最後寫」，更具決定性。
    """
    best: dict[tuple[str, str], Contact] = {}
    for c in contacts:
        key = (c.observer_faction, c.target_unit_id)
        cur = best.get(key)
        if cur is None or _FIDELITY_RANK[c.fidelity] > _FIDELITY_RANK[cur.fidelity]:
            best[key] = c
    return [best[k] for k in sorted(best)]


class SensorSweepSystem:
    def __init__(
        self,
        db: Session,
        session_id: str,
        hot_state: HotStateStore,
        rng: DeterministicRNG,
        sensor_for: SensorLookup,
        faction_for: FactionLookup,
        env_for: DetectEnvLookup,
        resolution: int = 8,
        relations: FactionRelations | None = None,
        interval_ticks: int = 5,
    ) -> None:
        self._db = db
        self._session_id = session_id
        self._hot = hot_state
        self._rng = rng
        self._sensor_for = sensor_for
        self._faction_for = faction_for
        self._env_for = env_for
        self._resolution = resolution
        # None → sweep 內退回全 HOSTILE 預設（僅同陣營互不偵測）。執行期目前確實取不到
        # 該局的關係矩陣（scenario loader 建完就沒持久化，AI orchestrator 亦同樣走預設），
        # 故先保留注入點；待關係矩陣可於執行期取得後接上，ALLIED 陣營即不再互相成為 contact。
        self._relations = relations
        # 掃描節奏（比照 CommsSystem 每 5 tick 重算）。1 tick＝1 sim 分鐘，實跑約 0.5s/tick；
        # 密集戰場的射程內配對數可達數百，每 tick 全掃會把 DB 打爆（每個 contact 一次
        # SELECT+寫）。每 interval 掃一次在 sim 時間尺度上仍屬即時（數分鐘內發現敵蹤）。
        self._interval = max(1, interval_ticks)

    async def sweep(self, now: SimTime) -> list[LedgerEvent]:
        if now.tick % self._interval != 0:
            return []  # 省算：只在每 interval tick 掃描
        observers: list[SensorUnit] = []
        candidates: list[TargetUnit] = []
        for unit_id, state in self._hot.get_all().items():
            lat, lng = state.get("lat"), state.get("lng")
            if lat is None or lng is None:
                continue
            faction = self._faction_for(unit_id)
            candidates.append(TargetUnit(unit_id, faction, float(lat), float(lng)))
            sensor = self._sensor_for(unit_id)
            if sensor is not None:
                observers.append(SensorUnit(unit_id, faction, float(lat), float(lng), sensor))

        contacts = sweep(
            observers,
            candidates,
            self._env_for,
            self._rng,
            now.tick,
            self._resolution,
            self._relations,
        )
        contacts = _best_per_target(contacts)
        store.record_all(self._db, self._session_id, contacts)
        self._db.commit()
        return [self._event(c) for c in contacts]

    def _event(self, contact: Contact) -> LedgerEvent:
        return LedgerEvent(
            event_type="SENSOR_CONTACT",
            tick=contact.tick,
            target_id=contact.target_unit_id,
            ai_decision={
                "observer_faction": contact.observer_faction,
                "fidelity": contact.fidelity.value,
            },
        )
