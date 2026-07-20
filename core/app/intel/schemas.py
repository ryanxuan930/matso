"""Intel 查詢的 Pydantic 視圖（O3.3）——**下發前端的投影，已依情報等級去識別化**。

紅線：`target_unit_id`（ground truth 連結）**永不下發**；contact_id 用 IntelContact 自身 id。
designation/type/faction 依 fidelity 逐級揭露（DETECTED 全隱 → IDENTIFIED 全揭）。
"""

from __future__ import annotations

from pydantic import BaseModel

from app.models.enums import IntelFidelity


class ContactView(BaseModel):
    contact_id: str  # 觀測方自己的 IntelContact id（非 target ground-truth id）
    fidelity: IntelFidelity
    last_seen_tick: int
    lat: float
    lng: float
    error_radius_m: float
    # 以下依 fidelity 逐級揭露；未達等級為 None
    unit_type: str | None = None  # CLASSIFIED+ 揭露（unit_level）
    designation: str | None = None  # IDENTIFIED 才揭露
    faction: str | None = None  # IDENTIFIED 才揭露敵我
