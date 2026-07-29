"""Faction-scoped intel 查詢服務（O3.3）——投影 + 去識別化（fog of war）。

`visible_contacts(session, faction)` 回傳該 faction 的敵情視圖，依 fidelity 逐級揭露；
**永不回傳其他 faction 的 contacts，也永不下發 target_unit_id / 未達等級的 ground truth。**
White Cell 的全知視角走 `god_view`（僅 WHITE_CELL 可用），與作戰方路徑完全分離。

WP-C5 敵情粗化（SPEC_FULL §6.2）：`visible_contacts` 收 `granularity` 參數——觀測陣營整體
通聯不良時，位置量化到 h3 res-6 格心、fidelity 上限 DETECTED。**參數無預設值以外的旁路**：
粒度由呼叫端（API / AI world_view）算好傳入，本層只負責一致地套用。
"""

from __future__ import annotations

import h3
from sqlalchemy.orm import Session

from app.comms import IntelGranularity
from app.factions import WHITE_CELL
from app.intel import store
from app.intel.schemas import ContactView
from app.models.enums import IntelFidelity
from app.models.tables import IntelContact, TacticalUnit

# 粗化解析度：h3 res-6 ≈ 3km 級的格心（SPEC_V2 §6 WP-C5 明訂）。
COARSE_H3_RES = 6


def coarse_error_radius_m() -> float:
    """粗化後的誤差半徑下限＝該解析度的六邊形邊長。

    只換座標而不放大誤差半徑，等於謊稱「我對這個格心有公尺級把握」——前端畫的誤差圈
    會比實際知識精確得多。
    """
    return float(h3.average_hexagon_edge_length(COARSE_H3_RES, unit="m"))


def coarsen_position(lat: float, lng: float) -> tuple[float, float]:
    """量化到 h3 res-6 格心（同格的多筆接觸會疊在同一點——那正是「粗化」的意思）。"""
    return h3.cell_to_latlng(h3.latlng_to_cell(lat, lng, COARSE_H3_RES))  # type: ignore[no-any-return]


class IntelService:
    def __init__(self, db: Session) -> None:
        self._db = db

    def visible_contacts(
        self,
        session_id: str,
        faction: str,
        granularity: IntelGranularity = IntelGranularity.FULL,
    ) -> list[ContactView]:
        contacts = store.query(self._db, session_id, faction)
        return [self._project(c, granularity=granularity) for c in contacts]

    def god_view(self, session_id: str, faction: str) -> list[ContactView]:
        """White Cell 全知：所有 faction 的 contacts（統裁/教學）。非 WHITE_CELL 一律拒絕。

        不套粗化：統裁看的是 ground truth，通聯不良是**作戰方**的問題。
        """
        if faction != WHITE_CELL:
            raise PermissionError("god_view 僅 WHITE_CELL 可用")
        all_contacts = self._db.query(IntelContact).filter_by(session_id=session_id).all()
        return [self._project(c, reveal_all=True) for c in all_contacts]

    def _project(
        self,
        contact: IntelContact,
        reveal_all: bool = False,
        granularity: IntelGranularity = IntelGranularity.FULL,
    ) -> ContactView:
        """依 fidelity 去識別化（再依 granularity 降級）。target_unit_id 永不進視圖。"""
        coarse = not reveal_all and granularity is not IntelGranularity.FULL
        lat, lng, err = contact.last_seen_lat, contact.last_seen_lng, contact.error_radius_m
        fidelity = contact.fidelity
        if coarse:
            lat, lng = coarsen_position(lat, lng)
            err = max(err, coarse_error_radius_m())
            # DETECTED 是最低等級，故「上限 DETECTED」＝一律 DETECTED。**必須在下方揭露判定
            # 之前降級**——只粗化座標卻留著番號/陣營，等於 fidelity 欄位與內容不符。
            fidelity = IntelFidelity.DETECTED
        view = ContactView(
            contact_id=contact.id,
            fidelity=fidelity,
            last_seen_tick=contact.last_seen_tick,
            lat=lat,
            lng=lng,
            error_radius_m=err,
        )
        rank = _RANK[fidelity]
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
