"""P3-01 LocationContext schema：三种入口（浏览器/手动/演示）。

G-16 契约：
- 精确坐标（WGS84→GCJ-02）只用于当前附近搜索，不写入 URL、普通日志、业务历史、公共分享。
- 位置上下文短 TTL、绑定会话、不可篡改。
- location_token 是不透明字符串，内部不含坐标，只映射到内存中的 LocationContext。
- API 响应只暴露 display_name/city_name/district_name，绝不暴露坐标。
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class LocationSource(str, Enum):
    """地点来源（名词表 §5）。"""

    BROWSER = "browser"
    MANUAL = "manual"
    DEMO = "demo"


# ---- 内部模型（不直接出现在 API 响应中） ----


class LocationContext(BaseModel):
    """内存中的位置上下文，由 location_token 映射。

    G-16：lat_gcj02/lng_gcj02 只存在于内存，不进日志、不进数据库、不进 API 响应。
    """

    model_config = ConfigDict(extra="forbid")

    lat_gcj02: float = Field(..., description="GCJ-02 纬度（内部，不暴露）")
    lng_gcj02: float = Field(..., description="GCJ-02 经度（内部，不暴露）")
    display_name: str = Field(..., min_length=1, max_length=64)
    city_name: str = Field(..., min_length=1, max_length=32)
    district_name: str = Field(..., min_length=1, max_length=32)
    source: LocationSource
    expires_at: float = Field(..., description="Unix 时间戳，过期时间")


# ---- API 请求 / 响应模型 ----


class LocationSearchRequestV1(BaseModel):
    """POST /api/v1/locations/search 请求体（手动地点入口）。"""

    model_config = ConfigDict(extra="forbid")

    keyword: str = Field(..., min_length=1, max_length=64, description="搜索关键词")
    city: str | None = Field(default=None, max_length=32, description="可选城市限定")
    limit: int = Field(default=5, ge=1, le=10)


class LocationTokenInfo(BaseModel):
    """API 响应中暴露的地点信息（G-16：不含坐标）。"""

    model_config = ConfigDict(extra="forbid")

    location_token: str = Field(..., description="短时不透明 token，用于后续商家搜索")
    display_name: str
    city_name: str
    district_name: str


class LocationSearchResponseV1(BaseModel):
    """POST /api/v1/locations/search 响应体。"""

    model_config = ConfigDict(extra="forbid")

    data: list[LocationTokenInfo]


class LocationReverseRequestV1(BaseModel):
    """POST /api/v1/locations/reverse 请求体（浏览器定位入口）。

    G-16：坐标在 POST body 中，不在 URL。
    坐标系为 WGS84（浏览器原生），后端转换为 GCJ-02 后只存内存。
    """

    model_config = ConfigDict(extra="forbid")

    latitude: float = Field(..., ge=-90.0, le=90.0, description="WGS84 纬度")
    longitude: float = Field(..., ge=-180.0, le=180.0, description="WGS84 经度")


class LocationReverseResponseV1(BaseModel):
    """POST /api/v1/locations/reverse 响应体。"""

    model_config = ConfigDict(extra="forbid")

    data: LocationTokenInfo


class DemoLocationItem(BaseModel):
    """演示地点列表项（不含坐标，不含 token）。"""

    model_config = ConfigDict(extra="forbid")

    code: str = Field(..., pattern=r"^[a-z0-9_]{2,40}$")
    display_name: str
    city_name: str
    district_name: str


class DemoLocationListResponse(BaseModel):
    """GET /api/v1/locations/demo 响应体。"""

    model_config = ConfigDict(extra="forbid")

    data: list[DemoLocationItem]


class DemoLocationSelectResponse(BaseModel):
    """POST /api/v1/locations/demo/{code}/select 响应体。"""

    model_config = ConfigDict(extra="forbid")

    data: LocationTokenInfo


# ---- demo_locations.json 数据 schema ----


class DemoLocationRecord(BaseModel):
    """demo_locations.json 单条记录（含坐标，仅后端加载用）。"""

    model_config = ConfigDict(extra="forbid")

    code: str = Field(..., pattern=r"^[a-z0-9_]{2,40}$")
    display_name: str = Field(..., min_length=1, max_length=64)
    city_name: str = Field(..., min_length=1, max_length=32)
    district_name: str = Field(..., min_length=1, max_length=32)
    lat_gcj02: float = Field(..., ge=-90.0, le=90.0, description="预设 GCJ-02 纬度")
    lng_gcj02: float = Field(..., ge=-180.0, le=180.0, description="预设 GCJ-02 经度")
