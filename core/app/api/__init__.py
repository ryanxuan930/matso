"""Core REST API 層（FastAPI routers；契約見 contracts/core_api.yaml）。"""

from app.api.aar import router as aar_router
from app.api.auth import router as auth_router
from app.api.control import router as control_router
from app.api.equipment import router as equipment_router
from app.api.errors import install_error_handlers
from app.api.inject import router as inject_router
from app.api.intel import router as intel_router
from app.api.lobby import router as lobby_router
from app.api.map_features import router as map_features_router
from app.api.movement import router as movement_router
from app.api.orbat import router as orbat_router
from app.api.orders import router as orders_router
from app.api.scenarios import router as scenarios_router
from app.api.units import router as units_router
from app.api.ws import router as ws_router

__all__ = [
    "aar_router",
    "auth_router",
    "control_router",
    "equipment_router",
    "inject_router",
    "install_error_handlers",
    "intel_router",
    "lobby_router",
    "map_features_router",
    "movement_router",
    "orbat_router",
    "orders_router",
    "scenarios_router",
    "units_router",
    "ws_router",
]
