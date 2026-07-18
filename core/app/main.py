"""FastAPI 進入點。M0 階段僅提供 healthz；REST/WS 端點於 M3+ 依 contracts/core_api.yaml 實作。"""

from fastapi import FastAPI

from app import __version__

app = FastAPI(title="MATSO Core Orchestrator", version=__version__)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok", "version": __version__}
