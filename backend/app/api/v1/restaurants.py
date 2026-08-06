"""P3-02/P3-03 商户搜索 API。

端点（对齐 07_API接口设计.md §23）：
- POST /api/v1/restaurants/search  附近商家搜索

G-16：location_token 解析内存中的 LocationContext，坐标不出内存。
G-10：商户结果称"最近匹配"，不声称"最好/最推荐"。
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Request
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError

from app.core.exceptions import BAD_REQUEST, NOT_FOUND, AppError
from app.repositories.food_dictionary import (
    DEFAULT_DICTIONARY_VERSION,
    get_food_dictionary_repository,
)
from app.schemas.poi import (
    RestaurantSearchRequestV1,
    RestaurantSearchResponseV1,
)
from app.services.location import get_token_store
from app.services.poi_provider import get_poi_provider

router = APIRouter(prefix="/api/v1/restaurants", tags=["restaurants"])


@router.post("/search", response_model=RestaurantSearchResponseV1)
async def search_restaurants(request: Request) -> dict[str, Any]:
    """POST /api/v1/restaurants/search 附近商家搜索。

    流程：
    1. 解析 JSON → 校验 source_type 不存在（G-07 不适用于商户搜索，但仍 extra=forbid）
    2. Pydantic 校验请求体
    3. 校验 food_code 在字典中存在且启用
    4. 用 location_token 解析 LocationContext（过期/不存在 → 400）
    5. 调用 MockPOIProvider（四态可重复触发）
    6. 返回响应（meta.provider_mode = "mock"）
    """
    # 0) 原始 JSON → Pydantic
    try:
        raw_body: Any = await request.json()
    except json.JSONDecodeError:
        raw_body = {}
    except Exception:  # noqa: BLE001
        raw_body = {}

    if not isinstance(raw_body, dict):
        raise RequestValidationError(
            errors=[
                {
                    "type": "value_error",
                    "loc": ("body",),
                    "msg": "请求体必须是 JSON 对象",
                    "input": raw_body,
                }
            ]
        )
    try:
        payload = RestaurantSearchRequestV1.model_validate(raw_body)
    except ValidationError as exc:
        raise RequestValidationError(errors=exc.errors(include_url=False)) from exc

    # 1) 校验 food_code 在字典中存在且启用
    dict_version = DEFAULT_DICTIONARY_VERSION
    try:
        get_food_dictionary_repository.cache_clear()
        repo = get_food_dictionary_repository(dict_version)
    except FileNotFoundError as exc:
        raise AppError(
            code=NOT_FOUND,
            status_code=404,
            message=f"食物字典加载失败：{dict_version}",
            details={"hint": str(exc)},
        ) from exc

    if not repo.contains_enabled(payload.food_code):
        raise AppError(
            code=BAD_REQUEST,
            status_code=400,
            message=f"food_code={payload.food_code!r} 不存在或未启用",
            details={
                "food_code": payload.food_code,
                "dictionary_version": dict_version,
            },
        )

    # 2) 用 location_token 解析 LocationContext
    store = get_token_store()
    ctx = store.resolve(payload.location_token)
    if ctx is None:
        raise AppError(
            code=BAD_REQUEST,
            status_code=400,
            message="location_token 无效或已过期，请重新选择地点",
            details={"hint": "token TTL 默认 30 分钟，过期后需重新获取"},
        )

    # 3) 调用 POIProvider
    provider = get_poi_provider()
    response = await provider.search_nearby_restaurants(
        food_code=payload.food_code,
        location_context=ctx,
        radius_m=payload.radius_m,
        limit=payload.limit,
        cursor=payload.cursor,
        mock_mode=payload.mock_mode,
    )

    # 4) 注入 request_id（从 request.state 取，middleware 已设置）
    request_id = str(getattr(request.state, "request_id", ""))
    response.meta.request_id = request_id

    return response.model_dump()
