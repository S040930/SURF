"""统一 API 异常处理。

所有响应统一为 `{"detail": <字符串>}`，消除"校验 422 是数组、业务
422 是字符串"的双形态，未捕获异常统一收敛为可读的 500 文案。
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

logger = logging.getLogger("api.errors")


def _validation_detail(errors: list) -> str:
    first = errors[0]
    field = ".".join(str(part) for part in first.get("loc", []) if part != "body")
    msg = first.get("msg", "参数校验失败")
    return f"{field}: {msg}" if field else msg


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(RequestValidationError)
    async def _handle_validation(
        _request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={"detail": _validation_detail(exc.errors())},
        )

    @app.exception_handler(HTTPException)
    async def _handle_http(_request: Request, exc: HTTPException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail},
        )

    @app.exception_handler(Exception)
    async def _handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content={"detail": "服务器内部错误"},
        )
