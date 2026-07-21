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
from app.intel import store
from app.intel.sensor import DetectionEnv, SensorProfile
from app.intel.sweep import Contact, SensorUnit, TargetUnit, sweep
from app.state.hot_state import HotStateStore
from app.state.ledger import LedgerEvent

SensorLookup = Callable[[str], SensorProfile | None]
FactionLookup = Callable[[str], str]
DetectEnvLookup = Callable[[SensorUnit, TargetUnit], DetectionEnv]


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
    ) -> None:
        self._db = db
        self._session_id = session_id
        self._hot = hot_state
        self._rng = rng
        self._sensor_for = sensor_for
        self._faction_for = faction_for
        self._env_for = env_for
        self._resolution = resolution

    async def sweep(self, now: SimTime) -> list[LedgerEvent]:
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
            observers, candidates, self._env_for, self._rng, now.tick, self._resolution
        )
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
