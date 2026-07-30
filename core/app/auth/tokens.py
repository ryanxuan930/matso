"""JWT 簽發與驗證（O4.1，SPEC §12）——短效 access + 長效 refresh。

認證屬執行期基礎設施（非模擬引擎）：token 的到期以**真實牆鐘**計算，與 app.runtime /
terrain_client 用 time.monotonic 同理，不違反「模擬邏輯禁用 datetime.now」紅線。簽發時間以
注入的 `now` 供測試決定性（預設真實 UTC 時鐘）。

claims：sub（user id）、role、type（access|refresh）、iat、exp。refresh 不可當 access 用
（type 檢查），反之亦然。
"""

from __future__ import annotations

import enum
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt

from app.errors import AuthInvalidTokenError, AuthTokenExpiredError


class TokenType(enum.StrEnum):
    ACCESS = "access"
    REFRESH = "refresh"


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class TokenClaims:
    subject: str  # user id
    role: str
    token_type: TokenType
    # WP-E2：撤銷的最小單位。舊 token（簽發時還沒有這個欄位）解出來是 ""，
    # **那些一律當作不可撤銷但仍有效**——強制失效會在部署當下把所有人踢掉。
    jti: str = ""
    expires_at: int = 0


@dataclass(frozen=True, slots=True)
class JwtCodec:
    """以對稱金鑰簽發/驗證 JWT。secret 由呼叫端注入（來自 Settings，env 提供）。"""

    secret: str
    algorithm: str = "HS256"
    now: Callable[[], datetime] = _utcnow

    def issue(
        self, subject: str, role: str, token_type: TokenType, ttl_s: int, jti: str | None = None
    ) -> str:
        issued = self.now()
        payload: dict[str, Any] = {
            "sub": subject,
            "role": role,
            "type": token_type.value,
            "iat": int(issued.timestamp()),
            "exp": int((issued + timedelta(seconds=ttl_s)).timestamp()),
            # WP-E2：每張 token 一個 id，撤銷表以此為鍵。
            "jti": jti or uuid.uuid4().hex,
        }
        return jwt.encode(payload, self.secret, algorithm=self.algorithm)

    def decode(self, token: str, expected_type: TokenType) -> TokenClaims:
        """驗證簽章 + 到期 + 類型。過期 → AuthTokenExpired；其餘無效 → AuthInvalidToken。"""
        try:
            payload = jwt.decode(token, self.secret, algorithms=[self.algorithm])
        except jwt.ExpiredSignatureError as exc:
            raise AuthTokenExpiredError("token 已過期") from exc
        except jwt.InvalidTokenError as exc:
            raise AuthInvalidTokenError("token 無效") from exc
        if payload.get("type") != expected_type.value:
            raise AuthInvalidTokenError(
                f"token 類型不符（需 {expected_type.value}，得 {payload.get('type')}）"
            )
        subject = payload.get("sub")
        role = payload.get("role")
        if not isinstance(subject, str) or not isinstance(role, str):
            raise AuthInvalidTokenError("token 缺少 sub/role")
        raw_exp = payload.get("exp")
        return TokenClaims(
            subject=subject,
            role=role,
            token_type=expected_type,
            jti=str(payload.get("jti") or ""),
            expires_at=int(raw_exp) if isinstance(raw_exp, (int, float)) else 0,
        )
