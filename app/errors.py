"""统一错误响应。

约定（与 ``docs/API.md`` 一致）：**所有错误响应都带 ``request_id``**，
客户端报错时把它贴给后端即可定位到那一条访问日志。

响应体形状::

    {"detail": <字符串或对象>, "request_id": "<id>"}

- 业务校验失败（剧本为空、声音描述为空）：422 + 字符串 detail；
- 请求体不合法（FastAPI 自动校验）：422，``detail.errors`` 给出逐字段原因；
- 未预期异常：500，不回显堆栈，只回 ``request_id``。
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.logging import get_logger, get_request_id

log = get_logger("app.errors")


def _body(detail: Any) -> dict[str, Any]:
    return {"detail": detail, "request_id": get_request_id()}


def _validation_detail(exc: RequestValidationError) -> dict[str, Any]:
    errors = [
        {
            "loc": [str(part) for part in err.get("loc", ())],
            "msg": str(err.get("msg", "")),
            "type": str(err.get("type", "")),
        }
        for err in exc.errors()
    ]
    return {"message": "请求参数校验失败", "errors": errors}


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=_body(exc.detail), headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(status_code=422, content=_body(_validation_detail(exc)))

    @app.exception_handler(Exception)
    async def _unhandled_error(request: Request, exc: Exception) -> JSONResponse:
        log.exception(
            "unhandled_error",
            extra={"method": request.method, "path": request.url.path, "error_type": type(exc).__name__},
        )
        return JSONResponse(status_code=500, content=_body("服务内部错误，请携带 request_id 反馈"))
