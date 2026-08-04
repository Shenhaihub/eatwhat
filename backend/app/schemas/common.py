"""统一响应模型（供文档与类型标注；错误响应实际由 build_error_body 构造）。"""

from typing import Any, Literal

from pydantic import BaseModel


class ErrorBody(BaseModel):
    code: str
    message: str
    details: dict[str, Any] | list[Any] | None = None
    request_id: str


class ErrorResponse(BaseModel):
    error: ErrorBody


class HealthLiveResponse(BaseModel):
    status: Literal["ok"]


class HealthReadyResponse(BaseModel):
    status: str
    config: str
    database: str
