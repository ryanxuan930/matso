"""帳號管理服務（#32，SPEC §12）——白軍/管理建立帳號、設定角色（權限）、重設密碼、刪除。

**權限**：限管理類角色（ADMIN / EXERCISE_DIRECTOR / WHITE_CELL_STAFF）；一般角色無權。
防呆：帳號名唯一；不可刪除自己（避免自我鎖死）；不可移除系統最後一個管理帳號。
密碼一律 Argon2 雜湊（hash_password），服務層不落明碼。
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.hashing import hash_password
from app.auth.schemas import CreateUserRequest, CurrentUser, UpdateUserRequest, UserView
from app.errors import AuthForbiddenError, UserConflictError, UserNotFoundError
from app.models import User, UserRole

# 可管理帳號的角色（白軍/統裁/管理）。
_ADMIN_ROLES = frozenset({UserRole.ADMIN, UserRole.EXERCISE_DIRECTOR, UserRole.WHITE_CELL_STAFF})


class UserService:
    def __init__(self, db: Session) -> None:
        self._db = db

    def _require_admin(self, actor: CurrentUser) -> None:
        if actor.role not in _ADMIN_ROLES:
            raise AuthForbiddenError("僅白軍/統裁/管理可管理帳號")

    def list_users(self, actor: CurrentUser) -> list[UserView]:
        self._require_admin(actor)
        rows = self._db.execute(select(User).order_by(User.username)).scalars().all()
        return [self._view(u) for u in rows]

    def create_user(self, actor: CurrentUser, req: CreateUserRequest) -> UserView:
        self._require_admin(actor)
        exists = self._db.execute(
            select(User).where(User.username == req.username)
        ).scalar_one_or_none()
        if exists is not None:
            raise UserConflictError(f"帳號名已存在：{req.username}")
        user = User(
            username=req.username,
            password_hash=hash_password(req.password),
            role=req.role,
        )
        self._db.add(user)
        self._db.commit()
        return self._view(user)

    def update_user(self, actor: CurrentUser, user_id: str, req: UpdateUserRequest) -> UserView:
        self._require_admin(actor)
        user = self._db.get(User, user_id)
        if user is None:
            raise UserNotFoundError(f"帳號不存在：{user_id}")
        # 降級最後一個管理帳號 → 阻擋（避免系統無管理者）。
        if req.role is not None and user.role in _ADMIN_ROLES and req.role not in _ADMIN_ROLES:
            self._guard_last_admin(user_id)
        if req.role is not None:
            user.role = req.role
        if req.password is not None:
            user.password_hash = hash_password(req.password)
        self._db.commit()
        return self._view(user)

    def delete_user(self, actor: CurrentUser, user_id: str) -> None:
        self._require_admin(actor)
        if user_id == actor.id:
            raise UserConflictError("不可刪除自己的帳號")
        user = self._db.get(User, user_id)
        if user is None:
            raise UserNotFoundError(f"帳號不存在：{user_id}")
        if user.role in _ADMIN_ROLES:
            self._guard_last_admin(user_id)
        self._db.delete(user)
        self._db.commit()

    def _guard_last_admin(self, excluding_id: str) -> None:
        """確保移除/降級後仍至少存在一個管理帳號。"""
        remaining = self._db.execute(
            select(func.count())
            .select_from(User)
            .where(User.role.in_(_ADMIN_ROLES), User.id != excluding_id)
        ).scalar_one()
        if remaining == 0:
            raise UserConflictError("不可移除系統最後一個管理帳號")

    @staticmethod
    def _view(user: User) -> UserView:
        return UserView(
            id=user.id,
            username=user.username,
            role=user.role,
            created_at=(
                user.created_at.isoformat()
                if hasattr(user.created_at, "isoformat")
                else (str(user.created_at) if user.created_at else None)
            ),
        )
