"""统一错误响应与异常处理器。

错误响应结构（对齐 07 §4.2）：
`{"error": {"code", "message", "details", "request_id"}}`
message 可直接展示给用户、不含堆栈；技术细节只写日志。
"""

import logging
from typing import Any, cast

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import JSONResponse

logger = logging.getLogger("app")

VALIDATION_ERROR = "VALIDATION_ERROR"
RATE_LIMITED = "RATE_LIMITED"
DATABASE_UNAVAILABLE = "DATABASE_UNAVAILABLE"
INTERNAL_ERROR = "INTERNAL_ERROR"
NOT_FOUND = "NOT_FOUND"
BAD_REQUEST = "BAD_REQUEST"
SERVICE_UNAVAILABLE = "SERVICE_UNAVAILABLE"

_HTTP_TO_CODE = {
    400: BAD_REQUEST,
    404: NOT_FOUND,
    429: RATE_LIMITED,
    500: INTERNAL_ERROR,
    503: SERVICE_UNAVAILABLE,
}

_HTTP_MESSAGES = {
    400: "请求无效",
    404: "资源不存在",
    429: "请求过于频繁，请稍后再试",
    500: "服务器内部错误，请稍后再试",
    503: "服务暂时不可用",
}


class AppError(Exception):
    """业务可控错误：携带错误码、用户可读信息和可选详情。"""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int = 500,
        details: dict[str, Any] | list[Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details


def get_request_id(request: Request) -> str:
    return str(getattr(request.state, "request_id", ""))


def build_error_body(
    code: str,
    message: str,
    request_id: str,
    details: dict[str, Any] | list[Any] | None = None,
) -> dict[str, object]:
    return {
        "error": {
            "code": code,
            "message": message,
            "details": details,
            "request_id": request_id,
        }
    }


def _record_error(request: Request, code: str) -> None:
    request.state.error_code = code


async def app_error_handler(request: Request, exc: Exception) -> JSONResponse:
    error = cast(AppError, exc)
    _record_error(request, error.code)
    logger.error(
        "app_error code=%s path=%s request_id=%s",
        error.code,
        request.url.path,
        get_request_id(request),
    )
    return JSONResponse(
        status_code=error.status_code,
        content=build_error_body(error.code, error.message, get_request_id(request), error.details),
    )


async def validation_error_handler(request: Request, exc: Exception) -> JSONResponse:
    error = cast(RequestValidationError, exc)
    _record_error(request, VALIDATION_ERROR)
    details: list[dict[str, str]] = [
        {"field": ".".join(str(part) for part in item["loc"]), "message": item["msg"]}
        for item in error.errors()
    ]
    # 注意：不把 `item["input"]` 放进 details，输入可能含敏感值
    return JSONResponse(
        status_code=422,
        content=build_error_body(VALIDATION_ERROR, "请求参数校验失败", get_request_id(request), details),
    )


async def http_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    error = cast(StarletteHTTPException, exc)
    code = _HTTP_TO_CODE.get(error.status_code, INTERNAL_ERROR)
    message = _HTTP_MESSAGES.get(error.status_code, "请求失败")
    # 允许业务代码抛 HTTPException(detail={"code":..., "message":...}) 透传
    if isinstance(error.detail, dict):
        code = str(error.detail.get("code", code))
        message = str(error.detail.get("message", message))
    _record_error(request, code)
    return JSONResponse(
        status_code=error.status_code,
        content=build_error_body(code, message, get_request_id(request)),
    )


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    _record_error(request, INTERNAL_ERROR)
    logger.exception(
        "unhandled_error path=%s request_id=%s",
        request.url.path,
        get_request_id(request),
    )
    return JSONResponse(
        status_code=500,
        content=build_error_body(INTERNAL_ERROR, "服务器内部错误，请稍后再试", get_request_id(request)),
    )


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, app_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)
