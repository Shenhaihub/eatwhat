"""P3-01 Location API + 坐标转换 + token 管理测试。

覆盖：
1. WGS84→GCJ-02 坐标转换（已知坐标验证）
2. LocationTokenStore（签发/校验/过期/清理）
3. 4 个 API 端点（search/reverse/demo/demo select）
4. G-16 合规：API 响应不含坐标、坐标在 POST body 不在 URL、token 不透明
"""

from __future__ import annotations

import time
from typing import Any

from fastapi.testclient import TestClient

from app.main import create_app
from app.schemas.location import LocationSource
from app.services.location import (
    LocationTokenStore,
    load_demo_locations,
    wgs84_to_gcj02,
)

# ======================================================
# 1. WGS84 → GCJ-02 坐标转换
# ======================================================


class TestCoordinateConversion:
    def test_wgs84_to_gcj02_returns_tuple_of_floats(self) -> None:
        lat, lng = wgs84_to_gcj02(30.5, 114.4)
        assert isinstance(lat, float)
        assert isinstance(lng, float)

    def test_wgs84_to_gcj02_known_offset(self) -> None:
        """武汉光谷附近 WGS84 坐标 → GCJ-02 应有 ~0.001-0.01 度偏移。"""
        lat_wgs = 30.50
        lng_wgs = 114.40
        lat_gcj, lng_gcj = wgs84_to_gcj02(lat_wgs, lng_wgs)
        # 偏移量在合理范围内（中国境内约 0.001~0.01 度）
        assert 0.0005 < abs(lat_gcj - lat_wgs) < 0.02
        assert 0.0005 < abs(lng_gcj - lng_wgs) < 0.02
        # 方向：GCJ-02 通常比 WGS84 偏南偏西（中国境内）
        assert lat_gcj != lat_wgs
        assert lng_gcj != lng_wgs

    def test_wgs84_to_gcj02_origin_near_zero_offset(self) -> None:
        """原点附近偏移应极小（非中国境内，偏移公式结果接近 0）。"""
        lat_gcj, lng_gcj = wgs84_to_gcj02(0.0, 0.0)
        # 在赤道/本初子午线附近，偏移应非常小
        assert abs(lat_gcj) < 1.0
        assert abs(lng_gcj) < 1.0


# ======================================================
# 2. LocationTokenStore
# ======================================================


class TestLocationTokenStore:
    def test_issue_and_resolve(self) -> None:
        store = LocationTokenStore(ttl_seconds=60)
        token = store.issue(
            lat_gcj02=30.5,
            lng_gcj02=114.4,
            display_name="光谷广场",
            city_name="武汉市",
            district_name="洪山区",
            source=LocationSource.DEMO,
        )
        assert isinstance(token, str)
        assert len(token) == 32  # UUID hex

        ctx = store.resolve(token)
        assert ctx is not None
        assert ctx.display_name == "光谷广场"
        assert ctx.lat_gcj02 == 30.5
        assert ctx.lng_gcj02 == 114.4

    def test_resolve_invalid_token_returns_none(self) -> None:
        store = LocationTokenStore()
        assert store.resolve("nonexistent_token") is None

    def test_expired_token_returns_none(self) -> None:
        store = LocationTokenStore(ttl_seconds=0)
        token = store.issue(
            lat_gcj02=30.5,
            lng_gcj02=114.4,
            display_name="测试",
            city_name="武汉市",
            district_name="洪山区",
            source=LocationSource.MANUAL,
        )
        # TTL=0 意味着立即过期
        time.sleep(0.01)
        assert store.resolve(token) is None

    def test_cleanup_expired(self) -> None:
        store = LocationTokenStore(ttl_seconds=0)
        for i in range(3):
            store.issue(
                lat_gcj02=30.5,
                lng_gcj02=114.4,
                display_name=f"测试{i}",
                city_name="武汉市",
                district_name="洪山区",
                source=LocationSource.BROWSER,
            )
        time.sleep(0.01)
        cleaned = store.cleanup_expired()
        assert cleaned == 3

    def test_clear_all(self) -> None:
        store = LocationTokenStore(ttl_seconds=60)
        store.issue(
            lat_gcj02=30.5,
            lng_gcj02=114.4,
            display_name="测试",
            city_name="武汉市",
            district_name="洪山区",
            source=LocationSource.DEMO,
        )
        store.clear_all()
        assert len(store._store) == 0


# ======================================================
# 3. 演示地点加载
# ======================================================


class TestDemoLocations:
    def test_load_demo_locations(self) -> None:
        load_demo_locations.cache_clear()
        data = load_demo_locations()
        assert len(data.items) == 5
        assert len(data.records) == 5
        # 每条 item 不含坐标
        for item in data.items:
            assert not hasattr(item, "lat_gcj02")
            assert not hasattr(item, "lng_gcj02")
        # 每条 record 含坐标
        for rec in data.records:
            assert rec.lat_gcj02 is not None
            assert rec.lng_gcj02 is not None

    def test_unique_codes(self) -> None:
        load_demo_locations.cache_clear()
        data = load_demo_locations()
        codes = [r.code for r in data.records]
        assert len(codes) == len(set(codes))


# ======================================================
# 4. API 端点测试
# ======================================================

_client = TestClient(create_app())


def _post(url: str, json: dict[str, Any] | None = None):
    return _client.post(url, json=json)


def _get(url: str):
    return _client.get(url)


class TestLocationSearchAPI:
    def test_search_with_keyword(self) -> None:
        resp = _post("/api/v1/locations/search", {"keyword": "光谷"})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert len(body["data"]) >= 1
        item = body["data"][0]
        assert "location_token" in item
        assert "display_name" in item
        assert "city_name" in item
        assert "district_name" in item
        # G-16：响应不含坐标
        assert "lat" not in item
        assert "lng" not in item
        assert "latitude" not in item
        assert "longitude" not in item
        assert "lat_gcj02" not in item
        assert "lng_gcj02" not in item

    def test_search_no_result(self) -> None:
        resp = _post("/api/v1/locations/search", {"keyword": "不存在的地点xyz"})
        assert resp.status_code == 200
        assert resp.json()["data"] == []

    def test_search_empty_keyword_422(self) -> None:
        resp = _post("/api/v1/locations/search", {"keyword": ""})
        assert resp.status_code == 422

    def test_search_missing_keyword_422(self) -> None:
        resp = _post("/api/v1/locations/search", {})
        assert resp.status_code == 422

    def test_search_extra_field_forbidden_422(self) -> None:
        resp = _post("/api/v1/locations/search", {"keyword": "光谷", "extra": "bad"})
        assert resp.status_code == 422


class TestLocationReverseAPI:
    def test_reverse_geocode(self) -> None:
        resp = _post(
            "/api/v1/locations/reverse",
            {"latitude": 30.50, "longitude": 114.40},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        data = body["data"]
        assert "location_token" in data
        assert "display_name" in data
        assert "city_name" in data
        assert "district_name" in data
        # G-16：响应不含坐标
        assert "lat" not in data
        assert "lng" not in data
        assert "latitude" not in data
        assert "longitude" not in data

    def test_reverse_invalid_lat_422(self) -> None:
        resp = _post("/api/v1/locations/reverse", {"latitude": 999.0, "longitude": 114.0})
        assert resp.status_code == 422

    def test_reverse_invalid_lng_422(self) -> None:
        resp = _post("/api/v1/locations/reverse", {"latitude": 30.0, "longitude": 999.0})
        assert resp.status_code == 422

    def test_reverse_missing_field_422(self) -> None:
        resp = _post("/api/v1/locations/reverse", {"latitude": 30.0})
        assert resp.status_code == 422

    def test_reverse_extra_field_forbidden_422(self) -> None:
        resp = _post(
            "/api/v1/locations/reverse",
            {"latitude": 30.0, "longitude": 114.0, "extra": "bad"},
        )
        assert resp.status_code == 422


class TestLocationDemoAPI:
    def test_list_demo_locations(self) -> None:
        resp = _get("/api/v1/locations/demo")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert len(body["data"]) == 5
        for item in body["data"]:
            assert "code" in item
            assert "display_name" in item
            assert "city_name" in item
            assert "district_name" in item
            # G-16：列表不含坐标、不含 token
            assert "lat_gcj02" not in item
            assert "lng_gcj02" not in item
            assert "location_token" not in item

    def test_select_demo_location(self) -> None:
        resp = _client.post("/api/v1/locations/demo/wuhan_optics_valley/select")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        data = body["data"]
        assert "location_token" in data
        assert data["display_name"] == "光谷广场"
        assert data["city_name"] == "武汉市"
        assert data["district_name"] == "洪山区"
        # G-16：响应不含坐标
        assert "lat_gcj02" not in data
        assert "lng_gcj02" not in data

    def test_select_demo_location_not_found_404(self) -> None:
        resp = _client.post("/api/v1/locations/demo/nonexistent_code/select")
        assert resp.status_code == 404
        body = resp.json()
        assert body["error"]["code"] == "NOT_FOUND"


# ======================================================
# 5. G-16 合规综合验证
# ======================================================


class TestG16Compliance:
    """G-16：精确坐标不写入 URL、业务历史和普通日志；拒绝授权仍可使用手动/演示路径。"""

    def test_all_location_endpoints_use_post_not_get(self) -> None:
        """坐标和搜索关键词在 POST body，不在 URL（G-16）。"""
        # search 用 POST
        resp = _post("/api/v1/locations/search", {"keyword": "光谷"})
        assert resp.status_code == 200
        # reverse 用 POST（坐标在 body）
        resp = _post("/api/v1/locations/reverse", {"latitude": 30.5, "longitude": 114.4})
        assert resp.status_code == 200

    def test_token_is_opaque_hex(self) -> None:
        """location_token 是不透明 UUID hex，不含坐标信息。"""
        resp = _client.post("/api/v1/locations/demo/wuhan_optics_valley/select")
        assert resp.status_code == 200
        token = resp.json()["data"]["location_token"]
        # UUID hex：32 个十六进制字符
        assert len(token) == 32
        assert all(c in "0123456789abcdef" for c in token)

    def test_reject_browser_still_works_with_manual_and_demo(self) -> None:
        """拒绝浏览器定位 → 手动搜索和演示地点仍可用。"""
        # 手动搜索
        resp = _post("/api/v1/locations/search", {"keyword": "光谷"})
        assert resp.status_code == 200
        assert len(resp.json()["data"]) >= 1
        # 演示地点
        resp = _get("/api/v1/locations/demo")
        assert resp.status_code == 200
        assert len(resp.json()["data"]) == 5
        # 选择演示地点
        resp = _client.post("/api/v1/locations/demo/wuhan_optics_valley/select")
        assert resp.status_code == 200

    def test_no_coordinates_in_any_api_response(self) -> None:
        """所有 location API 响应都不含坐标字段。"""
        # search
        resp = _post("/api/v1/locations/search", {"keyword": "光谷"})
        for item in resp.json()["data"]:
            assert not any(k in item for k in ("lat", "lng", "latitude", "longitude"))
        # reverse
        resp = _post("/api/v1/locations/reverse", {"latitude": 30.5, "longitude": 114.4})
        data = resp.json()["data"]
        assert not any(k in data for k in ("lat", "lng", "latitude", "longitude"))
        # demo list
        resp = _get("/api/v1/locations/demo")
        for item in resp.json()["data"]:
            assert not any(k in item for k in ("lat", "lng", "latitude", "longitude"))
        # demo select
        resp = _client.post("/api/v1/locations/demo/wuhan_optics_valley/select")
        data = resp.json()["data"]
        assert not any(k in data for k in ("lat", "lng", "latitude", "longitude"))
