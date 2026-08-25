"""P5-07 / P5-07B AI 日限流双实现（进程内 TTLCache / Redis 原子 INCR+EXPIRE）。

统一对外契约：所有限流器都提供 ``async consume_or_reject(user_id:str)->AIRateLimitResult``，
调用方 ChatService 只需 await，不需要关心到底是进程内还是跨进程。

两种实现选其一（基于 ``settings.redis_url`` 自动切换，见底部 ``build_ai_rate_limiter``）：

1. ``AIRateLimiterLocal``（P5-07 默认，单机/单 worker 场景）
   - 用 UTC 日期 key 前缀 + TTLCache 做双维度计数
   - user 维度 maxsize=100k，超了 LRU 淘汰 → 最坏会比限额更松（可接受保守退化）
   - 全局维度用同一 TTLCache，maxsize=30 足够存 30 天的 key（历史 key 86400s 自动过期）
   - 用 threading.Lock 保证线程安全（gunicorn sync worker 场景也 OK）
   - 在 async def 里调用同步 lock/dict 操作：耗时 <1ms，事件循环可以接受

2. ``AIRateLimiterRedis``（P5-07B，多 worker / 多机器 / 生产严格限流）
   - 一条 Lua 脚本原子"读取两维当前值 → 判断 → 若允许则 INCR 并在 key 首次写入时设置 TTL（到当日 23:59:59 UTC 剩余秒数 + 60s buffer）"
   - Redis key 约定：``eatwhat:aiquota:user:{day}:{user_id}``、``eatwhat:aiquota:global:{day}``
   - 单节点 Redis 即可承载极高 QPS（单 Lua 执行 <0.1ms）
   - 连接断开/超时：为了不阻塞推荐链路，fail-safe 降级为"放行并打印 warning"（保守策略：永远 fail-open 不拦截）
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final, Literal, Protocol, runtime_checkable

from cachetools import TTLCache

from app.core.config import Settings

log = logging.getLogger("app.services.ai.rate_limiter")

QuotaReason = Literal['ok', 'user_limit', 'global_limit']

REASON_OK: Final[QuotaReason] = 'ok'
REASON_USER_LIMIT: Final[QuotaReason] = 'user_limit'
REASON_GLOBAL_LIMIT: Final[QuotaReason] = 'global_limit'


@dataclass(slots=True, frozen=True)
class AIRateLimitResult:
    allowed: bool
    reason: QuotaReason
    user_today_used: int
    global_today_used: int
    user_limit: int
    global_limit: int


def _day_key() -> str:
    """UTC 日期 YYYY-MM-DD；换日自动归零。"""
    return datetime.now(tz=UTC).date().isoformat()


def _seconds_until_end_of_utc_day(buffer: int = 60) -> int:
    """距离 UTC 今日 23:59:59 还剩多少秒；再加 buffer 避免整点 Redis 集群同步抖动。"""
    now = datetime.now(tz=UTC)
    end = now.replace(hour=23, minute=59, second=59, microsecond=0)
    delta = int((end - now).total_seconds())
    delta = max(delta, 0)
    return delta + buffer


# -------------- 对外统一 Protocol（鸭子类型；调用方面向接口编程）--------------

@runtime_checkable
class AIRateLimiter(Protocol):
    """P5-07 统一限流协议：Local/Redis 都实现 async consume_or_reject。"""

    async def consume_or_reject(self, *, user_id: str) -> AIRateLimitResult:  # pragma: no cover
        ...

    async def rollback_consume(self, *, user_id: str) -> None:  # pragma: no cover
        """P5-05：AI 真正失败时返还一次"预占"额度（consume 后如果模型调用/校验失败，
        需要调用本方法，保证"成功才扣、失败全退"语义。多次 rollback 最多退回 0，
        不会产生负计数。仅保证同 user_id 和全局维度各回退 1 次/次调用。"""
        ...


# ============================================================
# 1) 进程内实现（默认）
# ============================================================

class AIRateLimiterLocal:
    """进程内日限流：TTLCache + threading.Lock。"""

    def __init__(
        self,
        *,
        user_daily_limit: int,
        global_daily_limit: int,
        lock: threading.Lock | None = None,
    ) -> None:
        self._user_limit: int = int(user_daily_limit or 0)
        self._global_limit: int = int(global_daily_limit or 0)
        self._user_cache: TTLCache[str, int] = TTLCache(maxsize=100_000, ttl=86_400)
        self._global_cache: TTLCache[str, int] = TTLCache(maxsize=30, ttl=86_400)
        self._lock: threading.Lock = lock if lock is not None else threading.Lock()

    async def consume_or_reject(self, *, user_id: str) -> AIRateLimitResult:
        user_key = f"{_day_key()}:{user_id}"
        global_key = _day_key()

        with self._lock:
            cur_user: int = self._user_cache.get(user_key, 0) or 0
            cur_global: int = self._global_cache.get(global_key, 0) or 0

            user_enabled = self._user_limit > 0
            global_enabled = self._global_limit > 0

            # 先 user 维度
            if user_enabled and cur_user >= self._user_limit:
                return AIRateLimitResult(
                    allowed=False,
                    reason=REASON_USER_LIMIT,
                    user_today_used=cur_user,
                    global_today_used=cur_global,
                    user_limit=self._user_limit,
                    global_limit=self._global_limit,
                )
            # 再 global 维度
            if global_enabled and cur_global >= self._global_limit:
                return AIRateLimitResult(
                    allowed=False,
                    reason=REASON_GLOBAL_LIMIT,
                    user_today_used=cur_user,
                    global_today_used=cur_global,
                    user_limit=self._user_limit,
                    global_limit=self._global_limit,
                )

            # 允许：原子增加两个计数器（并发请求不会双漏/多扣）
            next_user = cur_user + 1
            next_global = cur_global + 1
            # 如果该 user/global day key 首次写入，TTL 会在 86400 秒后清（兜底）
            self._user_cache[user_key] = next_user
            self._global_cache[global_key] = next_global
            return AIRateLimitResult(
                allowed=True,
                reason=REASON_OK,
                user_today_used=next_user,
                global_today_used=next_global,
                user_limit=self._user_limit,
                global_limit=self._global_limit,
            )

    async def rollback_consume(self, *, user_id: str) -> None:
        """返还一次消费（用户维度 + 全局维度各 -1，最低 0）。"""
        user_key = f"{_day_key()}:{user_id}"
        global_key = _day_key()
        with self._lock:
            for key, cache in (
                (user_key, self._user_cache),
                (global_key, self._global_cache),
            ):
                cur = cache.get(key, 0) or 0
                if cur <= 0:
                    continue
                cache[key] = cur - 1


# ============================================================
# 2) Redis 实现（P5-07B 生产多 worker）
# ============================================================

# Lua 脚本：原子判断-增加-首次写入时设 TTL；执行一次 = 最多 2 次 hash 查找 + 1 次 EVAL。
# 入参 KEYS[1]=user_key, KEYS[2]=global_key
# 入参 ARGV[1]=user_limit_int, ARGV[2]=global_limit_int, ARGV[3]=ttl_seconds_to_end_of_day
# 返回数组 [allowed_int(0|1), reason_code_int, cur_user_int_after_or_at_deny, cur_global_int_after_or_at_deny]
#
# reason_code_int 约定：
#   0 = OK
#   1 = user_limit
#   2 = global_limit
_LUA_INCR_CHECK = '''
local user_key = KEYS[1]
local global_key = KEYS[2]
local user_limit = tonumber(ARGV[1])
local global_limit = tonumber(ARGV[2])
local ttl_sec = tonumber(ARGV[3])

-- 读当前值（不存在按 0）
local cur_user = tonumber(redis.call('GET', user_key) or '0')
local cur_global = tonumber(redis.call('GET', global_key) or '0')

-- 1. user 维度检查
if user_limit > 0 and cur_user >= user_limit then
    return {0, 1, cur_user, cur_global}
end
-- 2. global 维度检查
if global_limit > 0 and cur_global >= global_limit then
    return {0, 2, cur_user, cur_global}
end

-- 允许：自增（INCR 在 key 不存在时 = SET 1）
local next_user = redis.call('INCR', user_key)
local next_global = redis.call('INCR', global_key)

-- 如果这是第一次写入（INCR 后值 == 1），设置 TTL 到今日结束
if next_user == 1 then
    redis.call('EXPIRE', user_key, ttl_sec)
end
if next_global == 1 then
    redis.call('EXPIRE', global_key, ttl_sec)
end

return {1, 0, next_user, next_global}
'''

_INT_TO_REASON: dict[int, QuotaReason] = {
    0: REASON_OK,
    1: REASON_USER_LIMIT,
    2: REASON_GLOBAL_LIMIT,
}

_USER_KEY_PREFIX = "eatwhat:aiquota:user:"
_GLOBAL_KEY_PREFIX = "eatwhat:aiquota:global:"


class AIRateLimiterRedis:
    """跨进程日限流：Redis Lua。任何 Redis 错误 → fail-safe 放行（不阻塞推荐链路）。

    脚本执行策略（兼容生产 + 测试 mock + Redis 重启场景）：
        1. 先尝试 SCRIPT LOAD + EVALSHA（省带宽）
        2. SCRIPT LOAD 失败（如 fakeredis 精简版不支持）→ 直接 EVAL
        3. EVALSHA 抛 NOSCRIPT（Redis 重启后脚本缓存清掉）→ 清掉 sha 缓存 + 重新 SCRIPT LOAD，仍失败则 EVAL
        4. 以上任一阶段异常 → fail-safe 放行并打 warning
    """

    def __init__(
        self,
        *,
        user_daily_limit: int,
        global_daily_limit: int,
        redis_url: str,
    ) -> None:
        self._user_limit: int = int(user_daily_limit or 0)
        self._global_limit: int = int(global_daily_limit or 0)
        if not redis_url:
            raise ValueError("AIRateLimiterRedis 必须提供 redis_url")
        import redis.asyncio as aioredis

        self._redis_url: str = redis_url
        self._redis = aioredis.from_url(self._redis_url, decode_responses=True)
        self._script_sha: str | None = None
        self._sha_lock = threading.Lock()

    async def _try_script_load(self) -> str | None:
        """尝试 SCRIPT LOAD；不支持时返回 None（让上层走 EVAL 兜底）。"""
        try:
            return await self._redis.script_load(_LUA_INCR_CHECK)
        except Exception as exc:
            log.warning("ai_rate_limiter_redis_script_load_skip err_type=%s msg=%s", type(exc).__name__, exc)
            return None

    async def _ensure_script_sha(self) -> str | None:
        if self._script_sha is not None:
            return self._script_sha
        with self._sha_lock:
            if self._script_sha is not None:
                return self._script_sha
            sha = await self._try_script_load()
            self._script_sha = sha
            return sha

    def _build_result(self, raw: list[int] | tuple[int, ...]) -> AIRateLimitResult:
        allowed_i, reason_i, cur_u, cur_g = (int(x) for x in raw)
        allowed = bool(allowed_i)
        if allowed:
            reason: QuotaReason = REASON_OK
        else:
            reason = _INT_TO_REASON.get(reason_i, REASON_OK)
        return AIRateLimitResult(
            allowed=allowed,
            reason=reason,
            user_today_used=cur_u,
            global_today_used=cur_g,
            user_limit=self._user_limit,
            global_limit=self._global_limit,
        )

    async def consume_or_reject(self, *, user_id: str) -> AIRateLimitResult:
        day = _day_key()
        user_key = f"{_USER_KEY_PREFIX}{day}:{user_id}"
        global_key = f"{_GLOBAL_KEY_PREFIX}{day}"
        ttl = _seconds_until_end_of_utc_day(buffer=60)
        # Redis EVAL/EVALSHA 标准签名：SCRIPT, numkeys:int, key1, key2, ..., arg1, arg2, ...
        num_keys = 2
        ul: int = self._user_limit
        gl: int = self._global_limit

        # 阶段一：EVALSHA（有 sha 缓存时）
        sha = await self._ensure_script_sha()
        if sha is not None:
            try:
                result = await self._redis.evalsha(sha, num_keys, user_key, global_key, ul, gl, ttl)
                return self._build_result(result)
            except Exception as exc:
                msg = str(exc).upper()
                is_noscript = "NOSCRIPT" in msg
                if is_noscript:
                    # Redis 重启清了脚本缓存 → 清空 sha，下一轮或当前轮尝试 SCRIPT LOAD/EVAL
                    log.warning("ai_rate_limiter_redis_noscript_reset_sha")
                    with self._sha_lock:
                        self._script_sha = None
                    # 尝试重新 load 一次
                    new_sha = await self._ensure_script_sha()
                    if new_sha is not None:
                        try:
                            result = await self._redis.evalsha(new_sha, num_keys, user_key, global_key, ul, gl, ttl)
                            return self._build_result(result)
                        except Exception as exc2:
                            log.info(
                                "ai_rate_limiter_redis_evalsha_retry_fail err_type=%s",
                                type(exc2).__name__,
                            )
                            # fall-through 到 EVAL
                elif not isinstance(exc, (ConnectionError, TimeoutError, OSError)):
                    # 非连接类异常（比如 ResponseError）→ 直接走 EVAL 兜底（fakeredis 可能报 script 命令不存在）
                    pass
                else:
                    # 连接类异常 → 直接 fail-safe 放行
                    return self._failsafe_result(exc)

        # 阶段二：直接 EVAL（脚本未缓存或 SCRIPT LOAD 不支持）
        try:
            result = await self._redis.eval(_LUA_INCR_CHECK, num_keys, user_key, global_key, ul, gl, ttl)
            return self._build_result(result)
        except Exception as exc:
            return self._failsafe_result(exc)

    def _failsafe_result(self, exc: Exception) -> AIRateLimitResult:
        log.warning(
            "ai_rate_limiter_redis_failsafe err_type=%s msg=%s user_limit=%s global_limit=%s",
            type(exc).__name__,
            exc,
            self._user_limit,
            self._global_limit,
        )
        return AIRateLimitResult(
            allowed=True,
            reason=REASON_OK,
            user_today_used=0,
            global_today_used=0,
            user_limit=self._user_limit,
            global_limit=self._global_limit,
        )

    async def close(self) -> None:
        """应用关闭时主动断开 Redis 连接（可选）。"""
        try:
            await self._redis.aclose()
        except Exception as exc:
            log.warning("ai_rate_limiter_redis_close err_type=%s", type(exc).__name__)

    async def rollback_consume(self, *, user_id: str) -> None:
        """user 维度 + 全局维度各 DECR 1，最低 0。错误 fail-safe 静默。"""
        day = _day_key()
        user_key = f"{_USER_KEY_PREFIX}{day}:{user_id}"
        global_key = f"{_GLOBAL_KEY_PREFIX}{day}"
        try:
            async with self._redis.pipeline(transaction=True):
                # DECR 后若 < 0 → 再 SET 0（防止并发/多次 rollback 产生负数），
                # 用先 DECR 再 SETRANGE/CLAMP：Redis 没有 CLAMP 命令，所以先 GET
                for k in (user_key, global_key):
                    cur_raw = await self._redis.get(k)
                    try:
                        cur = int(cur_raw) if cur_raw is not None else 0
                    except (TypeError, ValueError):
                        cur = 0
                    if cur > 0:
                        # 直接 SET cur-1 比 DECR + 再判断更原子且少一次 round-trip
                        await self._redis.set(k, str(cur - 1), keepttl=True)
        except Exception as exc:
            log.warning(
                "ai_rate_limiter_redis_rollback_fail err_type=%s msg=%s",
                type(exc).__name__,
                exc,
            )


# ============================================================
# 工厂：根据 settings 自动选实现
# ============================================================

def build_ai_rate_limiter(settings: Settings) -> AIRateLimiter:
    """优先 Redis（配置 redis_url）；否则用进程内 TTLCache。

    :raises ValueError: redis_url 非空但 redis 包未安装（用户手动 ``uv add redis`` 即可）。
    """
    if settings.redis_url:
        return AIRateLimiterRedis(
            user_daily_limit=settings.ai_daily_user_limit,
            global_daily_limit=settings.ai_global_daily_limit,
            redis_url=settings.redis_url,
        )
    return AIRateLimiterLocal(
        user_daily_limit=settings.ai_daily_user_limit,
        global_daily_limit=settings.ai_global_daily_limit,
    )
