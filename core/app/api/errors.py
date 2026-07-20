"""統一錯誤處理（O3.1）——領域例外與請求驗證錯誤轉為契約的 Error 格式。

契約格式（core_api.yaml Error）：{"error": {"code", "message", "details"}}。
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.errors import MatsoError


def _error_body(code: str, message: str, details: dict[str, Any]) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "details": details}}


async def _matso_handler(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, MatsoError)
    return JSONResponse(
        status_code=exc.http_status,
        content=_error_body(exc.error_code, exc.message, exc.details),
    )


async def _request_validation_handler(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    return JSONResponse(
        status_code=422,
        content=_error_body("ORDER_INVALID_PAYLOAD", "請求載荷格式錯誤", {"errors": exc.errors()}),
    )


def install_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(MatsoError, _matso_handler)
    app.add_exception_handler(RequestValidationError, _request_validation_handler)
