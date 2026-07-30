"""認證強化（WP-E2）：輪替 + 重用偵測、帳號鎖定、雜湊升級。"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session, sessionmaker

from app.auth.service import LOCKOUT_MINUTES, LOCKOUT_THRESHOLD, AuthService
from app.auth.tokens import JwtCodec, TokenType
from app.config import Settings
from app.errors import AuthInvalidCredentialsError, AuthInvalidTokenError
from app.models.tables import RevokedToken, User


def _svc(db: Session) -> AuthService:
    settings = Settings()
    return AuthService(db, JwtCodec(secret=settings.jwt_secret), settings)


def _seed(db: Session, password: str = "correct-horse") -> User:
    from app.auth.hashing import hash_password
    from app.models.enums import UserRole

    user = User(username="alice", password_hash=hash_password(password), role=UserRole.COMMANDER)
    db.add(user)
    db.commit()
    return user


# ---- 輪替與重用偵測 ----


def test_refreshing_revokes_the_old_token(session_factory: sessionmaker[Session]) -> None:
    db = session_factory()
    _seed(db)
    svc = _svc(db)
    first = svc.authenticate("alice", "correct-horse")
    svc.refresh(first.refresh_token)
    assert db.query(RevokedToken).count() == 1
    db.close()


def test_reusing_a_rotated_token_is_rejected(session_factory: sessionmaker[Session]) -> None:
    """**這是 rotation 真正的價值**：它把「偷到 token」從「可以無限續期」
    變成「最多用一次，而且會被發現」。"""
    db = session_factory()
    _seed(db)
    svc = _svc(db)
    first = svc.authenticate("alice", "correct-horse")
    svc.refresh(first.refresh_token)  # 合法持有者換掉它
    with pytest.raises(AuthInvalidTokenError, match="重用"):
        svc.refresh(first.refresh_token)  # 竊取者拿舊的來換
    db.close()


def test_reuse_detection_marks_the_family(session_factory: sessionmaker[Session]) -> None:
    db = session_factory()
    _seed(db)
    svc = _svc(db)
    first = svc.authenticate("alice", "correct-horse")
    svc.refresh(first.refresh_token)
    with pytest.raises(AuthInvalidTokenError):
        svc.refresh(first.refresh_token)
    assert {r.reason for r in db.query(RevokedToken).all()} == {"REUSE_DETECTED"}
    db.close()


def test_logout_actually_revokes(session_factory: sessionmaker[Session]) -> None:
    """**在此之前這個端點是 no-op**——登出只是前端把 token 丟掉，
    撿到的人照樣能一直換發新的 access。"""
    db = session_factory()
    _seed(db)
    svc = _svc(db)
    pair = svc.authenticate("alice", "correct-horse")
    svc.logout(pair.refresh_token)
    with pytest.raises(AuthInvalidTokenError):
        svc.refresh(pair.refresh_token)
    db.close()


def test_logging_out_an_already_invalid_token_is_silent(session_factory) -> None:  # type: ignore[no-untyped-def]
    """不回報——否則 logout 會變成一個 token 探測器。"""
    db = session_factory()
    _seed(db)
    _svc(db).logout("not-a-token")  # 不拋
    db.close()


def test_a_token_without_a_jti_is_left_alone(session_factory: sessionmaker[Session]) -> None:
    """簽發時還沒有 jti 的舊 token **仍然有效**——強制失效會在部署當下把所有人踢掉。"""
    db = session_factory()
    user = _seed(db)
    settings = Settings()
    codec = JwtCodec(secret=settings.jwt_secret)
    # 手工簽一張沒有 jti 的（模擬部署前簽發的 token）。
    from datetime import UTC, datetime, timedelta

    import jwt as pyjwt

    legacy = pyjwt.encode(
        {
            "sub": user.id,
            "role": user.role.value,
            "type": "refresh",
            "iat": int(datetime.now(UTC).timestamp()),
            "exp": int((datetime.now(UTC) + timedelta(hours=1)).timestamp()),
        },
        settings.jwt_secret,
        algorithm="HS256",
    )
    assert codec.decode(legacy, TokenType.REFRESH).jti == ""
    svc = AuthService(db, codec, settings)
    assert svc.refresh(legacy).access_token  # 不拋

    # ⚠ 這兩條才是重點（突變測試抓出來的）：只驗「第一次不拋」殺不掉
    # 「把空 jti 也寫進撤銷表」的突變——那會在**第二次**才把所有舊 token 一起擋掉。
    assert db.query(RevokedToken).filter(RevokedToken.jti == "").count() == 0
    assert svc.refresh(legacy).access_token
    db.close()


# ---- 帳號鎖定 ----


def test_repeated_failures_lock_the_account(session_factory: sessionmaker[Session]) -> None:
    db = session_factory()
    _seed(db)
    svc = _svc(db)
    for _ in range(LOCKOUT_THRESHOLD):
        with pytest.raises(AuthInvalidCredentialsError):
            svc.authenticate("alice", "wrong")
    # 鎖定後**連正確密碼也進不去**。
    with pytest.raises(AuthInvalidCredentialsError):
        svc.authenticate("alice", "correct-horse")
    db.close()


def test_a_locked_account_reports_the_same_error_as_a_bad_password(session_factory) -> None:  # type: ignore[no-untyped-def]
    """**分開回會把「這個帳號存在」洩漏出去**，那正是防帳號列舉在擋的事。"""
    db = session_factory()
    _seed(db)
    svc = _svc(db)
    for _ in range(LOCKOUT_THRESHOLD):
        with pytest.raises(AuthInvalidCredentialsError) as bad:
            svc.authenticate("alice", "wrong")
    with pytest.raises(AuthInvalidCredentialsError) as locked:
        svc.authenticate("alice", "correct-horse")
    assert str(locked.value) == str(bad.value)
    db.close()


def test_a_successful_login_clears_the_failure_count(session_factory) -> None:  # type: ignore[no-untyped-def]
    db = session_factory()
    user = _seed(db)
    svc = _svc(db)
    for _ in range(LOCKOUT_THRESHOLD - 1):
        with pytest.raises(AuthInvalidCredentialsError):
            svc.authenticate("alice", "wrong")
    svc.authenticate("alice", "correct-horse")
    db.refresh(user)
    assert not user.failed_attempts and user.locked_until is None
    db.close()


def test_locking_resets_the_counter(session_factory: sessionmaker[Session]) -> None:
    """鎖了就重新計數——否則解鎖後**一次**失敗又立刻鎖回去。"""
    db = session_factory()
    user = _seed(db)
    svc = _svc(db)
    for _ in range(LOCKOUT_THRESHOLD):
        with pytest.raises(AuthInvalidCredentialsError):
            svc.authenticate("alice", "wrong")
    db.refresh(user)
    assert user.failed_attempts == 0 and user.locked_until is not None
    assert LOCKOUT_MINUTES > 0
    db.close()


def test_an_existing_user_row_with_null_counters_behaves_normally(session_factory) -> None:  # type: ignore[no-untyped-def]
    """既有列的 `failedAttempts`/`lockedUntil` 是 NULL ＝從未失敗過。"""
    db = session_factory()
    user = _seed(db)
    user.failed_attempts = None
    user.locked_until = None
    db.commit()
    assert _svc(db).authenticate("alice", "correct-horse").access_token
    db.close()


# ---- 雜湊升級 ----


def test_a_stale_hash_is_upgraded_on_successful_login(session_factory) -> None:  # type: ignore[no-untyped-def]
    """`needs_rehash` 一直存在但**沒有任何呼叫端**——參數升級後既有密碼永遠停在舊參數。

    只在登入成功後做：那是唯一拿得到明文的時機。
    """
    db = session_factory()
    user = _seed(db)
    before = user.password_hash
    svc = _svc(db)

    import app.auth.service as mod

    original = mod.needs_rehash
    mod.needs_rehash = lambda _h: True  # type: ignore[assignment]
    try:
        svc.authenticate("alice", "correct-horse")
    finally:
        mod.needs_rehash = original  # type: ignore[assignment]
    db.refresh(user)
    assert user.password_hash != before
    # 升級後仍然登得進去（不是把雜湊寫壞了）。
    assert _svc(db).authenticate("alice", "correct-horse").access_token
    db.close()
