"""認證服務（O4.1，SPEC §12）——帳密驗證 → JWT 對；refresh → 新 access。

faction-scope / 角色權限的後端強制以 User.role + SessionParticipant 為據（本卡立地基，
各端點的 faction 過濾隨其落地）。列舉防護：帳號不存在與密碼錯回同一錯誤。
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.hashing import dummy_verify, verify_password
from app.auth.schemas import AccessToken, CurrentUser, TokenPair
from app.auth.tokens import JwtCodec, TokenType
from app.config import Settings
from app.errors import AuthInvalidCredentialsError, AuthInvalidTokenError
from app.models import User


class AuthService:
    def __init__(self, db: Session, codec: JwtCodec, settings: Settings) -> None:
        self._db = db
        self._codec = codec
        self._settings = settings

    def authenticate(self, username: str, password: str) -> TokenPair:
        """驗證帳密 → 簽發 access + refresh。失敗一律 AUTH_INVALID_CREDENTIALS（防帳號列舉）。"""
        user = self._db.execute(select(User).where(User.username == username)).scalar_one_or_none()
        if user is None:
            dummy_verify()  # 消除計時側信道：帳號不存在時仍跑等價 Argon2（CODE_REVIEW C4）
            raise AuthInvalidCredentialsError("帳號或密碼錯誤")
        if not verify_password(user.password_hash, password):
            raise AuthInvalidCredentialsError("帳號或密碼錯誤")
        return self._issue_pair(user)

    def refresh(self, refresh_token: str) -> AccessToken:
        """以有效 refresh token 換發新 access token。"""
        claims = self._codec.decode(refresh_token, TokenType.REFRESH)
        # refresh 有效但帳號可能已刪除
        user = self._db.get(User, claims.subject)
        if user is None:
            raise AuthInvalidTokenError("token 對應的帳號不存在")
        access = self._codec.issue(
            user.id, user.role.value, TokenType.ACCESS, self._settings.access_token_ttl_s
        )
        return AccessToken(access_token=access, expires_in=self._settings.access_token_ttl_s)

    def current_user(self, access_token: str) -> CurrentUser:
        """驗證 access token → 目前使用者（供 get_current_user 依賴）。"""
        claims = self._codec.decode(access_token, TokenType.ACCESS)
        user = self._db.get(User, claims.subject)
        if user is None:
            raise AuthInvalidTokenError("token 對應的帳號不存在")
        return CurrentUser(id=user.id, username=user.username, role=user.role)

    def _issue_pair(self, user: User) -> TokenPair:
        access = self._codec.issue(
            user.id, user.role.value, TokenType.ACCESS, self._settings.access_token_ttl_s
        )
        refresh = self._codec.issue(
            user.id, user.role.value, TokenType.REFRESH, self._settings.refresh_token_ttl_s
        )
        return TokenPair(
            access_token=access,
            refresh_token=refresh,
            expires_in=self._settings.access_token_ttl_s,
        )
