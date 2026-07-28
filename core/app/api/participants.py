"""推演參與者名冊 REST 端點——指派帳號×陣營×角色（決定誰能操控/查看哪個陣營）。

GET    /api/v1/sessions/{session_id}/participants           名冊 + 可指派陣營
PUT    /api/v1/sessions/{session_id}/participants/{user_id}  指派/更新（upsert）
DELETE /api/v1/sessions/{session_id}/participants/{user_id}  移除

權限：限統裁/白軍/管理（全域全知角色），或本局的統裁/白軍參與者。名冊即 fog-of-war 與下令
權限的來源（SessionParticipant，SPEC §12）——故指派本身必須是導演級動作。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.auth.schemas import CurrentUser
from app.errors import (
    AuthForbiddenError,
    FactionInvalidError,
    SessionNotFoundError,
    UserNotFoundError,
)
from app.factions import WHITE_CELL, validate_faction_id
from app.models import SessionParticipant, TacticalUnit, User, UserRole, WargameSession
from app.stream.faction_filter import is_omniscient

router = APIRouter(prefix="/api/v1/sessions", tags=["participants"])

# 可管理名冊 / 具導演權的參與者角色（本局內）。
_DIRECTOR_ROLES = frozenset({UserRole.EXERCISE_DIRECTOR, UserRole.WHITE_CELL_STAFF})


class SessionParticipantView(BaseModel):
    user_id: str
    username: str
    faction: str
    role: str
    unit_scope: list[str] = []  # 限指揮之單位子集（空＝整個陣營）


class RosterUnit(BaseModel):
    id: str
    designation: str
    faction: str


class ParticipantRoster(BaseModel):
    participants: list[SessionParticipantView]
    factions: list[str]  # 可指派：本局單位陣營 + WHITE_CELL
    units: list[RosterUnit]  # 供 unit_scope 選擇


class AssignParticipantRequest(BaseModel):
    faction: str
    role: UserRole  # pydantic 驗證：非法角色 → FastAPI 422
    unit_scope: list[str] = []  # 限指揮之單位子集（空＝整個陣營）


def _require_session_director(db: Session, user: CurrentUser, session_id: str) -> WargameSession:
    """限全知（統裁/白軍/管理）或本局統裁/白軍參與者；回傳 session（不存在→404）。"""
    session = db.get(WargameSession, session_id)
    if session is None:
        raise SessionNotFoundError(f"session 不存在：{session_id}")
    if is_omniscient(user.role):
        return session
    part = db.scalar(
        select(SessionParticipant).where(
            SessionParticipant.user_id == user.id,
            SessionParticipant.session_id == session_id,
        )
    )
    if part is None or part.role not in _DIRECTOR_ROLES:
        raise AuthForbiddenError("僅統裁/白軍/管理可管理參與者名冊")
    return session


def _session_factions(db: Session, session_id: str) -> list[str]:
    """本局已宣告陣營（由單位陣營導出，穩定排序）。"""
    rows = db.execute(
        select(TacticalUnit.faction).where(TacticalUnit.session_id == session_id)
    ).scalars()
    return sorted({f for f in rows if f})


def _view(db: Session, p: SessionParticipant) -> SessionParticipantView:
    u = db.get(User, p.user_id)
    scope = p.unit_scope if isinstance(p.unit_scope, list) else []
    return SessionParticipantView(
        user_id=p.user_id,
        username=(u.username if u is not None else "?"),
        faction=p.faction,
        role=p.role.value,
        unit_scope=[str(x) for x in scope],
    )


def _session_units(db: Session, session_id: str) -> list[RosterUnit]:
    units = (
        db.execute(select(TacticalUnit).where(TacticalUnit.session_id == session_id))
        .scalars()
        .all()
    )
    return [
        RosterUnit(id=u.id, designation=u.designation, faction=u.faction)
        for u in sorted(units, key=lambda u: (u.faction, u.designation))
    ]


@router.get("/{session_id}/participants", response_model=ParticipantRoster)
def list_participants(
    session_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ParticipantRoster:
    _require_session_director(db, user, session_id)
    parts = (
        db.execute(select(SessionParticipant).where(SessionParticipant.session_id == session_id))
        .scalars()
        .all()
    )
    factions = [*_session_factions(db, session_id), WHITE_CELL]
    return ParticipantRoster(
        participants=[_view(db, p) for p in parts],
        factions=factions,
        units=_session_units(db, session_id),
    )


@router.put("/{session_id}/participants/{user_id}", response_model=SessionParticipantView)
def assign_participant(
    session_id: str,
    user_id: str,
    req: AssignParticipantRequest,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SessionParticipantView:
    """指派/更新某帳號的參與陣營與角色（upsert，唯一約束 userId+sessionId）。"""
    _require_session_director(db, user, session_id)
    target = db.get(User, user_id)
    if target is None:
        raise UserNotFoundError(f"帳號不存在：{user_id}")
    # 陣營驗證：格式合法（允許 WHITE_CELL）且屬本局（單位陣營）或 WHITE_CELL。
    faction = validate_faction_id(req.faction, allow_white_cell=True)
    allowed = {WHITE_CELL, *_session_factions(db, session_id)}
    if faction not in allowed:
        raise FactionInvalidError(f"陣營不屬於本局：{faction}（可指派：{sorted(allowed)}）")
    # unit_scope 驗證：每個 id 須為本局該陣營單位（限縮只在自己陣營內有意義；空＝整個陣營）。
    scope = list(dict.fromkeys(req.unit_scope))  # 去重、保序
    if scope:
        own_ids = {
            u.id
            for u in db.execute(
                select(TacticalUnit).where(
                    TacticalUnit.session_id == session_id, TacticalUnit.faction == faction
                )
            ).scalars()
        }
        bad = [uid for uid in scope if uid not in own_ids]
        if bad:
            raise FactionInvalidError(f"unit_scope 含非本陣營/本局單位：{bad}")
    existing = db.scalar(
        select(SessionParticipant).where(
            SessionParticipant.user_id == user_id,
            SessionParticipant.session_id == session_id,
        )
    )
    if existing is not None:
        existing.faction = faction
        existing.role = req.role
        existing.unit_scope = scope
        p = existing
    else:
        p = SessionParticipant(
            user_id=user_id,
            session_id=session_id,
            faction=faction,
            role=req.role,
            unit_scope=scope,
        )
        db.add(p)
    db.commit()
    db.refresh(p)
    return _view(db, p)


@router.delete("/{session_id}/participants/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_participant(
    session_id: str,
    user_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    """移除某帳號的參與資格。不可移除最後一位統裁（避免整局無人可管理）。"""
    _require_session_director(db, user, session_id)
    part = db.scalar(
        select(SessionParticipant).where(
            SessionParticipant.user_id == user_id,
            SessionParticipant.session_id == session_id,
        )
    )
    if part is None:
        raise UserNotFoundError(f"該帳號非本局參與者：{user_id}")
    if part.role is UserRole.EXERCISE_DIRECTOR:
        directors = (
            db.execute(
                select(SessionParticipant).where(
                    SessionParticipant.session_id == session_id,
                    SessionParticipant.role == UserRole.EXERCISE_DIRECTOR,
                )
            )
            .scalars()
            .all()
        )
        if len(directors) <= 1:
            raise AuthForbiddenError("不可移除最後一位統裁（本局將無人可管理）")
    db.delete(part)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
