"""FastAPI 進入點。healthz + auth/lobby（O4.1）+ Order pipeline（O3.1）；其餘依里程碑逐步實作。"""

import logging

from fastapi import FastAPI

from app import __version__
from app.api import (
    auth_router,
    install_error_handlers,
    intel_router,
    lobby_router,
    orders_router,
)
from app.config import Settings

app = FastAPI(title="MATSO Core Orchestrator", version=__version__)

if Settings().jwt_secret_is_default:
    logging.getLogger("app").warning(
        "JWT_SECRET 未設定，使用不安全的開發預設；正式部署 MUST 以環境變數覆寫"
    )

install_error_handlers(app)
app.include_router(auth_router)
app.include_router(lobby_router)
app.include_router(orders_router)
app.include_router(intel_router)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok", "version": __version__}
