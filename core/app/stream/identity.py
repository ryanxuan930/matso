"""WS 連線身分解析（O4.3）——token 的 user → 該 session 的 faction + 角色。

一般角色：必須是該 session 的 SessionParticipant（取其 faction）。全知角色（統裁/白軍/管理）
即使非參與者亦可連（faction=WHITE_CELL、全知視角）。皆非 → 拒絕連線（fog of war 後端強制）。
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.factions import WHITE_CELL
from app.models import SessionParticipant, UserRole
from app.stream.faction_filter import is_omniscient


@dataclass(frozen=True, slots=True)
class WsIdentity:
    faction: str
    role: UserRole
    omniscient: bool


def resolve_ws_identity(
    db: Session, user_id: str, user_role: UserRole, session_id: str
) -> WsIdentity | None:
    """解析連線身分；無權（非參與者且非全知）→ None。"""
    participant = db.execute(
        select(SessionParticipant).where(
            SessionParticipant.user_id == user_id,
            SessionParticipant.session_id == session_id,
        )
    ).scalar_one_or_none()
    if participant is not None:
        return WsIdentity(
            faction=participant.faction,
            role=participant.role,
            omniscient=is_omniscient(participant.role),
        )
    if is_omniscient(user_role):
        return WsIdentity(faction=WHITE_CELL, role=user_role, omniscient=True)
    return None
