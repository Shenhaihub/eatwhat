"""请求上下文中间件：注入/回传 request_id 并写访问日志。

P7-02：增加请求指标收集（RequestMetrics），用于 /api/v1/system/metrics 端点。
"""

import logging
import threading
import time
import uuid
from collections import deque
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

logger = logging.getLogger("app")

_MAX_REQUEST_ID_LENGTH = 64


class RequestMetrics:
    """进程内请求指标收集器（线程安全）。

    用于 /api/v1/system/metrics 端点展示系统运行状态。
    """

    def __init__(self, maxlen: int = 1000) -> None:
        self._lock = threading.Lock()
        self._recent_latencies: deque[float] = deque(maxlen=maxlen)
        self._total_requests: int = 0
        self._error_requests: int = 0
        self._started_at: float = time.time()

    def record(self, duration_ms: float, status_code: int) -> None:
        with self._lock:
            self._total_requests += 1
            if status_code >= 400:
                self._error_requests += 1
            self._recent_latencies.append(duration_ms)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            latencies = list(self._recent_latencies)
            total = self._total_requests
            errors = self._error_requests
            uptime_s = time.time() - self._started_at

        if latencies:
            sorted_lat = sorted(latencies)
            n = len(sorted_lat)
            p50 = sorted_lat[n // 2]
            p95 = sorted_lat[min(int(n * 0.95), n - 1)]
            p99 = sorted_lat[min(int(n * 0.99), n - 1)]
            avg = sum(latencies) / n
        else:
            p50 = p95 = p99 = avg = 0.0

        return {
            "uptime_seconds": round(uptime_s, 1),
            "total_requests": total,
            "error_requests": errors,
            "error_rate": round(errors / total, 4) if total > 0 else 0.0,
            "recent_sample_size": len(latencies),
            "latency_ms": {
                "avg": round(avg, 1),
                "p50": round(p50, 1),
                "p95": round(p95, 1),
                "p99": round(p99, 1),
            },
        }


# 全局单例
_metrics = RequestMetrics()


def get_request_metrics() -> RequestMetrics:
    return _metrics


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
        _metrics.record(duration_ms, response.status_code)
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
