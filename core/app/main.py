"""FastAPI 進入點。healthz + auth/lobby（O4.1）+ Order pipeline（O3.1）+ 活模擬（O10.1）。"""

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

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
    orbat_router,
    orders_router,
    scenarios_router,
    units_router,
    ws_router,
)
from app.config import Settings
from app.sim_runtime import SimManager

_settings = Settings()

# 正式部署（MATSO_ENV=production）對不安全設定 fail-fast（CODE_REVIEW C13）。
_settings.ensure_production_safe()


@asynccontextmanager
async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """活模擬執行期（O10.1）：讓 MOVE 指令執行、單位移動、STATE_DIFF 廣播。

    E2E/測試（STUB_GATEWAY=1）不啟動——避免與「送出後取消 VALIDATED 指令」等流程相衝，
    且測試不需真 Redis。正式/開發（無 stub）啟動 SimManager 掃描迴圈。
    """
    manager: SimManager | None = None
    task: asyncio.Task[None] | None = None
    if not _settings.stub_gateway:
        manager = SimManager(redis_url=_settings.redis_url)
        task = asyncio.create_task(manager.run())
        logging.getLogger("app").info("Sim runtime 已啟動（活模擬 O10.1）")
    try:
        yield
    finally:
        if manager is not None:
            await manager.stop()
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task


app = FastAPI(title="MATSO Core Orchestrator", version=__version__, lifespan=_lifespan)

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
app.include_router(orbat_router)
app.include_router(orders_router)
app.include_router(scenarios_router)
app.include_router(units_router)
app.include_router(intel_router)
app.include_router(inject_router)
app.include_router(control_router)
app.include_router(aar_router)
app.include_router(ws_router)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok", "version": __version__}
