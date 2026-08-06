"""P3-02 Mock POIProvider：固定排序、零结果、超时、错误四态。

对齐 05_系统架构设计.md §11.2/§11.3 和 07_API接口设计.md §23。

四态（P3-02 验收硬性要求）：
- normal：返回固定排序的 5 条 mock 商户（按 distance_m 升序）
- empty：返回空列表 + 恢复建议（不视为错误）
- slow：模拟超时（默认 2 秒延迟，可通过 SLOW_DELAY_MS 调整）
- error：抛出 ServiceUnavailableError

mock 数据要点（G-10 / 14_设计审计 §6.3）：
- 商户名称使用"示例"前缀，明确标识为 mock 数据，避免与真实商户混淆。
- 距离按 food_code 哈希确定生成，保证同一 food_code 多次调用结果一致。
- 不包含价格、营业时间等无法验证的信息。
- provider_mode = "mock" 必须在 meta 中返回。

P3-04 接入高德 Live 时新增 AmapPOIProvider，实现同一接口。
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
from typing import Literal, Protocol

from app.core.exceptions import SERVICE_UNAVAILABLE, AppError
from app.schemas.location import LocationContext
from app.schemas.poi import (
    POIItem,
    POIProviderName,
    RestaurantSearchMeta,
    RestaurantSearchResponseV1,
    RestaurantSearchSuggestion,
)

logger = logging.getLogger("app")

MockMode = Literal["normal", "empty", "slow", "error"]

DEFAULT_SLOW_DELAY_MS = 2000


def _default_mock_mode() -> MockMode:
    """从环境变量读取默认 mock 模式。"""
    raw = os.environ.get("POI_MOCK_MODE", "normal").strip().lower()
    if raw in ("normal", "empty", "slow", "error"):
        return raw  # type: ignore[return-value]
    return "normal"


def _slow_delay_ms() -> int:
    raw = os.environ.get("POI_MOCK_SLOW_MS", str(DEFAULT_SLOW_DELAY_MS))
    try:
        val = int(raw)
        return val if val > 0 else DEFAULT_SLOW_DELAY_MS
    except ValueError:
        return DEFAULT_SLOW_DELAY_MS


# ---- mock 商户名称池（按 food_code 哈希选择，保证确定性） ----

_MOCK_NAME_SUFFIXES: tuple[str, ...] = (
    "（示范店）",
    "（光谷店）",
    "（步行街店）",
    "（洪山店）",
    "（江汉路店）",
)

_MOCK_ADDRESSES: tuple[str, ...] = (
    "光谷步行街示例地址",
    "珞喻路示例路段",
    "民族大道示例路段",
    "珞狮路示例路段",
    "江汉路示例路段",
)

_MOCK_CATEGORIES: tuple[str, ...] = (
    "餐饮服务;中餐厅",
    "餐饮服务;快餐",
    "餐饮服务;小吃",
    "餐饮服务;饮品店",
    "餐饮服务;其他",
)


def _hash_food_code(food_code: str) -> int:
    """对 food_code 做稳定哈希，用于确定性选择 mock 数据。"""
    h = hashlib.sha256(food_code.encode("utf-8")).hexdigest()
    return int(h[:8], 16)


def _build_mock_items(
    food_code: str,
    ctx: LocationContext,
    radius_m: int,
    limit: int,
) -> list[POIItem]:
    """根据 food_code + 地点上下文生成确定性 mock 商户列表。

    距离按 food_code 哈希分布，全部落在 radius_m 内（除非 radius_m 过小）。
    """
    seed = _hash_food_code(food_code)
    items: list[POIItem] = []
    for i in range(limit):
        # 距离：从 100m 起，按 seed 递增，封顶 radius_m
        base = 100 + (seed >> (i * 2) & 0x3FF)  # 0~1023 米偏移
        distance = min(base * (i + 1), radius_m)
        # 防止 distance = 0
        distance = max(distance, 50)
        name_suffix = _MOCK_NAME_SUFFIXES[i % len(_MOCK_NAME_SUFFIXES)]
        address = _MOCK_ADDRESSES[(seed + i) % len(_MOCK_ADDRESSES)]
        category = _MOCK_CATEGORIES[(seed + i) % len(_MOCK_CATEGORIES)]
        items.append(
            POIItem(
                provider=POIProviderName.MOCK,
                poi_id=f"mock_{food_code}_{i + 1:02d}",
                name=f"示例{food_code}{name_suffix}",
                category_text=category,
                distance_m=distance,
                address=address,
                city_name=ctx.city_name,
                district_name=ctx.district_name,
                map_uri=f"https://example.com/map?poi=mock_{food_code}_{i + 1:02d}",
            )
        )
    # 按 distance_m 升序（最近匹配优先）
    items.sort(key=lambda x: x.distance_m)
    return items


def _build_empty_suggestions(radius_m: int) -> list[RestaurantSearchSuggestion]:
    """空结果时的恢复建议。"""
    return [
        RestaurantSearchSuggestion(
            action="expand_radius",
            radius_m=min(radius_m * 2, 50_000),
        ),
        RestaurantSearchSuggestion(action="select_other_food"),
    ]


class POIProviderProtocol(Protocol):
    """POIProvider 统一接口（P3-04 AmapPOIProvider 也实现此接口）。"""

    async def search_nearby_restaurants(
        self,
        *,
        food_code: str,
        location_context: LocationContext,
        radius_m: int,
        limit: int,
        cursor: str | None,
        mock_mode: MockMode | None = None,
    ) -> RestaurantSearchResponseV1: ...


class MockPOIProvider:
    """Mock POIProvider：四态可重复触发。

    模式优先级：
    1. 请求体 mock_mode 参数（最高，用于 UI 测试）
    2. 环境变量 POI_MOCK_MODE（默认部署级控制）
    """

    def __init__(self, default_mode: MockMode | None = None) -> None:
        self._default_mode: MockMode = default_mode or _default_mock_mode()

    @property
    def provider_mode(self) -> Literal["mock"]:
        return "mock"

    @property
    def default_mode(self) -> MockMode:
        return self._default_mode

    def set_default_mode(self, mode: MockMode) -> None:
        """测试用：运行时修改默认模式。"""
        self._default_mode = mode

    async def search_nearby_restaurants(
        self,
        *,
        food_code: str,
        location_context: LocationContext,
        radius_m: int,
        limit: int,
        cursor: str | None,
        mock_mode: MockMode | None = None,
    ) -> RestaurantSearchResponseV1:
        mode: MockMode = mock_mode or self._default_mode

        if mode == "slow":
            delay_ms = _slow_delay_ms()
            logger.info("poi_mock_slow mode=slow delay_ms=%d food_code=%s", delay_ms, food_code)
            await asyncio.sleep(delay_ms / 1000.0)

        if mode == "error":
            logger.info("poi_mock_error mode=error food_code=%s", food_code)
            raise AppError(
                code=SERVICE_UNAVAILABLE,
                message="Mock POI 服务模拟不可用（测试模式）",
                status_code=503,
                details={"mock_mode": "error", "hint": "切换 mock_mode=normal 恢复"},
            )

        if mode == "empty":
            logger.info("poi_mock_empty mode=empty food_code=%s", food_code)
            return RestaurantSearchResponseV1(
                data=[],
                meta=RestaurantSearchMeta(
                    next_cursor=None,
                    cached=False,
                    provider_mode="mock",
                ),
                suggestions=_build_empty_suggestions(radius_m),
            )

        # normal
        logger.info("poi_mock_normal mode=normal food_code=%s radius_m=%d", food_code, radius_m)
        items = _build_mock_items(
            food_code=food_code,
            ctx=location_context,
            radius_m=radius_m,
            limit=limit,
        )
        return RestaurantSearchResponseV1(
            data=items,
            meta=RestaurantSearchMeta(
                next_cursor=None,
                cached=False,
                provider_mode="mock",
            ),
            suggestions=[],
        )


# ---- 全局单例 ----

_provider: MockPOIProvider | None = None


def get_poi_provider() -> MockPOIProvider:
    """获取全局 POIProvider 单例。

    P3-02 阶段只返回 MockPOIProvider；
    P3-04 接入高德时根据 POI_PROVIDER 环境变量切换。
    """
    global _provider
    if _provider is None:
        _provider = MockPOIProvider()
    return _provider


def reset_poi_provider() -> None:
    """测试用：重置单例（让下次 get_poi_provider 重新读取环境变量）。"""
    global _provider
    _provider = None


__all__ = [
    "DEFAULT_SLOW_DELAY_MS",
    "MockMode",
    "MockPOIProvider",
    "POIProviderProtocol",
    "get_poi_provider",
    "reset_poi_provider",
]
