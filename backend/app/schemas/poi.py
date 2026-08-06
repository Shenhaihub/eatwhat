"""P3-02 POI schema：商户搜索请求/响应模型。

对齐 07_EatWhat_API接口设计.md §23 POST /api/v1/restaurants/search。

G-16 合规：
- API 响应不含精确坐标（lat/lng），只有 distance_m（粗略距离）。
- location_token 是不透明字符串，后端用它解析内存中的 LocationContext。
- provider_mode = "mock" / "live" 必须返回，前端用于来源标注。

设计要点（14_设计审计 §6.3）：
- 商户结果称"最近匹配"，禁止"最好吃/最推荐"。
- 未知营业状态显示未知，不推断。
- 不显示商家价格保证。
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class POIProviderName(str, Enum):
    """POI 数据来源（名词表 §5）。"""

    MOCK = "mock"
    AMAP = "amap"


class POIItem(BaseModel):
    """单条商户结果（对齐 07 §23.1）。

    G-16：不含 lat/lng 精确坐标；distance_m 是粗略距离（整数米）。
    """

    model_config = ConfigDict(extra="forbid")

    provider: POIProviderName = Field(..., description="数据来源")
    poi_id: str = Field(..., min_length=1, max_length=64, description="POI 唯一 ID")
    name: str = Field(..., min_length=1, max_length=64, description="商户名称")
    category_text: str = Field(..., min_length=1, max_length=64, description="分类文本")
    distance_m: int = Field(..., ge=0, le=100_000, description="距离（米）")
    address: str = Field(..., min_length=1, max_length=128, description="粗略地址")
    city_name: str = Field(..., min_length=1, max_length=32)
    district_name: str = Field(..., min_length=1, max_length=32)
    map_uri: str = Field(..., min_length=1, max_length=256, description="地图跳转 URI")


class RestaurantSearchMeta(BaseModel):
    """搜索结果元数据。"""

    model_config = ConfigDict(extra="forbid")

    next_cursor: str | None = Field(default=None, description="分页游标，null 表示无下一页")
    cached: bool = Field(default=False, description="是否命中缓存")
    provider_mode: Literal["mock", "live"] = Field(..., description="数据来源模式")
    request_id: str = Field(default="", description="请求追踪 ID")


class RestaurantSearchSuggestion(BaseModel):
    """无结果时的恢复建议。"""

    model_config = ConfigDict(extra="forbid")

    action: Literal["expand_radius", "select_other_food"]
    radius_m: int | None = Field(default=None, ge=100, le=100_000)


class RestaurantSearchRequestV1(BaseModel):
    """POST /api/v1/restaurants/search 请求体。

    严格校验 + extra=forbid。
    recommendation_id 在 P3 阶段可选（无认证）；P4/P5 接入认证后改为必填。
    mock_mode 仅在 POI_PROVIDER=mock 时生效，用于 UI 重复触发四种状态。
    """

    model_config = ConfigDict(extra="forbid")

    food_code: str = Field(..., min_length=1, max_length=32, description="食物字典 code")
    location_token: str = Field(..., min_length=32, max_length=32, description="地点 token")
    radius_m: int = Field(default=1000, ge=100, le=50_000, description="搜索半径（米）")
    limit: int = Field(default=5, ge=1, le=10, description="返回条数")
    cursor: str | None = Field(default=None, max_length=128, description="分页游标")
    recommendation_id: str | None = Field(
        default=None,
        max_length=64,
        description="推荐 ID（P3 可选，P4/P5 必填）",
    )
    mock_mode: Literal["normal", "empty", "slow", "error"] | None = Field(
        default=None,
        description="测试专用：覆盖 Mock 模式（仅在 POI_PROVIDER=mock 时生效）",
    )


class RestaurantSearchResponseV1(BaseModel):
    """POST /api/v1/restaurants/search 响应体。"""

    model_config = ConfigDict(extra="forbid")

    data: list[POIItem] = Field(default_factory=list)
    meta: RestaurantSearchMeta
    suggestions: list[RestaurantSearchSuggestion] = Field(default_factory=list)


__all__ = [
    "POIItem",
    "POIProviderName",
    "RestaurantSearchMeta",
    "RestaurantSearchRequestV1",
    "RestaurantSearchResponseV1",
    "RestaurantSearchSuggestion",
]
