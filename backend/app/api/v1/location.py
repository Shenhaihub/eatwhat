"""P3-01 Location API：三种入口（浏览器/手动/演示）。

端点（G-16：坐标在 POST body，不在 URL）：
- POST /api/v1/locations/search         手动地点搜索（mock：本地匹配 demo 数据）
- POST /api/v1/locations/reverse         浏览器定位反向地理编码（mock：就近匹配 demo）
- GET  /api/v1/locations/demo            演示地点列表
- POST /api/v1/locations/demo/{code}/select  选择演示地点

所有端点返回 location_token（不透明，短 TTL），后续商家搜索用 token 而非坐标。
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from app.core.exceptions import NOT_FOUND, AppError
from app.schemas.location import (
    DemoLocationListResponse,
    DemoLocationSelectResponse,
    LocationReverseRequestV1,
    LocationReverseResponseV1,
    LocationSearchRequestV1,
    LocationSearchResponseV1,
    LocationSource,
    LocationTokenInfo,
)
from app.services.location import (
    find_demo_record,
    get_token_store,
    load_demo_locations,
    reverse_geocode_mock,
    search_demo_locations,
    wgs84_to_gcj02,
)

router = APIRouter(prefix="/api/v1/locations", tags=["locations"])


def _issue_token_info(
    *,
    lat_gcj02: float,
    lng_gcj02: float,
    display_name: str,
    city_name: str,
    district_name: str,
    source: LocationSource,
) -> LocationTokenInfo:
    """签发 token 并返回 API 响应对象（不含坐标）。"""
    store = get_token_store()
    token = store.issue(
        lat_gcj02=lat_gcj02,
        lng_gcj02=lng_gcj02,
        display_name=display_name,
        city_name=city_name,
        district_name=district_name,
        source=source,
    )
    return LocationTokenInfo(
        location_token=token,
        display_name=display_name,
        city_name=city_name,
        district_name=district_name,
    )


@router.post("/search", response_model=LocationSearchResponseV1)
async def search_locations(request: Request, body: LocationSearchRequestV1) -> LocationSearchResponseV1:
    """手动地点搜索（P3-01 mock：本地匹配 demo 数据）。

    P3-04 接入高德 Live 时替换为真实 POI 搜索 API。
    """
    results = search_demo_locations(body.keyword, limit=body.limit)
    if not results:
        # 空结果不报错，返回空列表（前端提示"未找到，试试演示地点"）
        return LocationSearchResponseV1(data=[])

    data: list[LocationTokenInfo] = []
    for r in results:
        info = _issue_token_info(
            lat_gcj02=r.lat_gcj02,
            lng_gcj02=r.lng_gcj02,
            display_name=r.display_name,
            city_name=r.city_name,
            district_name=r.district_name,
            source=LocationSource.MANUAL,
        )
        data.append(info)
    return LocationSearchResponseV1(data=data)


@router.post("/reverse", response_model=LocationReverseResponseV1)
async def reverse_geocode(request: Request, body: LocationReverseRequestV1) -> LocationReverseResponseV1:
    """浏览器定位反向地理编码（P3-01 mock：WGS84→GCJ-02 后就近匹配 demo）。

    G-16：坐标在 POST body，不在 URL；转换后坐标只存内存。
    """
    # WGS84 → GCJ-02（为 P3-04 高德 POI 搜索准备）
    lat_gcj, lng_gcj = wgs84_to_gcj02(body.latitude, body.longitude)

    # mock 反向地理编码：就近匹配演示地点
    demo = reverse_geocode_mock(body.latitude, body.longitude)

    info = _issue_token_info(
        lat_gcj02=lat_gcj,
        lng_gcj02=lng_gcj,
        display_name=demo.display_name,
        city_name=demo.city_name,
        district_name=demo.district_name,
        source=LocationSource.BROWSER,
    )
    return LocationReverseResponseV1(data=info)


@router.get("/demo", response_model=DemoLocationListResponse)
async def list_demo_locations(request: Request) -> DemoLocationListResponse:
    """演示地点列表（不含坐标，不含 token）。"""
    data = load_demo_locations()
    return DemoLocationListResponse(data=data.items)


@router.post("/demo/{code}/select", response_model=DemoLocationSelectResponse)
async def select_demo_location(request: Request, code: str) -> DemoLocationSelectResponse:
    """选择演示地点，签发 location_token。"""
    record = find_demo_record(code)
    if record is None:
        raise AppError(
            code=NOT_FOUND,
            message=f"演示地点不存在：{code}",
            status_code=404,
            details={"code": code},
        )
    info = _issue_token_info(
        lat_gcj02=record.lat_gcj02,
        lng_gcj02=record.lng_gcj02,
        display_name=record.display_name,
        city_name=record.city_name,
        district_name=record.district_name,
        source=LocationSource.DEMO,
    )
    return DemoLocationSelectResponse(data=info)
