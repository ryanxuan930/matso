"""種子裝備配發（O4.x ENGAGE 武器選擇）——把 SEED_WEAPONS 落為 EquipmentTemplate，
並為 session 內單位配發 EquipmentInstance（供資料驅動的 ENGAGE 武器/彈種選擇）。

**紅線**：純確定性（無 datetime/裸 random）；只寫既有結構（models 唯讀跟隨 prisma schema）。
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adjudication.seed_weapons import SEED_WEAPONS
from app.models.tables import EquipmentInstance, EquipmentTemplate, TacticalUnit


def ensure_weapon_templates(db: Session) -> dict[str, str]:
    """把 SEED_WEAPONS upsert 為 EquipmentTemplate（category=KINETIC）；回 {name: template_id}。

    冪等：以 name 查既有列，存在則更新 baseStats，不存在則新建。
    """
    out: dict[str, str] = {}
    for name, stats in SEED_WEAPONS.items():
        tmpl = db.execute(
            select(EquipmentTemplate).where(EquipmentTemplate.name == name)
        ).scalar_one_or_none()
        if tmpl is None:
            tmpl = EquipmentTemplate(name=name, category="KINETIC", base_stats=dict(stats))
            db.add(tmpl)
            db.flush()
        else:
            tmpl.base_stats = dict(stats)
        out[name] = tmpl.id
    return out


def seed_session_equipment(db: Session, session_id: str, default: str = "RIFLE_556") -> int:
    """為 session 內每個尚無裝備的單位配發一件預設武器（EquipmentInstance，ammo=100）。回配發件數。

    冪等：已有任何裝備的單位略過。default 須為 SEED_WEAPONS 的 key。
    """
    templates = ensure_weapon_templates(db)
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
