"""Per-faction intel store（SPEC §7.2 / §13.3）——fog of war 的後端實作基礎。

**紅線（faction-scope 後端強制）**：`query` 一律以 faction 過濾；沒有任何方法會回傳跨陣營
的 IntelContact 或 ground truth。每一方看到的世界＝自己的偵測結果集合。

upsert 規則：同 (session, faction, target) 只留一筆——位置/tick 更新為最新一次觀測；
情報等級取歷來最佳（一旦 IDENTIFIED 不因後續較差觀測降級）。
"""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.intel.sweep import Contact
from app.models.enums import IntelFidelity
from app.models.tables import IntelContact

_FIDELITY_RANK = {
    IntelFidelity.DETECTED: 0,
    IntelFidelity.CLASSIFIED: 1,
    IntelFidelity.IDENTIFIED: 2,
}


def record(db: Session, session_id: str, contact: Contact) -> None:
    """更新（或新增）觀測方對某目標的情報。呼叫方負責 commit。"""
    existing = db.scalar(
        select(IntelContact).where(
            IntelContact.session_id == session_id,
            IntelContact.faction == contact.observer_faction,
            IntelContact.target_unit_id == contact.target_unit_id,
        )
    )
    if existing is None:
        db.add(
            IntelContact(
                session_id=session_id,
                faction=contact.observer_faction,
                target_unit_id=contact.target_unit_id,
                fidelity=contact.fidelity,
                last_seen_tick=contact.tick,
                last_seen_lat=contact.lat,
                last_seen_lng=contact.lng,
                error_radius_m=contact.error_radius_m,
            )
        )
        return
    existing.last_seen_tick = contact.tick
    existing.last_seen_lat = contact.lat
    existing.last_seen_lng = contact.lng
    if _FIDELITY_RANK[contact.fidelity] > _FIDELITY_RANK[existing.fidelity]:
        existing.fidelity = contact.fidelity
        existing.error_radius_m = contact.error_radius_m


def record_all(db: Session, session_id: str, contacts: Iterable[Contact]) -> None:
    for contact in contacts:
        record(db, session_id, contact)


def query(db: Session, session_id: str, faction: str) -> list[IntelContact]:
    """**faction-scoped**：只回該 faction 的 contacts（後端強制 fog of war）。"""
    return list(
        db.scalars(
            select(IntelContact)
            .where(
                IntelContact.session_id == session_id,
                IntelContact.faction == faction,
            )
            .order_by(IntelContact.target_unit_id)
        )
    )
