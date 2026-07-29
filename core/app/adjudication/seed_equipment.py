"""種子裝備配發（O4.x ENGAGE 武器選擇）——把 SEED_WEAPONS 落為 EquipmentTemplate，
並為 session 內單位配發 EquipmentInstance（供資料驅動的 ENGAGE 武器/彈種選擇）。

**紅線**：純確定性（無 datetime/裸 random）；只寫既有結構（models 唯讀跟隨 prisma schema）。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adjudication.seed_weapons import (
    SEED_ARTILLERY,
    SEED_LOGISTICS,
    SEED_VEHICLES,
    SEED_WEAPONS,
)
from app.governance.guard import params_sealed
from app.models.tables import EquipmentInstance, EquipmentTemplate, TacticalUnit


def _upsert_templates(
    db: Session, seed: dict[str, dict[str, Any]], category: str
) -> dict[str, str]:
    """把一組 seed 定義 upsert 為 EquipmentTemplate（冪等：以 name 查，更新 baseStats 或新建）。

    ⚠ **WP-B4：簽證期間不覆寫既有範本的 baseStats**。這條路徑跑在**每一次**由想定開局時
    （`scenario/loader.py`）——若照常覆寫，在已簽證的演習裡新開一個預推/分析局，
    就會靜靜改掉被鎖住的那張表，然後**該演習的每一局都會因為雜湊不符而拒起**。

    只擋覆寫、不擋新建：新範本不改變任何既有值，也就動不到簽證的內容
    （簽證雜湊涵蓋全表，新建確實會讓雜湊變——但那是「有人在演習中新增裝備」，
    本來就該被開局比對抓到；靜靜跳過新建反而會讓增援單位拿不到武器）。
    """
    sealed = params_sealed(db)
    out: dict[str, str] = {}
    for name, stats in seed.items():
        tmpl = db.execute(
            select(EquipmentTemplate).where(EquipmentTemplate.name == name)
        ).scalar_one_or_none()
        if tmpl is None:
            tmpl = EquipmentTemplate(name=name, category=category, base_stats=dict(stats))
            db.add(tmpl)
            db.flush()
        elif not sealed:
            tmpl.base_stats = dict(stats)
        out[name] = tmpl.id
    return out


def ensure_weapon_templates(db: Session) -> dict[str, str]:
    """把 SEED_WEAPONS upsert 為 EquipmentTemplate（category=KINETIC）；回 {name: template_id}。"""
    return _upsert_templates(db, SEED_WEAPONS, "KINETIC")


def ensure_mobility_templates(db: Session) -> dict[str, str]:
    """把火砲（ARTILLERY）+ 載具（VEHICLE）範本 upsert（#80 Phase A）——使其 `base_stats.mobility`
    可供 `UnitMobilityResolver` 導出機動速度。回 {name: template_id}。單純新增範本列，不改任何
    既有單位編裝（不影響交戰/golden）。"""
    out = _upsert_templates(db, SEED_ARTILLERY, "ARTILLERY")
    out.update(_upsert_templates(db, SEED_VEHICLES, "VEHICLE"))
    out.update(_upsert_templates(db, SEED_LOGISTICS, "LOGISTICS"))  # #85 補給車
    return out


def seed_session_equipment(db: Session, session_id: str, default: str = "RIFLE_556") -> int:
    """為 session 內每個尚無裝備的單位配發一件預設武器（EquipmentInstance，ammo=100）。回配發件數。

    冪等：已有任何裝備的單位略過。default 須為 SEED_WEAPONS 的 key。
    """
    templates = ensure_weapon_templates(db)
    ensure_mobility_templates(db)  # #80：確保載具/火砲範本存在（供機動速度導出；不自動配發）
    default_tid = templates[default]
    units = (
        db.execute(select(TacticalUnit).where(TacticalUnit.session_id == session_id))
        .scalars()
        .all()
    )
    count = 0
    for unit in units:
        has_equipment = db.execute(
            select(EquipmentInstance.id).where(EquipmentInstance.owner_id == unit.id).limit(1)
        ).first()
        if has_equipment is not None:
            continue
        db.add(
            EquipmentInstance(
                template_id=default_tid,
                owner_id=unit.id,
                current_state={"ammo": 100},
            )
        )
        count += 1
    db.flush()
    return count
