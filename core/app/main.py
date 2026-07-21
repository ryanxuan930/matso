"""FastAPI 進入點。healthz + auth/lobby（O4.1）+ Order pipeline（O3.1）；其餘依里程碑逐步實作。"""

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api import (
    auth_router,
    install_error_handlers,
    intel_router,
    lobby_router,
    orders_router,
    ws_router,
)
from app.config import Settings

_settings = Settings()
app = FastAPI(title="MATSO Core Orchestrator", version=__version__)

if _settings.jwt_secret_is_default:
    logging.getLogger("app").warning(
        "JWT_SECRET 未設定，使用不安全的開發預設；正式部署 MUST 以環境變數覆寫"
    )

# 前端 COP 跨來源存取（O4.1）；允許來源由設定注入（faction-scope 仍由後端強制，SPEC §12）。
app.add_middleware(
    CORSMiddleware,
    allow_origins=_settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

install_error_handlers(app)
app.include_router(auth_router)
app.include_router(lobby_router)
app.include_router(orders_router)
app.include_router(intel_router)
app.include_router(ws_router)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok", "version": __version__}
