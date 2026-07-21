"""Core REST API 層（FastAPI routers；契約見 contracts/core_api.yaml）。"""

from app.api.auth import router as auth_router
from app.api.errors import install_error_handlers
from app.api.intel import router as intel_router
from app.api.lobby import router as lobby_router
from app.api.orders import router as orders_router
from app.api.ws import router as ws_router

__all__ = [
    "auth_router",
    "install_error_handlers",
    "intel_router",
    "lobby_router",
    "orders_router",
    "ws_router",
]
