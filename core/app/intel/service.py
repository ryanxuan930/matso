"""Faction-scoped intel 查詢服務（O3.3）——投影 + 去識別化（fog of war）。

`visible_contacts(session, faction)` 回傳該 faction 的敵情視圖，依 fidelity 逐級揭露；
**永不回傳其他 faction 的 contacts，也永不下發 target_unit_id / 未達等級的 ground truth。**
White Cell 的全知視角走 `god_view`（僅 WHITE_CELL 可用），與作戰方路徑完全分離。
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.factions import WHITE_CELL
from app.intel import store
from app.intel.schemas import ContactView
from app.models.enums import IntelFidelity
from app.models.tables import IntelContact, TacticalUnit


class IntelService:
    def __init__(self, db: Session) -> None:
        self._db = db

    def visible_contacts(self, session_id: str, faction: str) -> list[ContactView]:
        contacts = store.query(self._db, session_id, faction)
        return [self._project(c) for c in contacts]

    def god_view(self, session_id: str, faction: str) -> list[ContactView]:
        """White Cell 全知：所有 faction 的 contacts（統裁/教學）。非 WHITE_CELL 一律拒絕。"""
        if faction != WHITE_CELL:
            raise PermissionError("god_view 僅 WHITE_CELL 可用")
        all_contacts = self._db.query(IntelContact).filter_by(session_id=session_id).all()
        return [self._project(c, reveal_all=True) for c in all_contacts]

    def _project(self, contact: IntelContact, reveal_all: bool = False) -> ContactView:
        """依 fidelity 去識別化。target_unit_id（ground truth）永不進視圖。"""
        view = ContactView(
            contact_id=contact.id,
            fidelity=contact.fidelity,
            last_seen_tick=contact.last_seen_tick,
            lat=contact.last_seen_lat,
            lng=contact.last_seen_lng,
            error_radius_m=contact.error_radius_m,
        )
        rank = _RANK[contact.fidelity]
        if reveal_all or rank >= _RANK[IntelFidelity.CLASSIFIED]:
            target = self._db.get(TacticalUnit, contact.target_unit_id)
            if target is not None:
                view.unit_type = target.unit_level.value
                if reveal_all or rank >= _RANK[IntelFidelity.IDENTIFIED]:
                    view.designation = target.designation
                    view.faction = target.faction
        return view


_RANK = {
    IntelFidelity.DETECTED: 0,
    IntelFidelity.CLASSIFIED: 1,
    IntelFidelity.IDENTIFIED: 2,
}
