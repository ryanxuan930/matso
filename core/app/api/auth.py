"""Auth REST 端點（O4.1，SPEC §16.1）。

POST /api/v1/auth/login    帳密 → JWT 對（access + refresh）
POST /api/v1/auth/refresh  refresh token → 新 access token
POST /api/v1/auth/logout   無狀態 JWT，用戶端丟棄即登出（此端點供對稱/審計）
GET  /api/v1/auth/me       目前使用者（bearer）
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response

from app.api.deps import get_auth_service, get_current_user
from app.auth.schemas import (
    CurrentUser,
    LoginRequest,
    RefreshRequest,
    TokenPair,
)
from app.auth.service import AuthService

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@router.post("/login", response_model=TokenPair)
def login(req: LoginRequest, auth: AuthService = Depends(get_auth_service)) -> TokenPair:
    return auth.authenticate(req.username, req.password)


@router.post("/refresh", response_model=TokenPair)
def refresh(req: RefreshRequest, auth: AuthService = Depends(get_auth_service)) -> TokenPair:
    # 滑動續期：回傳新的 access + refresh 對（見 AuthService.refresh）。
    return auth.refresh(req.refresh_token)


@router.post("/logout", status_code=204)
def logout(_: CurrentUser = Depends(get_current_user)) -> Response:
    # 無狀態 JWT：伺服器不維護黑名單（Phase 1）；登出即用戶端丟棄 token。
    return Response(status_code=204)


@router.get("/me", response_model=CurrentUser)
def me(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    return user
