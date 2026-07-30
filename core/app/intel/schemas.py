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
    # ⚠ 這一欄過去叫 `unit_type`，但裝的是 **unit_level（階層）**。
    # 名實不符的後果不是無害的誤會：前端 `functionId()` 查的是兵科表（INFANTRY/ARMOR…），
    # 拿 'PLATOON' 去查永遠 miss（白做）；而若有人**照欄位名**把它接到階層符號上，
    # 敵方編成就會在 CLASSIFIED 這一級直接畫上圖，且因為欄位叫 unit_type，review 看不出來。
    echelon: str | None = None  # CLASSIFIED+ 揭露（編制層級）
    branch: str | None = None  # CLASSIFIED+ 揭露（兵科）
    designation: str | None = None  # IDENTIFIED 才揭露
    faction: str | None = None  # IDENTIFIED 才揭露敵我
