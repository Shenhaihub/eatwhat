"""EatWhat 后端应用入口。

`create_app(settings=...)` 工厂便于测试注入配置；模块级 `app = create_app()`
供 `uvicorn app.main:app` 使用。
"""

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.api.system import router as system_router
from app.api.v1 import router as questionnaire_v1_router
from app.core.ai_stats import configure_ai_call_logging
from app.core.config import Settings, get_settings
from app.core.exceptions import register_exception_handlers
from app.core.logging import configure_logging
from app.core.middleware import RequestContextMiddleware


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    # P2 起在此初始化数据库/Provider；P1-03 无外部资源
    yield


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """P7-04：添加安全响应头，防止常见的 Web 安全问题。

    - X-Content-Type-Options: nosniff → 阻止 MIME 类型嗅探
    - X-Frame-Options: DENY → 阻止点击劫持
    - X-XSS-Protection: 1; mode=block → 旧版浏览器 XSS 过滤
    - Referrer-Policy: strict-origin-when-cross-origin → 控制 Referer 泄露
    """

    async def dispatch(self, request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("X-XSS-Protection", "1; mode=block")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        return response


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings)
    configure_ai_call_logging(settings)

    app = FastAPI(
        title="EatWhat API",
        version="0.1.0",
        lifespan=lifespan,
        # P7-04：限制请求体大小（10MB，防止超大 payload 攻击）
        max_request_size=10 * 1024 * 1024,
    )
    app.state.settings = settings

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.frontend_origins_list,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID"],
    )
    # 安全响应头（在 CORS 之后，确保所有响应都带安全头）
    app.add_middleware(SecurityHeadersMiddleware)
    # 后 add 的中间件在外层，保证 request_id 最外层（CORS preflight 也带该头）
    app.add_middleware(RequestContextMiddleware)

    register_exception_handlers(app)
    app.include_router(system_router)
    app.include_router(questionnaire_v1_router)
    return app


app = create_app()
