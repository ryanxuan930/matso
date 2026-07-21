"""AuthService（O4.1）——authenticate / refresh / current_user，SQLite + 真雜湊。"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session, sessionmaker

from app.auth.hashing import hash_password
from app.auth.service import AuthService
from app.auth.tokens import JwtCodec, TokenType
from app.config import Settings
from app.errors import AuthInvalidCredentialsError, AuthInvalidTokenError
from app.models import User, UserRole

_SETTINGS = Settings(jwt_secret="test-secret-key-at-least-32-bytes-long!")


def _service(factory: sessionmaker[Session]) -> tuple[AuthService, Session]:
    db = factory()
    codec = JwtCodec(secret=_SETTINGS.jwt_secret, algorithm=_SETTINGS.jwt_algorithm)
    return AuthService(db, codec, _SETTINGS), db


def _seed_user(db: Session, username: str = "cmdr", password: str = "pw123") -> User:
    user = User(username=username, password_hash=hash_password(password), role=UserRole.COMMANDER)
    db.add(user)
    db.commit()
    return user


def test_authenticate_success_returns_pair(session_factory: sessionmaker[Session]) -> None:
    svc, db = _service(session_factory)
    user = _seed_user(db)
    pair = svc.authenticate("cmdr", "pw123")
    assert pair.token_type == "bearer"
    assert pair.expires_in == _SETTINGS.access_token_ttl_s
    # access token 解得回同一 user
    codec = JwtCodec(secret=_SETTINGS.jwt_secret)
    claims = codec.decode(pair.access_token, TokenType.ACCESS)
    assert claims.subject == user.id
    assert claims.role == "COMMANDER"


def test_authenticate_wrong_password_rejected(session_factory: sessionmaker[Session]) -> None:
    svc, db = _service(session_factory)
    _seed_user(db)
    with pytest.raises(AuthInvalidCredentialsError):
        svc.authenticate("cmdr", "wrong")


def test_authenticate_unknown_user_same_error(session_factory: sessionmaker[Session]) -> None:
    # 帳號不存在與密碼錯回同一錯誤（列舉防護）
    svc, _ = _service(session_factory)
    with pytest.raises(AuthInvalidCredentialsError):
        svc.authenticate("ghost", "whatever")


def test_refresh_issues_new_access(session_factory: sessionmaker[Session]) -> None:
    svc, db = _service(session_factory)
    _seed_user(db)
    pair = svc.authenticate("cmdr", "pw123")
    fresh = svc.refresh(pair.refresh_token)
    assert fresh.token_type == "bearer"
    JwtCodec(secret=_SETTINGS.jwt_secret).decode(fresh.access_token, TokenType.ACCESS)


def test_refresh_rejects_access_token(session_factory: sessionmaker[Session]) -> None:
    svc, db = _service(session_factory)
    _seed_user(db)
    pair = svc.authenticate("cmdr", "pw123")
    with pytest.raises(AuthInvalidTokenError):
        svc.refresh(pair.access_token)  # access 不能當 refresh 用


def test_current_user_from_access(session_factory: sessionmaker[Session]) -> None:
    svc, db = _service(session_factory)
    user = _seed_user(db)
    pair = svc.authenticate("cmdr", "pw123")
    me = svc.current_user(pair.access_token)
    assert me.id == user.id
    assert me.username == "cmdr"
    assert me.role is UserRole.COMMANDER


def test_refresh_rejected_when_user_deleted(session_factory: sessionmaker[Session]) -> None:
    # token 有效但帳號已刪除 → 拒絕（refresh 換發前檢查帳號存在）
    svc, db = _service(session_factory)
    user = _seed_user(db)
    pair = svc.authenticate("cmdr", "pw123")
    db.delete(user)
    db.commit()
    with pytest.raises(AuthInvalidTokenError):
        svc.refresh(pair.refresh_token)


def test_current_user_rejected_when_user_deleted(session_factory: sessionmaker[Session]) -> None:
    svc, db = _service(session_factory)
    user = _seed_user(db)
    pair = svc.authenticate("cmdr", "pw123")
    db.delete(user)
    db.commit()
    with pytest.raises(AuthInvalidTokenError):
        svc.current_user(pair.access_token)
