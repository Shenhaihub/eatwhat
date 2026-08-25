"""P3-02/P3-04 POIProvider 工厂与实现。

统一接口（POIProviderProtocol）：
- MockPOIProvider：四态（normal/empty/slow/error），用于 UI 验证与开发
- AmapPOIProvider：高德 Web 服务 API（周边搜索 Place Around Search）

provider 模式（Settings.poi_provider）：
- mock：强制使用 MockPOIProvider
- live：强制使用 AmapPOIProvider（AMAP_API_KEY 为空则启动报错）
- auto：优先 Amap，无 key 或 Live 连续失败自动降级到 Mock（运行时）

关键约束：
- G-16：Live provider 内部持有的原始响应坐标，绝不写入响应/日志
- G-10：商户结果称"最近匹配"，不声称"最好/最推荐"
- 高德密钥只在 Settings.amap_api_key（后端环境变量）读取，不出前端
- 所有网络调用用 httpx.AsyncClient（Pydantic 工程标准），带超时+重试

对齐：05_系统架构设计.md §11.2/§11.3，07_API接口设计.md §23
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import time
from typing import Any, Final, Literal, Protocol

from app.core.config import get_settings
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

type MockMode = Literal["normal", "empty", "slow", "error"]
type ProviderModeValue = Literal["mock", "live", "auto"]

DEFAULT_SLOW_DELAY_MS = 2000
AMAP_AROUND_URL: Final[str] = "https://restapi.amap.com/v3/place/around"
AMAP_KEYWORD_FALLBACK: Final[str] = "餐饮"
_AMAP_MAX_LIMIT = 25  # 高德 around 上限 25
_DEFAULT_REST_HTTP_TIMEOUT_S = 6.0
_DEFAULT_RETRY_TIMES = 2
_DEGRADE_FAILURE_THRESHOLD = 5  # auto 模式连续失败 N 次，切到 Mock 直到窗口恢复
_DEGRADE_WINDOW_S = 60  # auto 模式恢复窗口（秒）


def _default_mock_mode() -> MockMode:
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


# ============================================================
# Mock 数据（与 P3-02 保持兼容，未改动）
# ============================================================

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
    h = hashlib.sha256(food_code.encode("utf-8")).hexdigest()
    return int(h[:8], 16)


def _build_mock_items(
    food_code: str,
    ctx: LocationContext,
    radius_m: int,
    limit: int,
) -> list[POIItem]:
    seed = _hash_food_code(food_code)
    items: list[POIItem] = []
    for i in range(limit):
        base = 100 + (seed >> (i * 2) & 0x3FF)
        distance = min(base * (i + 1), radius_m)
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
    items.sort(key=lambda x: x.distance_m)
    return items


def _build_empty_suggestions(radius_m: int) -> list[RestaurantSearchSuggestion]:
    return [
        RestaurantSearchSuggestion(
            action="expand_radius",
            radius_m=min(radius_m * 2, 50_000),
        ),
        RestaurantSearchSuggestion(action="select_other_food"),
    ]


# ============================================================
# 统一协议
# ============================================================


class POIProviderProtocol(Protocol):
    """POIProvider 统一接口。AmapPOIProvider 与 MockPOIProvider 均实现此接口。"""

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


# ============================================================
# MockPOIProvider（与 P3-02 完全兼容，未改动四态语义）
# ============================================================


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


# ============================================================
# AmapPOIProvider（P3-04 高德 Live 接入）
# ============================================================

# 高德返回的 poi 片段（只取需要的字段，不做全量模型，避免意外泄漏）
type _AmapPoiRaw = dict[str, Any]


def _category_to_label(poi: _AmapPoiRaw) -> str:
    """高德 `type` 形如"餐饮服务;中餐厅;川菜"，归一化为前两段。"""
    typ = str(poi.get("type") or "餐饮服务;其他")
    parts = [p for p in typ.split("|")[0].split(";") if p]
    if len(parts) >= 2:
        return f"{parts[0]};{parts[1]}"
    return parts[0] if parts else "餐饮服务;其他"


def _poi_distance(poi: _AmapPoiRaw, fallback: int) -> int:
    """高德 around 接口响应里 `distance` 是字符串米数；取不到用 fallback。"""
    raw = poi.get("distance")
    if raw is None:
        return fallback
    try:
        v = int(str(raw))
        return max(0, min(v, 100_000))
    except ValueError:
        return fallback


def _region_parts(poi: _AmapPoiRaw, ctx: LocationContext) -> tuple[str, str, str]:
    """从高德响应取 (province, city_name, district_name)，缺失用 ctx 兜底（G-16 泄漏防御）。"""
    city = str(poi.get("cityname") or ctx.city_name or "").strip()
    district = str(poi.get("adname") or ctx.district_name or "").strip()
    # pname 是省份，可能为空（直辖市高德返回 cityname=北京市）
    return city or ctx.city_name, district or ctx.district_name, str(poi.get("pname") or "")


def _amap_poi_to_item(
    poi: _AmapPoiRaw,
    ctx: LocationContext,
    fallback_distance: int,
) -> POIItem | None:
    """把一条高德 poi 映射成严格的 POIItem。字段缺失时跳过。"""
    try:
        poi_id = str(poi.get("id") or "").strip()
        name = str(poi.get("name") or "").strip()
        if not poi_id or not name:
            return None
        # location 格式："lng,lat"（WGS-84 系之外通常是 GCJ-02），不用作响应，只检查存在性
        # 地址：高德 `address` 可能缺失（地下商铺等），用 `pname+cityname+adname` 兜底
        addr_raw = str(poi.get("address") or "").strip()
        city, district, _pname = _region_parts(poi, ctx)
        if not addr_raw:
            addr_raw = f"{city}{district}"
        # 截断防止超长（POIItem max_length=128）
        addr = (addr_raw[:128] if addr_raw else city) or "地址不详"
        map_uri = f"https://uri.amap.com/marker?position={poi.get('location','')}&name={_quote_uri(name)}"
        return POIItem(
            provider=POIProviderName.AMAP,
            poi_id=poi_id[:64],
            name=name[:64],
            category_text=_category_to_label(poi)[:64],
            distance_m=_poi_distance(poi, fallback_distance),
            address=addr[:128],
            city_name=city[:32] or ctx.city_name,
            district_name=district[:32] or ctx.district_name,
            map_uri=map_uri[:256],
        )
    except Exception:
        logger.exception("amap_poi_map_failed poi_id=%s", poi.get("id"))
        return None


def _quote_uri(s: str) -> str:
    """轻量 URI 组件转义（避免引入 urllib.parse 依赖导致热路径开销）。"""
    safe = ""
    for ch in s:
        if ch.isalnum() or ch in "-_./~":
            safe += ch
        else:
            safe += f"%{ord(ch):02X}"
    return safe


class AmapPOIProvider:
    """高德 Web 服务 POI 周边搜索（P3-04）。

    外部依赖：
    - httpx 异步 HTTP（必须已安装为 dev 依赖，测试中通过 monkeypatch mock）
    - Settings.amap_api_key：从环境变量 AMAP_API_KEY 读取，空字符串视为未配置

    行为：
    - 周边搜索：`GET /v3/place/around?location=lng,lat&radius=&keywords=&key=&offset=&page=`
    - keywords 优先用 food_code 对应中文（通过 food_dictionary 可选查询），否则"餐饮"
    - 按 `distance_m` 升序裁剪到 limit 条（高德 around 默认就是按距离）
    - 失败重试 N 次（默认 2）+ 整体超时；全部失败抛 AppError(503)
    - AutoFailoverWrapper 会在外层捕获 503 做降级，这里不主动降级

    边界：
    - 密钥绝不写入日志（通过 Settings.secret_values 在日志层统一脱敏）
    - 原始响应里的 `location` 精确坐标不会出现在 POIItem/响应/普通日志
    """

    def __init__(
        self,
        *,
        api_key: str,
        http_timeout_s: float = _DEFAULT_REST_HTTP_TIMEOUT_S,
        max_retries: int = _DEFAULT_RETRY_TIMES,
        base_url: str = AMAP_AROUND_URL,
    ) -> None:
        if not api_key:
            # Settings 里 amap_api_key="" 表示未配置；工厂层会拦截到 Mock
            raise ValueError("AmapPOIProvider: api_key 不能为空（使用 poi_provider=mock 或 配置 AMAP_API_KEY）")
        self._api_key = api_key
        self._http_timeout_s = http_timeout_s
        self._max_retries = max_retries
        self._base_url = base_url

    @property
    def provider_mode(self) -> Literal["live"]:
        return "live"

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
        # 1) 组装请求参数
        #    高德 location 格式："经度,纬度"（GCJ-02），P3-01 已转换并存入 LocationContext
        location_param = f"{location_context.lng_gcj02:.6f},{location_context.lat_gcj02:.6f}"
        keywords = self._resolve_keyword(food_code)
        # 分页：高德 page 从 1 起；cursor 暂未使用，未来可传 page=cursor
        page = int(cursor) if cursor and cursor.isdigit() else 1
        offset = min(max(limit, 1), _AMAP_MAX_LIMIT)

        params = {
            "key": self._api_key,
            "location": location_param,
            "radius": str(radius_m),
            "keywords": keywords,
            "offset": str(offset),
            "page": str(page),
            "extensions": "base",  # base 足够，不拿详情
            "output": "JSON",
        }

        # 2) 带重试的 HTTP 调用
        raw = await self._get_with_retry(params)
        if raw is None:
            raise AppError(
                code=SERVICE_UNAVAILABLE,
                message="高德 POI 服务不可用（多次重试失败）",
                status_code=503,
                details={"upstream": "amap", "retries": self._max_retries},
            )

        # 3) 响应健壮性校验
        status_code = raw.get("status")
        infocode = str(raw.get("infocode") or "")
        if status_code not in (1, "1"):
            # 高德常见错误：配额超限 infocode=10004/10005，签名错误=10001 等
            logger.warning(
                "amap_upstream_error status=%s infocode=%s info=%s",
                status_code, infocode, str(raw.get("info"))[:200],
            )
            raise AppError(
                code=SERVICE_UNAVAILABLE,
                message=f"高德 POI 服务返回错误（infocode={infocode}）",
                status_code=503,
                details={"upstream": "amap", "infocode": infocode},
            )

        # 4) 字段归一化
        pois_raw: list[_AmapPoiRaw] = raw.get("pois") or []
        items: list[POIItem] = []
        for idx, raw_poi in enumerate(pois_raw):
            # 距离兜底：第 i 条按 radius/limit 估算，保证单调递增
            fallback = int(min(radius_m, (idx + 1) * max(radius_m // max(limit, 1), 50)))
            item = _amap_poi_to_item(raw_poi, location_context, fallback)
            if item is not None:
                items.append(item)
            if len(items) >= limit:
                break

        # 5) 结果为空 → 返回建议
        if not items:
            return RestaurantSearchResponseV1(
                data=[],
                meta=RestaurantSearchMeta(
                    next_cursor=None,
                    cached=False,
                    provider_mode="live",
                ),
                suggestions=_build_empty_suggestions(radius_m),
            )

        # 6) 分页：如果本次返回 >= offset（还有下一页），生成 cursor
        next_cursor: str | None = None
        if len(pois_raw) >= offset and page * offset < 200:  # 高德 around 最多 1000 条；保守限制
            next_cursor = str(page + 1)

        return RestaurantSearchResponseV1(
            data=items,
            meta=RestaurantSearchMeta(
                next_cursor=next_cursor,
                cached=False,
                provider_mode="live",
            ),
            suggestions=[],
        )

    # -------- 内部辅助 --------

    def _resolve_keyword(self, food_code: str) -> str:
        """把 food_code 映射成高德搜索关键词；找不到就用通用词。

        直接用 food_dictionary 中的 display_name_zh 作为关键词（按需加载，避免循环 import）。
        """
        try:
            from app.repositories.food_dictionary import (
                DEFAULT_DICTIONARY_VERSION,
                get_food_dictionary_repository,
            )

            repo = get_food_dictionary_repository(DEFAULT_DICTIONARY_VERSION)
            entry = repo.get(food_code)
            if entry and entry.display_name_zh:
                return entry.display_name_zh
        except Exception:
            logger.exception("amap_keyword_dict_lookup_failed food_code=%s", food_code)
        return AMAP_KEYWORD_FALLBACK

    async def _get_with_retry(self, params: dict[str, str]) -> dict[str, Any] | None:
        """带简单重试的 HTTP GET；全部失败返回 None。使用 httpx（运行时导入，允许测试 monkeypatch）。"""
        import httpx  # 延迟导入，方便测试替换

        last_exc: Exception | None = None
        for attempt in range(self._max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=self._http_timeout_s) as client:
                    resp = await client.get(self._base_url, params=params)
                    resp.raise_for_status()
                    data = resp.json()
                    if isinstance(data, dict):
                        return data
                    logger.error("amap_non_dict_response type=%s", type(data).__name__)
                    return None
            except Exception as exc:
                last_exc = exc
                logger.warning(
                    "amap_http_attempt_failed attempt=%d/%d err=%s",
                    attempt + 1,
                    self._max_retries + 1,
                    str(exc)[:200],
                )
                if attempt < self._max_retries:
                    await asyncio.sleep(0.05 * (attempt + 1))  # 短退避
        logger.error("amap_http_all_retries_failed last=%s", str(last_exc)[:300])
        return None


# ============================================================
# 带缓存 + 自动降级的 Wrapper（P3-04 auto/live 通用）
# ============================================================


class _CacheEntry:
    __slots__ = ("expires_at", "value")

    def __init__(self, value: RestaurantSearchResponseV1, ttl_s: int) -> None:
        self.value = value
        self.expires_at = time.monotonic() + ttl_s


class AutoFailoverPOIProvider:
    """组合层：Live + 缓存 + 失败窗口降级 Mock（auto 模式使用，live 模式也可独立注入）。

    缓存键：(food_code, location_token, radius_m, limit, provider_setting)
    - location_token 代替坐标（G-16），同时让"同地点不同会话"缓存不交叉
    - TTL 由 Settings.poi_cache_ttl_seconds 控制（默认 1200s = 20min）

    自动降级窗口：
    - auto 模式下 Live 连续失败 N 次 → 切到 Mock 直到窗口（秒）过去
    - 窗口到期后再试一次 Live；成功则清零计数，否则继续 Mock
    - 日志只记"降级触发/恢复"，不泄漏密钥/坐标（符合 G-16 + 脱敏中间件）
    """

    def __init__(
        self,
        *,
        live: AmapPOIProvider | None,
        mock: MockPOIProvider,
        mode: ProviderModeValue,
        cache_ttl_s: int,
    ) -> None:
        self._live = live
        self._mock = mock
        self._mode: ProviderModeValue = mode
        self._cache_ttl_s = max(0, cache_ttl_s)
        self._cache: dict[tuple[str, str, int, int, str, str], _CacheEntry] = {}
        # 降级状态
        self._consecutive_failures = 0
        self._degrade_until = 0.0

    @property
    def provider_mode(self) -> ProviderModeValue:
        return self._mode

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
        # 1) mock 模式强制走 Mock；live 模式的 mock_mode 参数会被 Amap 忽略
        if self._mode == "mock":
            return await self._mock.search_nearby_restaurants(
                food_code=food_code,
                location_context=location_context,
                radius_m=radius_m,
                limit=limit,
                cursor=cursor,
                mock_mode=mock_mode,
            )

        # 2) auto 模式：处于降级窗口 → 直接 Mock（mock_mode 保留给 UI 测试）
        if self._mode == "auto" and time.monotonic() < self._degrade_until:
            logger.info("poi_auto_degrade_hit consecutive_failures=%d", self._consecutive_failures)
            resp = await self._mock.search_nearby_restaurants(
                food_code=food_code,
                location_context=location_context,
                radius_m=radius_m,
                limit=limit,
                cursor=cursor,
                mock_mode=mock_mode,
            )
            return self._stamp_provider_live_when_mock_fallback(resp)

        # 3) 缓存查询（不含 mock_mode，避免 UI 四态切换被缓存污染）
        cache_key = (food_code, location_context.display_name, radius_m, limit, cursor or "", self._mode)
        if self._cache_ttl_s > 0:
            self._purge_expired_cache()
            entry = self._cache.get(cache_key)
            if entry is not None:
                cached = RestaurantSearchResponseV1(
                    data=list(entry.value.data),
                    meta=RestaurantSearchMeta(
                        next_cursor=entry.value.meta.next_cursor,
                        cached=True,
                        provider_mode=entry.value.meta.provider_mode,
                    ),
                    suggestions=list(entry.value.suggestions),
                )
                return cached

        # 4) Live 调用 + 自动降级
        if self._live is None:
            # 没有 Live（auto 且 AMAP_API_KEY 空），返回 Mock（标 cached=False，防止混淆）
            resp = await self._mock.search_nearby_restaurants(
                food_code=food_code,
                location_context=location_context,
                radius_m=radius_m,
                limit=limit,
                cursor=cursor,
                mock_mode=mock_mode,
            )
            return self._stamp_provider_live_when_mock_fallback(resp)

        try:
            result = await self._live.search_nearby_restaurants(
                food_code=food_code,
                location_context=location_context,
                radius_m=radius_m,
                limit=limit,
                cursor=cursor,
                mock_mode=mock_mode,
            )
        except AppError as exc:
            # Live 抛出"上游不可用"类错误 → 计数+可能降级
            self._consecutive_failures += 1
            logger.warning(
                "poi_live_failure consecutive_failures=%d code=%s",
                self._consecutive_failures, exc.code,
            )
            if self._mode == "auto" and self._consecutive_failures >= _DEGRADE_FAILURE_THRESHOLD:
                self._degrade_until = time.monotonic() + _DEGRADE_WINDOW_S
                logger.warning(
                    "poi_auto_degrade_triggered window_s=%d", _DEGRADE_WINDOW_S,
                )
            # auto 模式：单请求级 fallback 到 Mock（哪怕还没到阈值），让用户仍有结果
            if self._mode == "auto":
                fallback = await self._mock.search_nearby_restaurants(
                    food_code=food_code,
                    location_context=location_context,
                    radius_m=radius_m,
                    limit=limit,
                    cursor=cursor,
                    mock_mode=mock_mode,
                )
                return self._stamp_provider_live_when_mock_fallback(fallback)
            raise  # live 模式：不降级，直接抛给上层统一错误中间件
        else:
            self._consecutive_failures = 0
            # 5) 写入缓存
            if self._cache_ttl_s > 0:
                self._cache[cache_key] = _CacheEntry(result, self._cache_ttl_s)
            return result

    # ---- 内部辅助 ----

    def _purge_expired_cache(self) -> None:
        now = time.monotonic()
        expired = [k for k, v in self._cache.items() if v.expires_at <= now]
        for k in expired:
            del self._cache[k]

    @staticmethod
    def _stamp_provider_live_when_mock_fallback(resp: RestaurantSearchResponseV1) -> RestaurantSearchResponseV1:
        """auto 降级返回 Mock 数据时，provider_mode 仍标 mock，前端已根据来源标注展示；不做伪装。"""
        # 保持 provider_mode=mock，语义诚实
        return resp

    # ---- 测试辅助 ----
    def _test_set_degrade_window(self, until: float) -> None:
        self._degrade_until = until
        self._consecutive_failures = _DEGRADE_FAILURE_THRESHOLD

    def _test_set_cache(self, key: tuple[str, str, int, int, str, str], value: RestaurantSearchResponseV1) -> None:
        self._cache[key] = _CacheEntry(value, max(60, self._cache_ttl_s))


# ============================================================
# 全局单例工厂（get_poi_provider）
# ============================================================

_provider: POIProviderProtocol | None = None


def _build_provider_from_settings() -> POIProviderProtocol:
    """根据 Settings 构造 provider。纯函数便于测试复用。"""
    settings = get_settings()
    raw = str(settings.poi_provider or "mock").strip().lower()
    if raw not in ("mock", "live", "auto"):
        logger.warning("invalid_poi_provider value=%s fallback=mock", raw)
        raw = "mock"
    mode: ProviderModeValue = raw  # type: ignore[assignment]

    mock = MockPOIProvider()

    # mock 模式：直接返回 Mock
    if mode == "mock":
        return mock

    # live / auto：尝试构造 Amap
    key = str(settings.amap_api_key or "").strip()
    amap: AmapPOIProvider | None = None
    try:
        if key:
            amap = AmapPOIProvider(api_key=key)
    except Exception:
        logger.exception("amap_provider_init_failed")
        amap = None

    ttl = int(getattr(settings, "poi_cache_ttl_seconds", 1200) or 1200)
    wrapper = AutoFailoverPOIProvider(
        live=amap,
        mock=mock,
        mode=mode,
        cache_ttl_s=ttl,
    )
    if mode == "live" and amap is None:
        # live 模式强制要求 key；抛 ValueError 让启动即失败（fail-fast）
        raise ValueError(
            "POI_PROVIDER=live 但 AMAP_API_KEY 未配置。"
            "请设置环境变量或改用 poi_provider=auto/mock。"
        )
    return wrapper


def get_poi_provider() -> POIProviderProtocol:
    """获取全局 POIProvider 单例（带缓存+自动降级 Wrapper）。

    单例模式：首次调用读取 Settings 并初始化。
    测试用：`reset_poi_provider()` 清理后可重新注入。
    """
    global _provider
    if _provider is None:
        _provider = _build_provider_from_settings()
    return _provider


def reset_poi_provider() -> None:
    """测试用：重置单例。"""
    global _provider
    _provider = None


__all__ = [
    "AMAP_AROUND_URL",
    "DEFAULT_SLOW_DELAY_MS",
    "AmapPOIProvider",
    "AutoFailoverPOIProvider",
    "MockMode",
    "MockPOIProvider",
    "POIProviderProtocol",
    "ProviderModeValue",
    "_amap_poi_to_item",  # 测试直接映射
    "_build_empty_suggestions",
    "_build_mock_items",
    "_category_to_label",
    "get_poi_provider",
    "reset_poi_provider",
]
