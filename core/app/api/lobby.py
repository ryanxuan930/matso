"""Lobby REST 端點（O4.1，SPEC §16.1）——session 列表 + 建局（皆需認證）。

GET  /api/v1/sessions   角色/參與過濾的 session 列表
POST /api/v1/sessions   建立 session（建立者成為 EXERCISE_DIRECTOR 參與者）
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response, status

from app.api.deps import get_current_user, get_lobby_service
from app.auth.schemas import CurrentUser
from app.lobby.schemas import CreateSessionRequest, EditSessionRequest, SessionSummary
from app.lobby.service import LobbyService

router = APIRouter(prefix="/api/v1/sessions", tags=["lobby"])


@router.get("", response_model=list[SessionSummary])
def list_sessions(
    user: CurrentUser = Depends(get_current_user),
    lobby: LobbyService = Depends(get_lobby_service),
) -> list[SessionSummary]:
    return lobby.list_sessions(user)


@router.post("", status_code=201, response_model=SessionSummary)
def create_session(
    req: CreateSessionRequest,
    user: CurrentUser = Depends(get_current_user),
    lobby: LobbyService = Depends(get_lobby_service),
) -> SessionSummary:
    return lobby.create_session(user, req)


@router.patch("/{session_id}", response_model=SessionSummary)
def edit_session(
    session_id: str,
    req: EditSessionRequest,
    user: CurrentUser = Depends(get_current_user),
    lobby: LobbyService = Depends(get_lobby_service),
) -> SessionSummary:
    """編輯已開推演設定（名稱 / 想定世界初始日期時間）——限統裁/管理（#16）。"""
    return lobby.edit_session(user, session_id, req)


@router.post("/{session_id}/archive", response_model=SessionSummary)
def archive_session(
    session_id: str,
    user: CurrentUser = Depends(get_current_user),
    lobby: LobbyService = Depends(get_lobby_service),
) -> SessionSummary:
    """封存推演（#31）——移入歷史頁、活模擬凍結。限統裁/管理。"""
    return lobby.set_archived(user, session_id, True)


@router.post("/{session_id}/unarchive", response_model=SessionSummary)
def unarchive_session(
    session_id: str,
    user: CurrentUser = Depends(get_current_user),
    lobby: LobbyService = Depends(get_lobby_service),
) -> SessionSummary:
    """還原封存推演（#31）——移回進行中。限統裁/管理。"""
    return lobby.set_archived(user, session_id, False)


@router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_session(
    session_id: str,
    user: CurrentUser = Depends(get_current_user),
    lobby: LobbyService = Depends(get_lobby_service),
) -> Response:
    """永久刪除推演（#31）——連同單位/事件/標註。限統裁/管理；前端須二次確認。"""
    lobby.delete_session(user, session_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
