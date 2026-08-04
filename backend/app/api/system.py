"""系统健康检查。

- `/health/live`：进程存活，不读配置不碰外部资源。
- `/health/ready`：P1-03 无数据库，恒 200；只返回状态字，不回显 key/URL。
  等 P4 接数据库后，live 模式下 DB 不可达时才应返回 503 并从编排移除流量。
"""

from typing import cast

from fastapi import APIRouter, FastAPI, Request

from app.core.config import Settings
from app.schemas.common import HealthLiveResponse, HealthReadyResponse

router = APIRouter(tags=["system"])


@router.get("/health/live", response_model=HealthLiveResponse)
def health_live() -> HealthLiveResponse:
    return HealthLiveResponse(status="ok")


@router.get("/health/ready", response_model=HealthReadyResponse)
def health_ready(request: Request) -> HealthReadyResponse:
    app = cast(FastAPI, request.scope["app"])
    settings: Settings = app.state.settings
    config = "ok"
    database = "not_configured" if not settings.database_configured else "unchecked"
    return HealthReadyResponse(status="ready", config=config, database=database)
