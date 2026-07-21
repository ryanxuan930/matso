"""Session 參與者解析（O4.5）——共用於 orders / units 端點的 faction-scope 閘門。"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.schemas import CurrentUser
from app.errors import OrderPermissionError
from app.models import SessionParticipant


def require_participant(db: Session, user: CurrentUser, session_id: str) -> SessionParticipant:
    """呼叫者於此 session 的參與者身分；非參與者 → 403（fog of war 後端強制，SPEC §12）。"""
    participant = db.execute(
        select(SessionParticipant).where(
            SessionParticipant.user_id == user.id,
            SessionParticipant.session_id == session_id,
        )
    ).scalar_one_or_none()
    if participant is None:
        raise OrderPermissionError(f"非此 session 參與者：{user.username}")
    return participant
