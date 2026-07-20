"""FastAPI 進入點。healthz + Order pipeline（O3.1）；其餘 REST/WS 端點依里程碑逐步實作。"""

from fastapi import FastAPI

from app import __version__
from app.api import install_error_handlers, intel_router, orders_router

app = FastAPI(title="MATSO Core Orchestrator", version=__version__)

install_error_handlers(app)
app.include_router(orders_router)
app.include_router(intel_router)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok", "version": __version__}
