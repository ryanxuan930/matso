"""JWT 簽發/驗證（O4.1）——claims、類型檢查、過期、竄改，皆以注入時鐘確定性測試。"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.auth.tokens import JwtCodec, TokenType
from app.errors import AuthInvalidTokenError, AuthTokenExpiredError

_SECRET = "test-secret-key-at-least-32-bytes-long!"


def _codec(now: datetime | None = None) -> JwtCodec:
    if now is None:
        return JwtCodec(secret=_SECRET)
    return JwtCodec(secret=_SECRET, now=lambda: now)


def test_issue_and_decode_roundtrip() -> None:
    codec = _codec()
    token = codec.issue("user-1", "COMMANDER", TokenType.ACCESS, ttl_s=900)
    claims = codec.decode(token, TokenType.ACCESS)
    assert claims.subject == "user-1"
    assert claims.role == "COMMANDER"
    assert claims.token_type is TokenType.ACCESS


def test_refresh_token_rejected_as_access() -> None:
    codec = _codec()
    refresh = codec.issue("u", "STAFF", TokenType.REFRESH, ttl_s=1000)
    with pytest.raises(AuthInvalidTokenError):
        codec.decode(refresh, TokenType.ACCESS)  # 類型不符


def test_expired_token_raises_expired() -> None:
    # 以過去時鐘簽發 → exp 早於真實 now → 過期
    past = datetime(2000, 1, 1, tzinfo=UTC)
    token = _codec(past).issue("u", "STAFF", TokenType.ACCESS, ttl_s=900)
    with pytest.raises(AuthTokenExpiredError):
        _codec().decode(token, TokenType.ACCESS)


def test_tampered_signature_rejected() -> None:
    token = _codec().issue("u", "STAFF", TokenType.ACCESS, ttl_s=900)
    with pytest.raises(AuthInvalidTokenError):
        JwtCodec(secret="different-secret-key-32-bytes-long!!").decode(token, TokenType.ACCESS)


def test_garbage_token_rejected() -> None:
    with pytest.raises(AuthInvalidTokenError):
        _codec().decode("not.a.jwt", TokenType.ACCESS)


def test_token_missing_claims_rejected() -> None:
    # 以同 secret 簽出缺 sub/role 但類型正確的 token → 拒絕
    import jwt

    forged = jwt.encode({"type": "access"}, _SECRET, algorithm="HS256")
    with pytest.raises(AuthInvalidTokenError):
        _codec().decode(forged, TokenType.ACCESS)
