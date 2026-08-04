"""请求上下文中间件：注入/回传 request_id 并写访问日志。"""

import logging
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

logger = logging.getLogger("app")

_MAX_REQUEST_ID_LENGTH = 64


class RequestContextMiddleware(BaseHTTPMiddleware):
    """读取或生成 `X-Request-ID`，注入响应头并记录访问日志。"""

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = request.headers.get("X-Request-ID", "").strip()
        if not request_id or len(request_id) > _MAX_REQUEST_ID_LENGTH:
            request_id = uuid.uuid4().hex
        request.state.request_id = request_id
        start_time = time.perf_counter()

        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id

        duration_ms = (time.perf_counter() - start_time) * 1000
        logger.info(
            "http_request method=%s path=%s status=%s duration_ms=%.1f request_id=%s error_code=%s",
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
            request_id,
            getattr(request.state, "error_code", None) or "",
        )
        return response
