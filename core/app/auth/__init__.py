"""認證模組（O4.1，SPEC §12）——Argon2id 密碼 + JWT access/refresh。"""

from app.auth.hashing import hash_password, needs_rehash, verify_password
from app.auth.schemas import (
    AccessToken,
    CurrentUser,
    LoginRequest,
    RefreshRequest,
    TokenPair,
)
from app.auth.service import AuthService
from app.auth.tokens import JwtCodec, TokenClaims, TokenType

__all__ = [
    "AccessToken",
    "AuthService",
    "CurrentUser",
    "JwtCodec",
    "LoginRequest",
    "RefreshRequest",
    "TokenClaims",
    "TokenPair",
    "TokenType",
    "hash_password",
    "needs_rehash",
    "verify_password",
]
