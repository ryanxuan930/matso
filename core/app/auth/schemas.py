"""Auth REST 載荷（O4.1）——對應 contracts/core_api.yaml 的 auth schema。"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.models.enums import UserRole


class LoginRequest(BaseModel):
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=1)


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int  # access token 剩餘秒數


class AccessToken(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class CurrentUser(BaseModel):
    id: str
    username: str
    role: UserRole


# ---------------- 帳號管理（#32；白軍/管理設定帳號與權限） ----------------


class UserView(BaseModel):
    id: str
    username: str
    role: UserRole
    created_at: str | None = None


class CreateUserRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=8, max_length=128)
    role: UserRole = UserRole.OBSERVER


class UpdateUserRequest(BaseModel):
    """更新帳號：角色（權限）或重設密碼；欄位 None＝不動。"""

    role: UserRole | None = None
    password: str | None = Field(default=None, min_length=8, max_length=128)
