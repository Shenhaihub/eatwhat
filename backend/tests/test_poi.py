"""P3-02 Mock POIProvider 四态 + 商户搜索 API 测试。

覆盖：
1. MockPOIProvider 四态（normal/empty/slow/error）
2. POST /api/v1/restaurants/search 端到端
3. G-16 合规：响应不含坐标
4. food_code 校验、location_token 校验
5. 确定性：同一 food_code 多次调用结果一致
"""

from __future__ import annotations

import asyncio
import time

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.schemas.location import LocationSource
from app.services.location import get_token_store
from app.services.poi_provider import (
    MockPOIProvider,
    reset_poi_provider,
)

# ======================================================
# 1. MockPOIProvider 单元测试（四态）
# ======================================================


def _make_ctx():
    """构造一个测试用 LocationContext（通过 token store 签发）。"""
    store = get_token_store()
    token = store.issue(
        lat_gcj02=30.5,
        lng_gcj02=114.4,
        display_name="光谷广场",
        city_name="武汉市",
        district_name="洪山区",
        source=LocationSource.DEMO,
    )
    return store.resolve(token)


class TestMockPOIProviderFourModes:
    """P3-02 验收：normal/empty/slow/error 四类场景可重复触发。"""

    def setup_method(self) -> None:
        reset_poi_provider()

    def teardown_method(self) -> None:
        reset_poi_provider()

    def test_normal_returns_5_items_sorted_by_distance(self) -> None:
        ctx = _make_ctx()
        assert ctx is not None
        provider = MockPOIProvider(default_mode="normal")
        result = asyncio.run(
            provider.search_nearby_restaurants(
                food_code="malatang",
                location_context=ctx,
                radius_m=3000,
                limit=5,
                cursor=None,
            )
        )
        assert len(result.data) == 5
        assert result.meta.provider_mode == "mock"
        # 距离升序
        distances = [item.distance_m for item in result.data]
        assert distances == sorted(distances)
        # 所有商户名称含"示例"前缀
        for item in result.data:
            assert item.name.startswith("示例")
            assert item.provider.value == "mock"
            assert item.poi_id.startswith("mock_malatang_")

    def test_empty_returns_empty_data_with_suggestions(self) -> None:
        ctx = _make_ctx()
        assert ctx is not None
        provider = MockPOIProvider(default_mode="empty")
        result = asyncio.run(
            provider.search_nearby_restaurants(
                food_code="malatang",
                location_context=ctx,
                radius_m=1000,
                limit=5,
                cursor=None,
            )
        )
        assert result.data == []
        assert result.meta.provider_mode == "mock"
        assert len(result.suggestions) == 2
        actions = [s.action for s in result.suggestions]
        assert "expand_radius" in actions
        assert "select_other_food" in actions
        # expand_radius 建议半径翻倍
        expand = next(s for s in result.suggestions if s.action == "expand_radius")
        assert expand.radius_m == 2000

    def test_slow_returns_after_delay(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("POI_MOCK_SLOW_MS", "50")
        ctx = _make_ctx()
        assert ctx is not None
        provider = MockPOIProvider(default_mode="slow")
        start = time.monotonic()
        result = asyncio.run(
            provider.search_nearby_restaurants(
                food_code="malatang",
                location_context=ctx,
                radius_m=1000,
                limit=5,
                cursor=None,
                mock_mode="slow",
            )
        )
        elapsed = time.monotonic() - start
        # 延迟 50ms，至少 > 0.04s
        assert elapsed >= 0.04
        # slow 模式最终也返回 normal 结果
        assert len(result.data) == 5

    def test_error_raises_app_error_503(self) -> None:
        ctx = _make_ctx()
        assert ctx is not None
        provider = MockPOIProvider(default_mode="error")
        from app.core.exceptions import AppError

        with pytest.raises(AppError) as exc_info:
            asyncio.run(
                provider.search_nearby_restaurants(
                    food_code="malatang",
                    location_context=ctx,
                    radius_m=1000,
                    limit=5,
                    cursor=None,
                )
            )
        assert exc_info.value.status_code == 503
        assert exc_info.value.code == "SERVICE_UNAVAILABLE"
        assert exc_info.value.details["mock_mode"] == "error"

    def test_mock_mode_override_takes_priority(self) -> None:
        """请求体 mock_mode 优先于 provider 默认模式。"""
        ctx = _make_ctx()
        assert ctx is not None
        provider = MockPOIProvider(default_mode="normal")
        # 用 mock_mode=empty 覆盖 normal
        result = asyncio.run(
            provider.search_nearby_restaurants(
                food_code="malatang",
                location_context=ctx,
                radius_m=1000,
                limit=5,
                cursor=None,
                mock_mode="empty",
            )
        )
        assert result.data == []

    def test_deterministic_same_food_code_same_results(self) -> None:
        """同一 food_code 多次调用返回相同结果。"""
        ctx = _make_ctx()
        assert ctx is not None
        provider = MockPOIProvider(default_mode="normal")
        r1 = asyncio.run(
            provider.search_nearby_restaurants(
                food_code="beef_noodles",
                location_context=ctx,
                radius_m=3000,
                limit=5,
                cursor=None,
            )
        )
        r2 = asyncio.run(
            provider.search_nearby_restaurants(
                food_code="beef_noodles",
                location_context=ctx,
                radius_m=3000,
                limit=5,
                cursor=None,
            )
        )
        assert [i.poi_id for i in r1.data] == [i.poi_id for i in r2.data]
        assert [i.distance_m for i in r1.data] == [i.distance_m for i in r2.data]

    def test_different_food_codes_produce_different_poi_ids(self) -> None:
        ctx = _make_ctx()
        assert ctx is not None
        provider = MockPOIProvider(default_mode="normal")
        r1 = asyncio.run(
            provider.search_nearby_restaurants(
                food_code="malatang",
                location_context=ctx,
                radius_m=3000,
                limit=5,
                cursor=None,
            )
        )
        r2 = asyncio.run(
            provider.search_nearby_restaurants(
                food_code="beef_noodles",
                location_context=ctx,
                radius_m=3000,
                limit=5,
                cursor=None,
            )
        )
        assert [i.poi_id for i in r1.data] != [i.poi_id for i in r2.data]


# ======================================================
# 2. POST /api/v1/restaurants/search 端到端
# ======================================================

_client = TestClient(create_app())


def _issue_token() -> str:
    """通过 API 签发一个演示地点 token。"""
    resp = _client.post("/api/v1/locations/demo/wuhan_optics_valley/select")
    assert resp.status_code == 200
    return resp.json()["data"]["location_token"]


class TestRestaurantSearchAPI:
    def setup_method(self) -> None:
        reset_poi_provider()

    def teardown_method(self) -> None:
        reset_poi_provider()

    def test_search_normal(self) -> None:
        token = _issue_token()
        resp = _client.post(
            "/api/v1/restaurants/search",
            json={
                "food_code": "malatang",
                "location_token": token,
                "radius_m": 3000,
                "limit": 5,
                "mock_mode": "normal",
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert len(body["data"]) == 5
        assert body["meta"]["provider_mode"] == "mock"
        assert body["meta"]["request_id"]  # 注入了 request_id
        # G-16：响应不含坐标
        for item in body["data"]:
            assert "lat" not in item
            assert "lng" not in item
            assert "latitude" not in item
            assert "longitude" not in item
            assert "lat_gcj02" not in item
            assert "lng_gcj02" not in item

    def test_search_empty(self) -> None:
        token = _issue_token()
        resp = _client.post(
            "/api/v1/restaurants/search",
            json={
                "food_code": "malatang",
                "location_token": token,
                "mock_mode": "empty",
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["data"] == []
        assert len(body["suggestions"]) == 2

    def test_search_error_503(self) -> None:
        token = _issue_token()
        resp = _client.post(
            "/api/v1/restaurants/search",
            json={
                "food_code": "malatang",
                "location_token": token,
                "mock_mode": "error",
            },
        )
        assert resp.status_code == 503
        body = resp.json()
        assert body["error"]["code"] == "SERVICE_UNAVAILABLE"

    def test_search_invalid_location_token_400(self) -> None:
        resp = _client.post(
            "/api/v1/restaurants/search",
            json={
                "food_code": "malatang",
                "location_token": "0" * 32,  # 不存在的 token
                "mock_mode": "normal",
            },
        )
        assert resp.status_code == 400
        body = resp.json()
        assert body["error"]["code"] == "BAD_REQUEST"
        assert "location_token" in body["error"]["message"]

    def test_search_invalid_food_code_400(self) -> None:
        token = _issue_token()
        resp = _client.post(
            "/api/v1/restaurants/search",
            json={
                "food_code": "nonexistent_food",
                "location_token": token,
                "mock_mode": "normal",
            },
        )
        assert resp.status_code == 400
        body = resp.json()
        assert body["error"]["code"] == "BAD_REQUEST"
        assert "food_code" in body["error"]["message"]

    def test_search_missing_required_field_422(self) -> None:
        resp = _client.post(
            "/api/v1/restaurants/search",
            json={"location_token": "a" * 32},  # 缺 food_code
        )
        assert resp.status_code == 422

    def test_search_extra_field_forbidden_422(self) -> None:
        token = _issue_token()
        resp = _client.post(
            "/api/v1/restaurants/search",
            json={
                "food_code": "malatang",
                "location_token": token,
                "extra": "bad",
            },
        )
        assert resp.status_code == 422

    def test_search_invalid_mock_mode_422(self) -> None:
        token = _issue_token()
        resp = _client.post(
            "/api/v1/restaurants/search",
            json={
                "food_code": "malatang",
                "location_token": token,
                "mock_mode": "invalid_mode",
            },
        )
        assert resp.status_code == 422

    def test_search_radius_out_of_range_422(self) -> None:
        token = _issue_token()
        resp = _client.post(
            "/api/v1/restaurants/search",
            json={
                "food_code": "malatang",
                "location_token": token,
                "radius_m": 999_999,
            },
        )
        assert resp.status_code == 422


# ======================================================
# 3. G-16 合规综合验证
# ======================================================


class TestG16CompliancePOI:
    """G-16：商户搜索响应不含坐标，location_token 不透明。"""

    def test_response_has_no_coordinates(self) -> None:
        token = _issue_token()
        resp = _client.post(
            "/api/v1/restaurants/search",
            json={
                "food_code": "malatang",
                "location_token": token,
                "mock_mode": "normal",
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        for item in body["data"]:
            assert not any(
                k in item
                for k in ("lat", "lng", "latitude", "longitude", "lat_gcj02", "lng_gcj02")
            )

    def test_response_meta_has_provider_mode(self) -> None:
        """meta.provider_mode 必须返回，前端用于来源标注。"""
        token = _issue_token()
        resp = _client.post(
            "/api/v1/restaurants/search",
            json={
                "food_code": "malatang",
                "location_token": token,
                "mock_mode": "normal",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["meta"]["provider_mode"] == "mock"

    def test_merchant_names_use_example_prefix(self) -> None:
        """G-10：mock 商户名称必须含"示例"前缀，避免与真实商户混淆。"""
        token = _issue_token()
        resp = _client.post(
            "/api/v1/restaurants/search",
            json={
                "food_code": "malatang",
                "location_token": token,
                "mock_mode": "normal",
            },
        )
        assert resp.status_code == 200
        for item in resp.json()["data"]:
            assert "示例" in item["name"], f"商户名称 {item['name']} 应含'示例'前缀"
