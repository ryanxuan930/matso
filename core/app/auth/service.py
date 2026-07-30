"""認證服務（O4.1，SPEC §12）——帳密驗證 → JWT 對；refresh → 新 access。

faction-scope / 角色權限的後端強制以 User.role + SessionParticipant 為據（本卡立地基，
各端點的 faction 過濾隨其落地）。列舉防護：帳號不存在與密碼錯回同一錯誤。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.hashing import dummy_verify, hash_password, needs_rehash, verify_password
from app.auth.schemas import CurrentUser, TokenPair
from app.auth.tokens import JwtCodec, TokenClaims, TokenType
from app.config import Settings
from app.errors import AuthInvalidCredentialsError, AuthInvalidTokenError
from app.models.tables import RevokedToken, User

# WP-E2 帳號鎖定（防爆破）。5 次/15 分鐘是常見的保守值——太嚴會讓打錯字的人被鎖，
# 太鬆則擋不住自動化嘗試。
LOCKOUT_THRESHOLD = 5
LOCKOUT_MINUTES = 15


class AuthService:
    def __init__(self, db: Session, codec: JwtCodec, settings: Settings) -> None:
        self._db = db
        self._codec = codec
        self._settings = settings

    def authenticate(self, username: str, password: str) -> TokenPair:
        """驗證帳密 → 簽發 access + refresh。失敗一律 AUTH_INVALID_CREDENTIALS（防帳號列舉）。

        WP-E2：連續失敗 `LOCKOUT_THRESHOLD` 次 → 鎖 `LOCKOUT_MINUTES` 分鐘（防爆破）。
        **被鎖時回的錯誤與密碼錯誤完全一樣**——分開回會把「這個帳號存在」洩漏出去，
        那正是防帳號列舉在擋的事。
        """
        user = self._db.execute(select(User).where(User.username == username)).scalar_one_or_none()
        if user is None:
            dummy_verify()  # 消除計時側信道：帳號不存在時仍跑等價 Argon2（CODE_REVIEW C4）
            raise AuthInvalidCredentialsError("帳號或密碼錯誤")
        if self._is_locked(user):
            dummy_verify()  # 鎖定期間也跑一次，讓耗時與正常路徑一致
            raise AuthInvalidCredentialsError("帳號或密碼錯誤")
        if not verify_password(user.password_hash, password):
            self._record_failure(user)
            raise AuthInvalidCredentialsError("帳號或密碼錯誤")
        self._clear_failures(user)
        self._upgrade_hash_if_needed(user, password)
        return self._issue_pair(user)

    def logout(self, refresh_token: str) -> None:
        """撤銷該 refresh token（WP-E2）。

        **登出必須真的生效**——在此之前 refresh token 撤銷不了，登出只是前端把它丟掉，
        撿到的人照樣能一直換發新的 access。
        """
        try:
            claims = self._codec.decode(refresh_token, TokenType.REFRESH)
        except AuthInvalidTokenError:
            return  # 已經無效的 token 不必再撤銷；也不回報（避免當成 token 探測器）
        self._revoke(claims, reason="LOGOUT")
        self._db.commit()

    # ---- 鎖定 ----

    def _is_locked(self, user: User) -> bool:
        until = user.locked_until
        return until is not None and until > datetime.now(UTC).replace(tzinfo=None)

    def _record_failure(self, user: User) -> None:
        user.failed_attempts = (user.failed_attempts or 0) + 1
        if user.failed_attempts >= LOCKOUT_THRESHOLD:
            user.locked_until = datetime.now(UTC).replace(tzinfo=None) + timedelta(
                minutes=LOCKOUT_MINUTES
            )
            user.failed_attempts = 0  # 鎖了就重新計數，否則解鎖後一次失敗又立刻鎖回去
        self._db.commit()

    def _clear_failures(self, user: User) -> None:
        if user.failed_attempts or user.locked_until:
            user.failed_attempts = 0
            user.locked_until = None
            self._db.commit()

    def _upgrade_hash_if_needed(self, user: User, password: str) -> None:
        """Argon2 參數升級（WP-E2）。**只在登入成功後做**——那是唯一拿得到明文的時機。"""
        if needs_rehash(user.password_hash):
            user.password_hash = hash_password(password)
            self._db.commit()

    def refresh(self, refresh_token: str) -> TokenPair:
        """以有效 refresh token 換發新 token 對（**滑動續期**）。

        同時換發新的 access + refresh——只要使用者持續操作（每 <access TTL 觸發一次 refresh），
        refresh 視窗就一直往後延，session 不因 refresh 到期而中斷（使用者要求：除非登出/關頁，
        否則一直延長）。

        WP-E2 **輪替 + 重用偵測**：換發時撤銷舊的那張。若有人拿**已撤銷**的 refresh 來換，
        那代表這張 token 被複製過（合法持有者早就換掉它了）——此時**撤銷該使用者全部**
        的 refresh token 並拒絕。這是 rotation 真正的價值：它把「偷到 token」從
        「可以無限續期」變成「最多用一次，而且會被發現」。
        """
        claims = self._codec.decode(refresh_token, TokenType.REFRESH)
        user = self._db.get(User, claims.subject)
        if user is None:
            raise AuthInvalidTokenError("token 對應的帳號不存在")
        if claims.jti and self._is_revoked(claims.jti):
            self._revoke_all(user.id, reason="REUSE_DETECTED")
            self._db.commit()
            raise AuthInvalidTokenError("refresh token 已被撤銷（偵測到重用）")
        self._revoke(claims, reason="ROTATED")
        pair = self._issue_pair(user)
        self._db.commit()
        return pair

    # ---- 撤銷 ----

    def _is_revoked(self, jti: str) -> bool:
        return self._db.get(RevokedToken, jti) is not None

    def _revoke(self, claims: TokenClaims, *, reason: str) -> None:
        """撤銷單張。**沒有 jti 的舊 token 略過**——簽發時還沒有這個欄位，
        強制失效會在部署當下把所有人踢掉。"""
        if not claims.jti or self._is_revoked(claims.jti):
            return
        self._db.add(
            RevokedToken(
                jti=claims.jti,
                user_id=claims.subject,
                expires_at=datetime.fromtimestamp(claims.expires_at, UTC).replace(tzinfo=None)
                if claims.expires_at
                else datetime.now(UTC).replace(tzinfo=None),
                reason=reason,
            )
        )

    def _revoke_all(self, user_id: str, *, reason: str) -> None:
        """撤銷該使用者**目前已知**的全部 refresh token。

        ⚠ 只能撤銷撤銷表裡有的（＝曾被輪替過的）——還在流通、從未被換過的那些沒有紀錄。
        完整的「全家族撤銷」需要簽發時就登記每一張，那是更大的改動；此處先讓重用偵測
        至少切斷已知的鏈，並留下 `REUSE_DETECTED` 供稽核追查。
        """
        rows = self._db.execute(
            select(RevokedToken).where(RevokedToken.user_id == user_id)
        ).scalars()
        for row in rows:
            row.reason = reason

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
