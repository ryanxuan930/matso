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
from app.factions import WHITE_CELL
from app.lobby.schemas import CreateSessionRequest, EditSessionRequest, SessionSummary
from app.models import SessionParticipant, UserRole, WargameSession

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
        omni = user.role in _OMNISCIENT_ROLES
        return [
            self._summary(
                s,
                my_factions.get(s.id),
                orbat_edit=omni or (my_factions.get(s.id) in set(s.orbat_edit_factions or [])),
            )
            for s in sessions
        ]

    def create_session(self, user: CurrentUser, req: CreateSessionRequest) -> SessionSummary:
        """建立 session 列，建立者成為 EXERCISE_DIRECTOR 參與者（WHITE_CELL faction）。

        帶 `scenario_id` → 由已存想定開局（建 session + orbat 單位 + relations，#7）；否則建空局。
        """
        if req.scenario_id:
            return self._create_from_scenario(user, req)
        session = WargameSession(
            name=req.name,
            scenario_id=req.scenario_id,
            master_seed=0,  # 佔位；flush 取得 session.id 後以其導出（見下）
            mode=req.mode,
            current_weather={},
        )
        self._db.add(session)
        self._db.flush()  # 取得 session.id
        # master_seed 摻入 session.id（uuid）避免「同名同人」建局的 RNG 流碰撞（CODE_REVIEW C15）。
        session.master_seed = _derive_seed(req.name, user.id, session.id)
        participant = SessionParticipant(
            user_id=user.id,
            session_id=session.id,
            faction=WHITE_CELL,
            role=UserRole.EXERCISE_DIRECTOR,
            unit_scope=[],
        )
        self._db.add(participant)
        self._db.commit()
        return self._summary(session, participant.faction, orbat_edit=True)  # 建立者為統裁（全知）

    def _create_from_scenario(self, user: CurrentUser, req: CreateSessionRequest) -> SessionSummary:
        """由已存想定開局（#7）：載回 bundle → 建 session + 單位 → 建立者為統裁參與者。"""
        import json

        from app.errors import ScenarioInvalidError, ScenarioNotFoundError
        from app.models import Scenario
        from app.scenario import ScenarioError, create_session_from_scenario, load_scenario_bundle

        row = self._db.get(Scenario, req.scenario_id)
        if row is None:
            raise ScenarioNotFoundError(f"想定不存在：{req.scenario_id}")
        try:
            loaded = load_scenario_bundle(json.loads(bytes(row.package_blob)))
        except ScenarioError as exc:
            raise ScenarioInvalidError(str(exc)) from exc
        seed = _derive_seed(loaded.name, user.id, str(req.scenario_id))
        sid = create_session_from_scenario(
            self._db,
            loaded,
            master_seed=seed,
            scenario_id=req.scenario_id,
            seed_default_equipment=True,  # 配發預設武器，供資料驅動的 ENGAGE 武器/彈種選擇
        )
        self._db.add(
            SessionParticipant(
                user_id=user.id,
                session_id=sid,
                faction=WHITE_CELL,
                role=UserRole.EXERCISE_DIRECTOR,
                unit_scope=[],
            )
        )
        self._db.commit()
        session = self._db.get(WargameSession, sid)
        assert session is not None
        return self._summary(session, WHITE_CELL, orbat_edit=True)  # 建立者為統裁（全知）

    def _participant_factions(self, user_id: str) -> dict[str, str]:
        rows = (
            self._db.execute(
                select(SessionParticipant).where(SessionParticipant.user_id == user_id)
            )
            .scalars()
            .all()
        )
        return {p.session_id: p.faction for p in rows}

    @staticmethod
    def _summary(
        session: WargameSession, my_faction: str | None, orbat_edit: bool = False
    ) -> SessionSummary:
        return SessionSummary(
            id=session.id,
            name=session.name,
            scenario_id=session.scenario_id,
            mode=session.mode.value,
            status=(
                "ARCHIVED"
                if session.archived_at is not None
                else "ENDED"
                if session.end_time is not None
                else "ACTIVE"
            ),
            my_faction=my_faction,
            orbat_edit=orbat_edit,
            archived_at=(
                session.archived_at.isoformat()
                if session.archived_at is not None and hasattr(session.archived_at, "isoformat")
                else (str(session.archived_at) if session.archived_at else None)
            ),
            start_time=(
                session.start_time.isoformat()
                if hasattr(session.start_time, "isoformat")
                else (str(session.start_time) if session.start_time else None)
            ),
            world_start_time=(
                session.world_start_time.isoformat()
                if session.world_start_time is not None
                and hasattr(session.world_start_time, "isoformat")
                else (str(session.world_start_time) if session.world_start_time else None)
            ),
        )

    def edit_session(
        self, user: CurrentUser, session_id: str, req: EditSessionRequest
    ) -> SessionSummary:
        """編輯已開推演設定（#16）——名稱 / 想定世界初始日期時間。限統裁/管理（全知）。"""
        from datetime import datetime

        from app.errors import AuthForbiddenError, SessionNotFoundError

        # 全知（統裁/白軍/管理）恆可編；否則須為本 session 的統裁/白軍參與者（含建立者）。
        if user.role not in _OMNISCIENT_ROLES:
            part = self._db.execute(
                select(SessionParticipant).where(
                    SessionParticipant.user_id == user.id,
                    SessionParticipant.session_id == session_id,
                )
            ).scalar_one_or_none()
            if part is None or part.role not in (
                UserRole.EXERCISE_DIRECTOR,
                UserRole.WHITE_CELL_STAFF,
            ):
                raise AuthForbiddenError("僅統裁/管理可編輯推演設定")
        session = self._db.get(WargameSession, session_id)
        if session is None:
            raise SessionNotFoundError(f"session 不存在：{session_id}")
        if req.name is not None:
            session.name = req.name
        if req.world_start_time is not None:
            wst = req.world_start_time.strip()
            if wst == "":
                session.world_start_time = None
            else:
                try:
                    session.world_start_time = datetime.fromisoformat(wst)  # type: ignore[assignment]
                except ValueError as exc:
                    raise AuthForbiddenError(f"世界初始時間格式錯誤：{wst}") from exc
        self._db.commit()
        my_faction = self._participant_factions(user.id).get(session_id)
        return self._summary(session, my_faction, orbat_edit=True)

    # ---------------- #31 封存 / 還原 / 刪除 ----------------

    def _require_director(self, user: CurrentUser, session_id: str) -> WargameSession:
        """限統裁/管理（全知），否則須為本 session 統裁/白軍參與者。回傳 session（不存在→404）。"""
        from app.errors import AuthForbiddenError, SessionNotFoundError

        if user.role not in _OMNISCIENT_ROLES:
            part = self._db.execute(
                select(SessionParticipant).where(
                    SessionParticipant.user_id == user.id,
                    SessionParticipant.session_id == session_id,
                )
            ).scalar_one_or_none()
            if part is None or part.role not in (
                UserRole.EXERCISE_DIRECTOR,
                UserRole.WHITE_CELL_STAFF,
            ):
                raise AuthForbiddenError("僅統裁/管理可封存或刪除推演")
        session = self._db.get(WargameSession, session_id)
        if session is None:
            raise SessionNotFoundError(f"session 不存在：{session_id}")
        return session

    def set_archived(self, user: CurrentUser, session_id: str, archived: bool) -> SessionSummary:
        """封存（archived=True）或還原（False）一局。封存＝活模擬凍結、移入歷史頁。"""
        from datetime import datetime

        session = self._require_director(user, session_id)
        # 封存時間為真實世界 metadata（非模擬邏輯，不受 SimClock 紅線約束）。
        session.archived_at = datetime.now() if archived else None  # type: ignore[assignment]
        self._db.commit()
        my_faction = self._participant_factions(user.id).get(session_id)
        return self._summary(session, my_faction, orbat_edit=True)

    def delete_session(self, user: CurrentUser, session_id: str) -> None:
        """永久刪除一局（連同單位/事件/標註，DB cascade）。限統裁/管理，前端須二次確認。"""
        session = self._require_director(user, session_id)
        self._db.delete(session)
        self._db.commit()


def _derive_seed(name: str, user_id: str, session_id: str) -> int:
    """由建局名 + 建立者 + session.id 確定性導出 master_seed（避免裸 random；P4 模擬 RNG 根）。

    摻入 session.id（uuid）確保即使同名同人建多局，master_seed 也互異（CODE_REVIEW C15）。
    以 BLAKE2b 取 63-bit 正整數，落在 DB BigInt 範圍內。
    """
    digest = hashlib.blake2b(f"{name}:{user_id}:{session_id}".encode(), digest_size=8).digest()
    return int.from_bytes(digest, "big") & 0x7FFF_FFFF_FFFF_FFFF
