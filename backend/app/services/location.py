"""P3-01 Location service：坐标转换 + location_token 管理 + 演示地点加载。

G-16 合规：
- WGS84→GCJ-02 转换在服务端完成，转换后坐标只存内存（LocationTokenStore）。
- location_token 是 UUID hex，不透明，不含坐标。
- Token 短 TTL（默认 30 分钟），过期自动失效。
- 日志只记录 source/display_name，绝不记录坐标（RedactFilter 兜底）。
"""

from __future__ import annotations

import json
import logging
import math
import time
import uuid
from functools import lru_cache
from pathlib import Path
from typing import NamedTuple

from app.schemas.location import (
    DemoLocationItem,
    DemoLocationRecord,
    LocationContext,
    LocationSource,
)

logger = logging.getLogger("app")

# ---- WGS84 → GCJ-02 坐标转换 ----
# 标准算法，常量来自国家测绘局 GCJ-02 规范。

_PI = 3.1415926535897932384626
_A = 6378245.0  # 半长轴
_EE = 0.00669342162296594323  # 偏心率平方


def _transform_lat(x: float, y: float) -> float:
    ret = (
        -100.0
        + 2.0 * x
        + 3.0 * y
        + 0.2 * y * y
        + 0.1 * x * y
        + 0.2 * math.sqrt(abs(x))
    )
    ret += (20.0 * math.sin(6.0 * x * _PI) + 20.0 * math.sin(2.0 * x * _PI)) * 2.0 / 3.0
    ret += (20.0 * math.sin(y * _PI) + 40.0 * math.sin(y / 3.0 * _PI)) * 2.0 / 3.0
    ret += (
        160.0 * math.sin(y / 12.0 * _PI) + 320 * math.sin(y * _PI / 30.0)
    ) * 2.0 / 3.0
    return ret


def _transform_lng(x: float, y: float) -> float:
    ret = (
        300.0
        + x
        + 2.0 * y
        + 0.1 * x * x
        + 0.1 * x * y
        + 0.1 * math.sqrt(abs(x))
    )
    ret += (20.0 * math.sin(6.0 * x * _PI) + 20.0 * math.sin(2.0 * x * _PI)) * 2.0 / 3.0
    ret += (20.0 * math.sin(x * _PI) + 40.0 * math.sin(x / 3.0 * _PI)) * 2.0 / 3.0
    ret += (
        150.0 * math.sin(x / 12.0 * _PI) + 300.0 * math.sin(x / 30.0 * _PI)
    ) * 2.0 / 3.0
    return ret


def wgs84_to_gcj02(lat: float, lng: float) -> tuple[float, float]:
    """将 WGS84 坐标转换为 GCJ-02 坐标（火星坐标系）。

    用于浏览器定位入口：浏览器 navigator.geolocation 返回 WGS84，
    高德 POI 搜索需要 GCJ-02。
    """
    dlat = _transform_lat(lng - 105.0, lat - 35.0)
    dlng = _transform_lng(lng - 105.0, lat - 35.0)
    radlat = lat / 180.0 * _PI
    magic = math.sin(radlat)
    magic = 1 - _EE * magic * magic
    sqrtmagic = math.sqrt(magic)
    dlat = (dlat * 180.0) / ((_A * (1 - _EE)) / (magic * sqrtmagic) * _PI)
    dlng = (dlng * 180.0) / (_A / sqrtmagic * math.cos(radlat) * _PI)
    return lat + dlat, lng + dlng


# ---- Location Token Store（内存，短 TTL） ----

DEFAULT_TOKEN_TTL_SECONDS = 30 * 60  # 30 分钟


class LocationTokenStore:
    """内存中的 location_token → LocationContext 映射。

    G-16：短 TTL、绑定会话、不可篡改。Token 过期后自动失效。
    线程安全：单线程 async FastAPI 不需要锁；如需多进程可换 Redis（当前不引入）。
    """

    def __init__(self, ttl_seconds: int = DEFAULT_TOKEN_TTL_SECONDS) -> None:
        self._store: dict[str, LocationContext] = {}
        self._ttl = ttl_seconds

    def issue(
        self,
        *,
        lat_gcj02: float,
        lng_gcj02: float,
        display_name: str,
        city_name: str,
        district_name: str,
        source: LocationSource,
    ) -> str:
        """签发新 token，返回不透明 hex 字符串。"""
        token = uuid.uuid4().hex
        ctx = LocationContext(
            lat_gcj02=lat_gcj02,
            lng_gcj02=lng_gcj02,
            display_name=display_name,
            city_name=city_name,
            district_name=district_name,
            source=source,
            expires_at=time.time() + self._ttl,
        )
        self._store[token] = ctx
        # G-16：日志只记 source + display_name，不记坐标
        logger.info(
            "location_token_issued source=%s display_name=%s", source.value, display_name
        )
        return token

    def resolve(self, token: str) -> LocationContext | None:
        """校验 token 并返回 LocationContext；过期或不存在返回 None。"""
        ctx = self._store.get(token)
        if ctx is None:
            return None
        if time.time() > ctx.expires_at:
            self._store.pop(token, None)
            logger.info("location_token_expired source=%s", ctx.source.value)
            return None
        return ctx

    def cleanup_expired(self) -> int:
        """清理所有过期 token，返回清理数量。"""
        now = time.time()
        expired = [t for t, c in self._store.items() if now > c.expires_at]
        for t in expired:
            self._store.pop(t, None)
        return len(expired)

    def clear_all(self) -> None:
        """清空所有 token（测试用）。"""
        self._store.clear()


# 全局单例（模块级）
_token_store = LocationTokenStore()


def get_token_store() -> LocationTokenStore:
    return _token_store


# ---- 演示地点加载 ----

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"


class DemoLocationData(NamedTuple):
    items: list[DemoLocationItem]  # 不含坐标，给 API 响应用
    records: list[DemoLocationRecord]  # 含坐标，给内部 token 签发用


@lru_cache(maxsize=1)
def load_demo_locations() -> DemoLocationData:
    """从 demo_locations.json 加载演示地点。"""
    path = _DATA_DIR / "demo_locations.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    records = [DemoLocationRecord(**r) for r in raw["locations"]]
    # 唯一 code 校验
    codes = [r.code for r in records]
    if len(codes) != len(set(codes)):
        raise ValueError("demo_locations.json 有重复 code")
    items = [
        DemoLocationItem(
            code=r.code,
            display_name=r.display_name,
            city_name=r.city_name,
            district_name=r.district_name,
        )
        for r in records
    ]
    return DemoLocationData(items=items, records=records)


def find_demo_record(code: str) -> DemoLocationRecord | None:
    """按 code 查找演示地点记录。"""
    data = load_demo_locations()
    for r in data.records:
        if r.code == code:
            return r
    return None


def search_demo_locations(keyword: str, limit: int = 5) -> list[DemoLocationRecord]:
    """关键词搜索演示地点（P3-01 mock 手动搜索：本地匹配 demo 数据）。"""
    data = load_demo_locations()
    kw = keyword.strip().lower()
    if not kw:
        return data.records[:limit]
    matched = [
        r
        for r in data.records
        if kw in r.display_name.lower()
        or kw in r.city_name.lower()
        or kw in r.district_name.lower()
        or kw in r.code.lower()
    ]
    return matched[:limit]


def reverse_geocode_mock(lat_wgs84: float, lng_wgs84: float) -> DemoLocationRecord:
    """P3-01 mock 反向地理编码：把 WGS84 坐标就近匹配到最近的演示地点。

    P3-04 接入高德 Live 时替换为真实 reverse geocode API。
    """
    lat_gcj, lng_gcj = wgs84_to_gcj02(lat_wgs84, lng_wgs84)
    data = load_demo_locations()
    # 找最近的演示地点（简单欧氏距离，足够 mock 用）
    best = min(
        data.records,
        key=lambda r: (r.lat_gcj02 - lat_gcj) ** 2 + (r.lng_gcj02 - lng_gcj) ** 2,
    )
    return best
