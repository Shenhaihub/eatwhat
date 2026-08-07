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
from typing import Self

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


# ======================================================
# 4. AmapPOIProvider + AutoFailover 契约测试（P3-04）
# ======================================================

from app.schemas.poi import POIItem, POIProviderName
from app.services.poi_provider import (
    AMAP_AROUND_URL,
    AmapPOIProvider,
    AutoFailoverPOIProvider,
    _amap_poi_to_item,
    _category_to_label,
    get_poi_provider,
)

# ---- 4.1 字段映射：纯函数无 IO，最值得覆盖 ----

def _sample_amap_poi(overrides: dict | None = None) -> dict:
    base = {
        "id": "B0FFFXXXXX",
        "name": "真功夫（光谷店）",
        "type": "餐饮服务;中式快餐;中式快餐",
        "address": "珞喻路光谷世界城",
        "distance": "420",
        "location": "114.400000,30.500000",  # GCJ-02，仅 map_uri 局部使用，不进入响应字段
        "cityname": "武汉市",
        "adname": "洪山区",
        "pname": "湖北省",
    }
    if overrides:
        base.update(overrides)
    return base


class TestAmapFieldMapping:
    """Amap → POIItem 纯函数映射，覆盖边界字段。"""

    def test_normal_poi_maps_correctly(self) -> None:
        ctx = _make_ctx()
        assert ctx is not None
        item = _amap_poi_to_item(_sample_amap_poi(), ctx, fallback_distance=999)
        assert item is not None
        assert isinstance(item, POIItem)
        assert item.provider == POIProviderName.AMAP
        assert item.poi_id == "B0FFFXXXXX"
        assert item.name == "真功夫（光谷店）"
        assert item.category_text == "餐饮服务;中式快餐"
        assert item.distance_m == 420
        assert item.address == "珞喻路光谷世界城"
        assert item.city_name == "武汉市"
        assert item.district_name == "洪山区"
        # G-16：响应字段不包含坐标（lat/lng/location 等）
        dump = item.model_dump()
        for forbidden in ("lat", "lng", "latitude", "longitude", "location"):
            assert forbidden not in dump

    def test_missing_address_filled_by_region(self) -> None:
        ctx = _make_ctx()
        assert ctx is not None
        poi = _sample_amap_poi({"address": ""})
        item = _amap_poi_to_item(poi, ctx, fallback_distance=100)
        assert item is not None
        assert "武汉市洪山区" in item.address

    def test_missing_distance_uses_fallback(self) -> None:
        ctx = _make_ctx()
        assert ctx is not None
        poi = _sample_amap_poi({"distance": None})
        item = _amap_poi_to_item(poi, ctx, fallback_distance=555)
        assert item is not None
        assert item.distance_m == 555

    def test_missing_id_or_name_skipped(self) -> None:
        ctx = _make_ctx()
        assert ctx is not None
        assert _amap_poi_to_item(_sample_amap_poi({"id": ""}), ctx, 100) is None
        assert _amap_poi_to_item(_sample_amap_poi({"name": ""}), ctx, 100) is None

    def test_category_normalization_reduces_to_two_levels(self) -> None:
        assert _category_to_label({"type": "餐饮服务;中餐厅;川菜"}) == "餐饮服务;中餐厅"
        assert _category_to_label({"type": "餐饮服务;饮品店"}) == "餐饮服务;饮品店"
        assert _category_to_label({"type": "餐饮服务"}) == "餐饮服务"
        assert _category_to_label({}) == "餐饮服务;其他"


# ---- 4.2 AmapPOIProvider：模拟 httpx 返回，走全量正常/异常路径 ----

def _fake_amap_resp(pois: list[dict] | None, *, status: str = "1", infocode: str = "10000", info: str = "OK") -> dict:
    return {"status": status, "infocode": infocode, "info": info, "pois": pois or []}


class _FakeAsyncClient:
    """替代 httpx.AsyncClient，记录调用 + 预存响应。"""

    def __init__(self, resp_json: dict | None, *, raise_http: type[Exception] | None = None, calls: list) -> None:
        self._resp_json = resp_json
        self._raise_http = raise_http
        self._calls = calls

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def get(self, url: str, params: dict) -> _FakeResp:
        self._calls.append((url, params))
        if self._raise_http is not None:
            raise self._raise_http("simulated")
        return _FakeResp(self._resp_json)


class _FakeResp:
    def __init__(self, json_val: dict | None) -> None:
        self._json = json_val

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict | None:
        return self._json


class TestAmapPOIProviderHttp:
    """通过 monkeypatch httpx.AsyncClient，验证 AmapPOIProvider 全路径。"""

    def test_normal_returns_amap_items(self, monkeypatch: pytest.MonkeyPatch) -> None:
        ctx = _make_ctx()
        assert ctx is not None
        pois = [_sample_amap_poi({"id": f"B{i:04d}", "distance": str(100 + i * 50)}) for i in range(3)]
        calls: list = []

        def _factory(*a, **kw):
            return _FakeAsyncClient(_fake_amap_resp(pois), calls=calls)

        monkeypatch.setattr("httpx.AsyncClient", _factory)
        prov = AmapPOIProvider(api_key="test_key_123", max_retries=0)
        res = asyncio.run(
            prov.search_nearby_restaurants(
                food_code="malatang",
                location_context=ctx,
                radius_m=3000,
                limit=5,
                cursor=None,
            )
        )
        assert res.meta.provider_mode == "live"
        assert res.meta.cached is False
        assert len(res.data) == 3
        assert all(it.provider == POIProviderName.AMAP for it in res.data)
        # 请求参数正确
        assert len(calls) == 1
        url, params = calls[0]
        assert url == AMAP_AROUND_URL
        assert params["key"] == "test_key_123"
        assert params["location"].endswith(",30.500000") or params["location"].startswith("114.400000,")
        assert params["radius"] == "3000"
        assert params["keywords"]  # 非空（从 food_dictionary 取或 fallback）

    def test_empty_pois_returns_suggestions(self, monkeypatch: pytest.MonkeyPatch) -> None:
        ctx = _make_ctx()
        assert ctx is not None
        calls: list = []

        def _factory(*a, **kw):
            return _FakeAsyncClient(_fake_amap_resp([]), calls=calls)

        monkeypatch.setattr("httpx.AsyncClient", _factory)
        prov = AmapPOIProvider(api_key="k", max_retries=0)
        res = asyncio.run(
            prov.search_nearby_restaurants(
                food_code="malatang",
                location_context=ctx,
                radius_m=1500,
                limit=5,
                cursor=None,
            )
        )
        assert res.data == []
        assert res.meta.provider_mode == "live"
        actions = [s.action for s in res.suggestions]
        assert "expand_radius" in actions
        expand = next(s for s in res.suggestions if s.action == "expand_radius")
        assert expand.radius_m == 3000  # 1500 * 2

    def test_upstream_infocode_error_raises_503(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.core.exceptions import AppError

        ctx = _make_ctx()
        assert ctx is not None
        calls: list = []

        def _factory(*a, **kw):
            return _FakeAsyncClient(
                _fake_amap_resp(None, status="0", infocode="10005", info="QPS LIMIT"),
                calls=calls,
            )

        monkeypatch.setattr("httpx.AsyncClient", _factory)
        prov = AmapPOIProvider(api_key="k", max_retries=0)
        with pytest.raises(AppError) as ei:
            asyncio.run(
                prov.search_nearby_restaurants(
                    food_code="malatang", location_context=ctx, radius_m=1000, limit=5, cursor=None
                )
            )
        assert ei.value.status_code == 503
        assert ei.value.details["infocode"] == "10005"

    def test_all_http_retries_fail_raises_503(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from httpx import ConnectError  # 只要是异常类即可

        from app.core.exceptions import AppError

        ctx = _make_ctx()
        assert ctx is not None
        calls: list = []

        def _factory(*a, **kw):
            return _FakeAsyncClient(None, raise_http=ConnectError, calls=calls)

        monkeypatch.setattr("httpx.AsyncClient", _factory)
        prov = AmapPOIProvider(api_key="k", max_retries=2, http_timeout_s=0.01)
        with pytest.raises(AppError) as ei:
            asyncio.run(
                prov.search_nearby_restaurants(
                    food_code="malatang", location_context=ctx, radius_m=1000, limit=5, cursor=None
                )
            )
        assert ei.value.status_code == 503
        # 重试次数：1 首次 + 2 重试 = 3 次
        assert len(calls) == 3


# ---- 4.3 AutoFailoverPOIProvider：缓存 + auto 降级窗口 ----

class _FailingAmap(AmapPOIProvider):
    """永远抛 503 的 AmapPOIProvider，用于降级测试。"""

    def __init__(self, *, raise_count: int = 10**9) -> None:
        super().__init__(api_key="DUMMY", max_retries=0)
        self._raise_remaining = raise_count

    async def search_nearby_restaurants(self, *a, **kw):  # type: ignore[override]
        from app.core.exceptions import SERVICE_UNAVAILABLE, AppError

        if self._raise_remaining > 0:
            self._raise_remaining -= 1
            raise AppError(code=SERVICE_UNAVAILABLE, message="live down", status_code=503)
        ctx = kw["location_context"]
        return super().search_nearby_restaurants(*a, **kw) if False else _mock_search_result(ctx)


def _mock_search_result(ctx) -> RestaurantSearchResponseV1:  # type: ignore[name-defined]  # 向前兼容
    from app.schemas.poi import POIProviderName, RestaurantSearchMeta, RestaurantSearchResponseV1
    items = [
        POIItem(
            provider=POIProviderName.AMAP,
            poi_id="A1",
            name="Amap Test",
            category_text="餐饮服务;测试",
            distance_m=100,
            address="测试地址",
            city_name=ctx.city_name,
            district_name=ctx.district_name,
            map_uri="https://uri.amap.com/",
        )
    ]
    return RestaurantSearchResponseV1(
        data=items,
        meta=RestaurantSearchMeta(next_cursor=None, cached=False, provider_mode="live"),
        suggestions=[],
    )


def _from_import_rsv() -> None:
    # 触发 import，让上面的类型注解可用（仅需 import 一次）
    pass


from app.schemas.poi import RestaurantSearchResponseV1

_from_import_rsv()


class TestAutoFailoverPOIProvider:
    def test_cache_hits_same_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """同 key 第二次调用走缓存，meta.cached=True，且不再触达 Live。"""
        ctx = _make_ctx()
        assert ctx is not None
        calls: list[int] = []

        class _CountAmap(AmapPOIProvider):
            async def search_nearby_restaurants(self, *a, **kw):  # type: ignore[override]
                calls.append(1)
                return _mock_search_result(kw["location_context"])

        amap = _CountAmap(api_key="DUMMY", max_retries=0)
        wrapper = AutoFailoverPOIProvider(live=amap, mock=MockPOIProvider(), mode="auto", cache_ttl_s=60)
        r1 = asyncio.run(
            wrapper.search_nearby_restaurants(
                food_code="X", location_context=ctx, radius_m=1000, limit=5, cursor=None
            )
        )
        r2 = asyncio.run(
            wrapper.search_nearby_restaurants(
                food_code="X", location_context=ctx, radius_m=1000, limit=5, cursor=None
            )
        )
        assert len(calls) == 1
        assert r1.meta.cached is False
        assert r2.meta.cached is True
        assert [i.poi_id for i in r1.data] == [i.poi_id for i in r2.data]

    def test_auto_single_failure_falls_back_to_mock_without_window(self) -> None:
        """auto 模式下 Live 单次失败：立即给 Mock 数据，不进入窗口。"""
        ctx = _make_ctx()
        assert ctx is not None
        wrapper = AutoFailoverPOIProvider(
            live=_FailingAmap(raise_count=1), mock=MockPOIProvider(), mode="auto", cache_ttl_s=0
        )
        res = asyncio.run(
            wrapper.search_nearby_restaurants(
                food_code="M", location_context=ctx, radius_m=1000, limit=3, cursor=None
            )
        )
        # 1 次失败 → fallback Mock，窗口未触发
        assert res.meta.provider_mode == "mock"
        assert res.data[0].provider == POIProviderName.MOCK

    def test_auto_consecutive_failures_trigger_window(self) -> None:
        """连续 N 次失败 → 触发降级窗口，下一次直接 Mock 不触达 Live。"""
        ctx = _make_ctx()
        assert ctx is not None
        live = _FailingAmap(raise_count=100)
        wrapper = AutoFailoverPOIProvider(live=live, mock=MockPOIProvider(), mode="auto", cache_ttl_s=0)
        # 连续 5 次失败，触发窗口
        for _ in range(5):
            asyncio.run(
                wrapper.search_nearby_restaurants(
                    food_code="M", location_context=ctx, radius_m=1000, limit=3, cursor=None
                )
            )
        assert live._raise_remaining == 100 - 5
        # 第 6 次：直接 Mock，不再触达 Live
        before = live._raise_remaining
        res = asyncio.run(
            wrapper.search_nearby_restaurants(
                food_code="M2", location_context=ctx, radius_m=1000, limit=3, cursor=None
            )
        )
        assert live._raise_remaining == before
        assert res.meta.provider_mode == "mock"

    def test_live_mode_failure_does_not_degrade(self) -> None:
        """live 模式：Live 失败直接抛错，不给 Mock 兜底。"""
        from app.core.exceptions import AppError

        ctx = _make_ctx()
        assert ctx is not None
        wrapper = AutoFailoverPOIProvider(
            live=_FailingAmap(raise_count=10), mock=MockPOIProvider(), mode="live", cache_ttl_s=0
        )
        with pytest.raises(AppError):
            asyncio.run(
                wrapper.search_nearby_restaurants(
                    food_code="M", location_context=ctx, radius_m=1000, limit=3, cursor=None
                )
            )

    def test_mock_mode_never_uses_live(self, monkeypatch: pytest.MonkeyPatch) -> None:
        ctx = _make_ctx()
        assert ctx is not None
        calls: list = []

        class _AssertLive(AmapPOIProvider):
            async def search_nearby_restaurants(self, *a, **kw):  # type: ignore[override]
                calls.append(1)
                return _mock_search_result(kw["location_context"])

        wrapper = AutoFailoverPOIProvider(
            live=_AssertLive(api_key="D"), mock=MockPOIProvider(), mode="mock", cache_ttl_s=0
        )
        r = asyncio.run(
            wrapper.search_nearby_restaurants(
                food_code="X", location_context=ctx, radius_m=1000, limit=5, cursor=None
            )
        )
        assert calls == []
        assert r.meta.provider_mode == "mock"
        assert all(it.provider == POIProviderName.MOCK for it in r.data)


# ---- 4.4 工厂：get_poi_provider 按 Settings 模式行为正确 ----

class TestPOIProviderFactory:
    def setup_method(self) -> None:
        reset_poi_provider()

    def teardown_method(self) -> None:
        reset_poi_provider()

    def test_default_mock_returns_mock_provider(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("POI_PROVIDER", "mock")
        monkeypatch.setenv("AMAP_API_KEY", "")
        # 让 get_settings 重新读取
        from app.core.config import get_settings as _gs
        _gs.cache_clear()
        reset_poi_provider()
        p = get_poi_provider()
        # 默认 mock 模式返回 MockPOIProvider（属性可验证）
        assert isinstance(p, MockPOIProvider)

    def test_auto_with_key_returns_wrapper(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("POI_PROVIDER", "auto")
        monkeypatch.setenv("AMAP_API_KEY", "some_key")
        from app.core.config import get_settings as _gs
        _gs.cache_clear()
        reset_poi_provider()
        p = get_poi_provider()
        assert isinstance(p, AutoFailoverPOIProvider)
        assert p.provider_mode == "auto"

    def test_live_without_key_raises_fast(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("POI_PROVIDER", "live")
        monkeypatch.setenv("AMAP_API_KEY", "")
        from app.core.config import get_settings as _gs
        _gs.cache_clear()
        reset_poi_provider()
        with pytest.raises(ValueError, match="AMAP_API_KEY 未配置"):
            get_poi_provider()
