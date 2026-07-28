"""帳號管理 REST 端點（#32，SPEC §12）——白軍/統裁/管理建立帳號與設定權限。

GET    /api/v1/users           列出所有帳號
POST   /api/v1/users           建立帳號（設定角色/初始密碼）
PATCH  /api/v1/users/{id}      更新角色（權限）/ 重設密碼
DELETE /api/v1/users/{id}      刪除帳號

權限一律於服務層強制（管理類角色）；防呆：帳號名唯一、不可刪自己/最後管理員。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.auth.schemas import CreateUserRequest, CurrentUser, UpdateUserRequest, UserView
from app.auth.user_service import UserService

router = APIRouter(prefix="/api/v1/users", tags=["users"])


def _service(db: Session = Depends(get_db)) -> UserService:
    return UserService(db)


@router.get("", response_model=list[UserView])
def list_users(
    user: CurrentUser = Depends(get_current_user),
    svc: UserService = Depends(_service),
) -> list[UserView]:
    return svc.list_users(user)


@router.post("", status_code=status.HTTP_201_CREATED, response_model=UserView)
def create_user(
    req: CreateUserRequest,
    user: CurrentUser = Depends(get_current_user),
    svc: UserService = Depends(_service),
) -> UserView:
    return svc.create_user(user, req)


@router.patch("/{user_id}", response_model=UserView)
def update_user(
    user_id: str,
    req: UpdateUserRequest,
    user: CurrentUser = Depends(get_current_user),
    svc: UserService = Depends(_service),
) -> UserView:
    return svc.update_user(user, user_id, req)


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(
    user_id: str,
    user: CurrentUser = Depends(get_current_user),
    svc: UserService = Depends(_service),
) -> Response:
    svc.delete_user(user, user_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
