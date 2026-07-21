"""FastAPI 進入點。healthz + auth/lobby（O4.1）+ Order pipeline（O3.1）；其餘依里程碑逐步實作。"""

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api import (
    aar_router,
    auth_router,
    control_router,
    inject_router,
    install_error_handlers,
    intel_router,
    lobby_router,
    orders_router,
    units_router,
    ws_router,
)
from app.config import Settings

_settings = Settings()
app = FastAPI(title="MATSO Core Orchestrator", version=__version__)

# 正式部署（MATSO_ENV=production）對不安全設定 fail-fast（CODE_REVIEW C13）。
_settings.ensure_production_safe()

if _settings.jwt_secret_is_default:
    logging.getLogger("app").warning(
        "JWT_SECRET 未設定，使用不安全的開發預設；正式部署 MUST 以環境變數覆寫"
    )

# 前端 COP 跨來源存取（O4.1）；允許來源由設定注入（faction-scope 仍由後端強制，SPEC §12）。
# CORS 萬用字元與 credentials 不相容——遇 `*` 時關掉 credentials 而非讓瀏覽器整組拒絕（C14）。
_allow_credentials = not _settings.cors_allows_wildcard
if _settings.cors_allows_wildcard:
    logging.getLogger("app").warning("CORS_ORIGINS 含 '*'：已停用 allow_credentials（勿用於生產）")
app.add_middleware(
    CORSMiddleware,
    allow_origins=_settings.cors_origin_list,
    allow_credentials=_allow_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
)

install_error_handlers(app)
app.include_router(auth_router)
app.include_router(lobby_router)
app.include_router(orders_router)
app.include_router(units_router)
app.include_router(intel_router)
app.include_router(inject_router)
app.include_router(control_router)
app.include_router(aar_router)
app.include_router(ws_router)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok", "version": __version__}
