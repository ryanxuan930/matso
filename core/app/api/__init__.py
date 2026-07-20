"""Core REST API 層（FastAPI routers；契約見 contracts/core_api.yaml）。"""

from app.api.errors import install_error_handlers
from app.api.orders import router as orders_router

__all__ = ["install_error_handlers", "orders_router"]
