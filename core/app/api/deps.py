"""API 依賴注入（O3.1/O4.1）。測試以 app.dependency_overrides 覆寫 get_db / get_gateway。"""

from __future__ import annotations

from collections.abc import Iterator
from functools import lru_cache

import grpc
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.auth.schemas import CurrentUser
from app.auth.service import AuthService
from app.auth.tokens import JwtCodec
from app.config import Settings
from app.db import default_session_factory
from app.errors import AuthInvalidTokenError
from app.lobby.service import LobbyService
from app.orders.precheck import PhysicsGateway, TerrainGatewayAdapter
from app.orders.service import OrderService
from app.plugins import TerrainClient


def get_db() -> Iterator[Session]:
    db = default_session_factory()()
    try:
        yield db
    finally:
        db.close()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def get_auth_service(
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> AuthService:
    codec = JwtCodec(secret=settings.jwt_secret, algorithm=settings.jwt_algorithm)
    return AuthService(db, codec, settings)


def get_lobby_service(db: Session = Depends(get_db)) -> LobbyService:
    return LobbyService(db)


# auto_error=False：缺 token 時不由 FastAPI 直接 403，改由我們拋領域例外 → 統一 Error 格式
_bearer = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    auth: AuthService = Depends(get_auth_service),
) -> CurrentUser:
    if credentials is None:
        raise AuthInvalidTokenError("缺少 Authorization bearer token")
    return auth.current_user(credentials.credentials)


@lru_cache(maxsize=1)
def _default_channel() -> grpc.Channel:
    return grpc.insecure_channel(get_settings().terrain_grpc_target)


class _StubGateway:
    """E2E/開發用許可式 gateway（env STUB_GATEWAY=1）：precheck 恆可行 + 假 ETA。

    僅供無 terrain 的前端下令全流程 E2E；絕非真物理引擎（同 SQLite E2E DB 的測試 affordance）。
    """

    def path_reachable(self, from_h3: str, to_h3: str, mobility_profile: str) -> tuple[bool, str]:
        return True, "stub: cost=5.0, eta=5"

    def has_los(
        self, observer: tuple[float, float, float], target: tuple[float, float, float]
    ) -> tuple[bool, float]:
        return True, 15.0


def get_gateway() -> PhysicsGateway:
    """物理 gateway。STUB_GATEWAY=1 → 許可式 stub（E2E）；否則真 terrain gRPC（未起 → 503）。"""
    if get_settings().stub_gateway:
        return _StubGateway()
    return TerrainGatewayAdapter(TerrainClient(_default_channel()))


def get_order_service(
    db: Session = Depends(get_db),
    gateway: PhysicsGateway = Depends(get_gateway),
) -> OrderService:
    return OrderService(db, gateway)
