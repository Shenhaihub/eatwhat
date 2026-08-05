"""EatWhat 后端应用入口。

`create_app(settings=...)` 工厂便于测试注入配置；模块级 `app = create_app()`
供 `uvicorn app.main:app` 使用。
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.system import router as system_router
from app.api.v1 import router as questionnaire_v1_router
from app.core.config import Settings, get_settings
from app.core.exceptions import register_exception_handlers
from app.core.logging import configure_logging
from app.core.middleware import RequestContextMiddleware


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    # P2 起在此初始化数据库/Provider；P1-03 无外部资源
    yield


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings)

    app = FastAPI(title="EatWhat API", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.frontend_origins_list,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID"],
    )
    # 后 add 的中间件在外层，保证 request_id 最外层（CORS preflight 也带该头）
    app.add_middleware(RequestContextMiddleware)

    register_exception_handlers(app)
    app.include_router(system_router)
    app.include_router(questionnaire_v1_router)
    return app


app = create_app()
