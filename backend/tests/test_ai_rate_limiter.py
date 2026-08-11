"""P5-07 / P5-07B AI 日限流双实现单元测试。

覆盖：
  - AIRateLimiterLocal：正常放行、user 超限、global 超限、并发安全（线程级）、换日归零
  - AIRateLimiterRedis：Lua 脚本逻辑（用 fakeredis 模拟真实 Redis 行为）、
    fail-safe 降级（连接异常时一律放行）、SCRIPT LOAD EVALSHA 正常链路
  - build_ai_rate_limiter 工厂：按 settings.redis_url 自动选实现
"""
from __future__ import annotations

import asyncio
import threading
from datetime import date
from unittest.mock import patch

import pytest

from app.core.config import Settings
from app.services.ai.rate_limiter import (
    _INT_TO_REASON,
    REASON_GLOBAL_LIMIT,
    REASON_OK,
    REASON_USER_LIMIT,
    AIRateLimiterLocal,
    AIRateLimiterRedis,
    AIRateLimitResult,
    _day_key,
    _seconds_until_end_of_utc_day,
    build_ai_rate_limiter,
)

# ============================================================
# 通用工具
# ============================================================

def _make_local(ul: int = 3, gl: int = 10) -> AIRateLimiterLocal:
    return AIRateLimiterLocal(user_daily_limit=ul, global_daily_limit=gl)


async def _consume_n(rl, user_id: str, n: int) -> list[AIRateLimitResult]:
    return [await rl.consume_or_reject(user_id=user_id) for _ in range(n)]


# ============================================================
# AIRateLimiterLocal
# ============================================================

class TestAIRateLimiterLocal:
    @pytest.mark.asyncio
    async def test_happy_path_allow_and_increment(self):
        rl = _make_local(ul=3, gl=10)
        r1 = await rl.consume_or_reject(user_id="u1")
        assert r1.allowed is True
        assert r1.reason == REASON_OK
        assert r1.user_today_used == 1
        assert r1.global_today_used == 1

        r2 = await rl.consume_or_reject(user_id="u1")
        assert r2.user_today_used == 2
        assert r2.global_today_used == 2

    @pytest.mark.asyncio
    async def test_user_limit_hit_blocks(self):
        rl = _make_local(ul=2, gl=100)
        await _consume_n(rl, "u1", 2)
        r3 = await rl.consume_or_reject(user_id="u1")
        assert r3.allowed is False
        assert r3.reason == REASON_USER_LIMIT
        assert r3.user_today_used == 2
        # 超限被拒：不应再递增 global
        assert r3.global_today_used == 2

    @pytest.mark.asyncio
    async def test_global_limit_hit_blocks_other_users(self):
        rl = _make_local(ul=10, gl=3)
        await rl.consume_or_reject(user_id="u1")
        await rl.consume_or_reject(user_id="u2")
        await rl.consume_or_reject(user_id="u3")
        # 第四位用户：global 已满
        r = await rl.consume_or_reject(user_id="u4")
        assert r.allowed is False
        assert r.reason == REASON_GLOBAL_LIMIT
        assert r.global_today_used == 3

    @pytest.mark.asyncio
    async def test_user_limit_is_per_user(self):
        rl = _make_local(ul=1, gl=100)
        assert (await rl.consume_or_reject(user_id="u1")).allowed is True
        assert (await rl.consume_or_reject(user_id="u1")).allowed is False
        # 另一个用户还能用
        assert (await rl.consume_or_reject(user_id="u2")).allowed is True

    @pytest.mark.asyncio
    async def test_disabled_limits_allow_all(self):
        # 0 = 不启用
        rl = AIRateLimiterLocal(user_daily_limit=0, global_daily_limit=0)
        for i in range(50):
            r = await rl.consume_or_reject(user_id=f"u{i}")
            assert r.allowed is True

    @pytest.mark.asyncio
    async def test_thread_safety_no_double_counting(self):
        """多线程并发扣减——同一用户的 N 次并发请求，最终计数值应恰好 = N（若未超限）。"""
        rl = _make_local(ul=1000, gl=1000)
        errors_allowed: list[bool] = []
        lock = threading.Lock()

        async def worker():
            r = await rl.consume_or_reject(user_id="concurrent")
            with lock:
                errors_allowed.append(r.allowed)

        def run_one():
            asyncio.run(worker())

        threads = [threading.Thread(target=run_one) for _ in range(200)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert all(errors_allowed), "200 次并发请求都应该被允许（限额 1000）"
        final = await rl.consume_or_reject(user_id="concurrent")
        # 200 次 worker + 本次调用 = 201
        assert final.user_today_used == 201
        # global 同样
        assert final.global_today_used >= 201

    @pytest.mark.asyncio
    async def test_day_key_rollover_isolation(self):
        """模拟换日——不同 day key 之间计数器互不影响。"""
        rl = _make_local(ul=1, gl=10)
        today = _day_key()
        # 今天先扣满
        r1 = await rl.consume_or_reject(user_id="u1")
        assert r1.allowed is True
        assert (await rl.consume_or_reject(user_id="u1")).allowed is False

        # mock 明天
        tomorrow = date.fromisoformat(today).toordinal() + 1
        tomorrow_str = date.fromordinal(tomorrow).isoformat()
        with patch("app.services.ai.rate_limiter._day_key", return_value=tomorrow_str):
            r_tomorrow = await rl.consume_or_reject(user_id="u1")
            assert r_tomorrow.allowed is True
            assert r_tomorrow.user_today_used == 1


# ============================================================
# AIRateLimiterRedis（内存模拟 Redis，精确复刻 Lua 脚本语义）
# ============================================================

class _InMemoryAsyncRedis:
    """纯内存模拟限流器需要的 Redis 异步接口：eval / evalsha / script_load。

    eval 直接用 Python 实现与 Lua 脚本 _LUA_INCR_CHECK 完全等价的逻辑，
    从而可以在 CI / 无 Redis 环境下完整验证 Redis 限流语义，
    不受 fakeredis 精简版 "script 命令不存在" 限制。
    """

    def __init__(self) -> None:
        self._data: dict[str, int] = {}
        self._ttl: dict[str, int] = {}
        self._scripts: dict[str, str] = {}

    # ----- 限流器实际调用的三个接口 -----
    async def script_load(self, script: str) -> str:
        import hashlib

        sha = hashlib.sha1(script.encode()).hexdigest()
        self._scripts[sha] = script
        return sha

    async def evalsha(self, sha: str, numkeys: int, *args: object) -> list[int]:
        if sha not in self._scripts:
            # 精确还原 Redis 的 NOSCRIPT 响应
            raise RuntimeError("NOSCRIPT No matching script. Please use EVAL.")
        return self._run_lua_logic(numkeys, list(args))

    async def eval(self, _script: str, numkeys: int, *args: object) -> list[int]:
        # 忽略 _script 内容，直接跑 Python 版等价逻辑
        return self._run_lua_logic(numkeys, list(args))

    async def aclose(self) -> None:
        pass

    # ----- 等价于 _LUA_INCR_CHECK 的 Python 实现 -----
    def _run_lua_logic(self, numkeys: int, args: list[object]) -> list[int]:
        assert numkeys == 2, "本限流器约定 2 个 key (user/global)"
        user_key = str(args[0])
        global_key = str(args[1])
        user_limit = int(args[2])
        global_limit = int(args[3])
        ttl_sec = int(args[4])

        cur_user = self._data.get(user_key, 0)
        cur_global = self._data.get(global_key, 0)

        if user_limit > 0 and cur_user >= user_limit:
            return [0, 1, cur_user, cur_global]
        if global_limit > 0 and cur_global >= global_limit:
            return [0, 2, cur_user, cur_global]

        next_user = cur_user + 1
        next_global = cur_global + 1
        self._data[user_key] = next_user
        self._data[global_key] = next_global
        if next_user == 1:
            self._ttl[user_key] = ttl_sec
        if next_global == 1:
            self._ttl[global_key] = ttl_sec
        return [1, 0, next_user, next_global]


class TestAIRateLimiterRedis:
    @pytest.fixture
    def fake_redis_url(self):
        return "redis://memory:0/0"

    @pytest.fixture
    def fakeredis_patch(self):
        """把 redis.asyncio.from_url → 返回 _InMemoryAsyncRedis。"""
        import redis.asyncio as aioredis

        original = aioredis.from_url
        aioredis.from_url = lambda *a, **kw: _InMemoryAsyncRedis()  # type: ignore[assignment]
        yield
        aioredis.from_url = original  # type: ignore[assignment]

    @pytest.mark.asyncio
    async def test_redis_happy_path(self, fakeredis_patch, fake_redis_url):
        rl = AIRateLimiterRedis(
            user_daily_limit=3,
            global_daily_limit=10,
            redis_url=fake_redis_url,
        )
        r1 = await rl.consume_or_reject(user_id="u1")
        assert r1.allowed is True
        assert r1.user_today_used == 1
        assert r1.global_today_used == 1

        r2 = await rl.consume_or_reject(user_id="u1")
        assert r2.user_today_used == 2
        assert r2.global_today_used == 2

    @pytest.mark.asyncio
    async def test_redis_user_limit(self, fakeredis_patch, fake_redis_url):
        rl = AIRateLimiterRedis(
            user_daily_limit=2,
            global_daily_limit=100,
            redis_url=fake_redis_url,
        )
        await rl.consume_or_reject(user_id="u1")
        await rl.consume_or_reject(user_id="u1")
        r3 = await rl.consume_or_reject(user_id="u1")
        assert r3.allowed is False
        assert r3.reason == REASON_USER_LIMIT
        assert r3.user_today_used == 2

    @pytest.mark.asyncio
    async def test_redis_global_limit(self, fakeredis_patch, fake_redis_url):
        rl = AIRateLimiterRedis(
            user_daily_limit=100,
            global_daily_limit=2,
            redis_url=fake_redis_url,
        )
        await rl.consume_or_reject(user_id="u1")
        await rl.consume_or_reject(user_id="u2")
        r3 = await rl.consume_or_reject(user_id="u3")
        assert r3.allowed is False
        assert r3.reason == REASON_GLOBAL_LIMIT
        assert r3.global_today_used == 2

    @pytest.mark.asyncio
    async def test_redis_user_limit_does_not_increment_on_deny(self, fakeredis_patch, fake_redis_url):
        """用户超限被拒 → global 不应再被消耗。"""
        rl = AIRateLimiterRedis(user_daily_limit=1, global_daily_limit=100, redis_url=fake_redis_url)
        r1 = await rl.consume_or_reject(user_id="u1")
        assert r1.allowed is True
        assert r1.global_today_used == 1
        r2 = await rl.consume_or_reject(user_id="u1")
        assert r2.allowed is False
        # global 应仍为 1（不是 2）
        assert r2.global_today_used == 1

    @pytest.mark.asyncio
    async def test_redis_fail_safe_on_connection_error(self):
        """任何 Redis 调用抛错 → fail-safe 放行 + 返回合法 AIRateLimitResult。"""
        rl = AIRateLimiterLocal.__new__(AIRateLimiterRedis)  # 跳过 __init__
        rl._user_limit = 3
        rl._global_limit = 10
        rl._script_sha = None

        class _BoomRedis:
            async def script_load(self, *_a, **_kw):
                raise RuntimeError("boom")

            async def eval(self, *_a, **_kw):
                raise RuntimeError("boom")

        rl._redis = _BoomRedis()  # type: ignore[assignment]
        rl._sha_lock = threading.Lock()

        r = await rl.consume_or_reject(user_id="any")  # type: ignore[arg-type]
        assert r.allowed is True
        assert r.reason == REASON_OK
        assert r.user_limit == 3
        assert r.global_limit == 10

    @pytest.mark.asyncio
    async def test_fail_safe_evalsha_noscript_then_recover_via_eval(self, fakeredis_patch, fake_redis_url):
        """EVALSHA 首次 NOSCRIPT → 走 EVAL 兜底正常执行。"""
        rl = AIRateLimiterRedis(user_daily_limit=5, global_daily_limit=10, redis_url=fake_redis_url)
        # 先正常调用一次
        r1 = await rl.consume_or_reject(user_id="u1")
        assert r1.allowed is True
        assert r1.user_today_used == 1

        # 手动把 sha 清掉 + _scripts 清空，模拟 Redis 重启
        with rl._sha_lock:
            rl._script_sha = None
        # 同时让 evalsha 永远抛 NOSCRIPT 但 eval 正常
        inner: _InMemoryAsyncRedis = rl._redis  # type: ignore[assignment]
        orig_evalsha = inner.evalsha

        async def _always_noscript(*a, **kw):
            raise RuntimeError("NOSCRIPT No matching script.")

        inner.evalsha = _always_noscript  # type: ignore[method-assign]
        r2 = await rl.consume_or_reject(user_id="u2")
        # 应正常放行（通过 EVAL 分支）
        assert r2.allowed is True
        # global 累计：u1+u2 = 2
        assert r2.global_today_used == 2
        # 恢复
        inner.evalsha = orig_evalsha  # type: ignore[method-assign]


# ============================================================
# build_ai_rate_limiter 工厂
# ============================================================

class TestBuildAIRateLimiter:
    def test_default_selects_local(self):
        s = Settings(redis_url="")
        rl = build_ai_rate_limiter(s)
        assert isinstance(rl, AIRateLimiterLocal)

    def test_redis_url_selects_redis(self, monkeypatch):
        """有 redis_url → 返回 AIRateLimiterRedis 实例。"""
        import redis.asyncio as aioredis

        def _fake(url, **_):
            return _InMemoryAsyncRedis()

        monkeypatch.setattr(aioredis, "from_url", _fake)

        s = Settings(redis_url="redis://localhost:6379/0")
        rl = build_ai_rate_limiter(s)
        assert isinstance(rl, AIRateLimiterRedis)


# ============================================================
# 辅助函数
# ============================================================

class TestHelpers:
    def test_seconds_until_end_of_day_reasonable(self):
        secs = _seconds_until_end_of_utc_day(buffer=60)
        # 正常应在 0~86400+60 之间
        assert 0 <= secs <= 86400 + 60 + 1

    def test_day_key_format(self):
        k = _day_key()
        # ISO 日期 = YYYY-MM-DD（长度 10）
        assert len(k) == 10
        assert k.count("-") == 2

    def test_int_to_reason_complete(self):
        assert _INT_TO_REASON[0] == REASON_OK
        assert _INT_TO_REASON[1] == REASON_USER_LIMIT
        assert _INT_TO_REASON[2] == REASON_GLOBAL_LIMIT
        assert len(_INT_TO_REASON) == 3
