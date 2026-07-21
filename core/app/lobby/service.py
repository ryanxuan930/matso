"""Lobby 服務（O4.1，SPEC §13.1）——session 列表（角色/參與過濾）+ 建局。

**faction-scope 後端強制（SPEC §12）**：一般角色只看得到自己參與的 session；統裁/管理角色
（EXERCISE_DIRECTOR / WHITE_CELL_STAFF / ADMIN）看得到全部。前端過濾不可信。

範圍（O4.1）：list + create。加入（join）與完整 session 生命週期（scenario 載入、kernel 生成）
屬後續卡（O7/O8）；本卡的 create 只建 WargameSession 列並讓建立者成為 EXERCISE_DIRECTOR 參與者。
"""

from __future__ import annotations

import hashlib

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.schemas import CurrentUser
from app.lobby.schemas import CreateSessionRequest, SessionSummary
from app.models import Faction, SessionParticipant, UserRole, WargameSession

# 看得到全部 session 的統裁/管理角色（其餘只看自己參與的）
_OMNISCIENT_ROLES = frozenset(
    {UserRole.EXERCISE_DIRECTOR, UserRole.WHITE_CELL_STAFF, UserRole.ADMIN}
)


class LobbyService:
    def __init__(self, db: Session) -> None:
        self._db = db

    def list_sessions(self, user: CurrentUser) -> list[SessionSummary]:
        """依角色過濾的 session 列表。統裁/管理見全部，其餘僅見自己參與的。"""
        my_factions = self._participant_factions(user.id)
        if user.role in _OMNISCIENT_ROLES:
            sessions = self._db.execute(select(WargameSession)).scalars().all()
        else:
            session_ids = list(my_factions.keys())
            if not session_ids:
                return []
            sessions = (
                self._db.execute(select(WargameSession).where(WargameSession.id.in_(session_ids)))
                .scalars()
                .all()
            )
        return [self._summary(s, my_factions.get(s.id)) for s in sessions]

    def create_session(self, user: CurrentUser, req: CreateSessionRequest) -> SessionSummary:
        """建立 session 列，建立者成為 EXERCISE_DIRECTOR 參與者（WHITE_CELL faction）。"""
        session = WargameSession(
            name=req.name,
            scenario_id=req.scenario_id,
            master_seed=_derive_seed(req.name, user.id),
            mode=req.mode,
            current_weather={},
        )
        self._db.add(session)
        self._db.flush()  # 取得 session.id
        participant = SessionParticipant(
            user_id=user.id,
            session_id=session.id,
            faction=Faction.WHITE_CELL,
            role=UserRole.EXERCISE_DIRECTOR,
            unit_scope=[],
        )
        self._db.add(participant)
        self._db.commit()
        return self._summary(session, participant.faction)

    def _participant_factions(self, user_id: str) -> dict[str, Faction]:
        rows = (
            self._db.execute(
                select(SessionParticipant).where(SessionParticipant.user_id == user_id)
            )
            .scalars()
            .all()
        )
        return {p.session_id: p.faction for p in rows}

    @staticmethod
    def _summary(session: WargameSession, my_faction: Faction | None) -> SessionSummary:
        return SessionSummary(
            id=session.id,
            name=session.name,
            scenario_id=session.scenario_id,
            mode=session.mode.value,
            status="ENDED" if session.end_time is not None else "ACTIVE",
            my_faction=my_faction.value if my_faction is not None else None,
        )


def _derive_seed(name: str, user_id: str) -> int:
    """由建局名 + 建立者確定性導出 master_seed（避免裸 random；P4 種子為模擬 RNG 根）。

    以 BLAKE2b 取 63-bit 正整數，落在 DB BigInt 範圍內。
    """
    digest = hashlib.blake2b(f"{name}:{user_id}".encode(), digest_size=8).digest()
    return int.from_bytes(digest, "big") & 0x7FFF_FFFF_FFFF_FFFF
