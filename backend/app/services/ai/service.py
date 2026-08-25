"""P5-03 ChatService：AI 能力的业务侧统一门面。

职责分层（关键！代码维护成本主要在这一层）：
    1. Provider 选择：按 settings.ai_provider 自动选 mock / deepseek
    2. 超时控制：``asyncio.wait_for`` 硬卡 timeout_ms（防御 Provider 内部忘设超时）
    3. 密钥解密：DeepSeek 需要时调用 resolve_encrypted_api_key（只在本函数作用域
       暴露明文，不保存到实例属性，防止被 inspect/意外泄漏到日志）
    4. Schema 校验：用 Pydantic model_validate_json 强约束 AI 输出；一旦越界
       （字段缺失/新增/不在 food_code 启用字典/question_id 不符合规则等）一律视为
       "AI 输出不可信"→ 返回 None → 业务层回退规则引擎
    5. 异常统一：所有网络/超时/HTTP/模型限流/解密失败异常 → 捕获后写结构化日志
       → 返回 None。避免任何一条 AI 链路把整个推荐打挂。
    6. P5-09：细分失败码——通过 ContextVar（协程隔离）记录最后一次失败类型，
       业务层（如 recommendation_session）可在调用后 ``take_last_fail_code()``
       读出，用来把 session.final_reason 写得更细，最终给前端 source badge 提示。

用法示例（推荐路由 P5-04 接入）：
    service = ChatService(settings=settings)
    ai_result: FinalRecommendationOutput | None = (
        await service.generate_final_recommendation(profile=..., history=...)
    )
    fail_code = service.take_last_fail_code()
    if ai_result is None:
        # 回退规则引擎（真源），可按 fail_code 细分 final_reason
        candidates = rule_engine.generate(profile, history, k=5)
"""
from __future__ import annotations

import asyncio
import contextvars
import logging
from collections.abc import Mapping
from typing import Any

import httpx
from pydantic import BaseModel, ValidationError

from app.core.config import Settings
from app.core.encryption import AIKeyEncryptionError, resolve_encrypted_api_key
from app.schemas.ai import FinalRecommendationOutput, FollowUpQuestionOutput

from .base import AIProvider, ChatMessage
from .deepseek_provider import DeepSeekProvider
from .mock_provider import MockAIProvider
from .rate_limiter import AIRateLimiter, AIRateLimitResult

log = logging.getLogger("app.services.ai.service")

DEFAULT_CHAT_TIMEOUT_MS = 15_000
DEFAULT_FINAL_TEMPERATURE = 0.3
DEFAULT_FOLLOW_UP_TEMPERATURE = 0.5

# P5-09：细分失败码集合（保持小写加下划线，便于落库后直接用在前端 key）
FAIL_BUILD = "build"                # Provider 构建失败（密钥/配置）——未实际发出 HTTP 请求
FAIL_LOCAL_QUOTA = "local_quota"    # 本机 rate_limiter 超限（用户/全局任意一维）
FAIL_REMOTE_QUOTA = "remote_quota"  # 云端 DeepSeek 返回 429/限流
FAIL_UNAUTHORIZED = "unauthorized"  # 401/403（API key 错、被吊销、没权限）
FAIL_TIMEOUT = "timeout"            # 网络/asyncio.timeout 超时
FAIL_SCHEMA = "schema"              # Pydantic schema 校验失败
FAIL_UNKNOWN = "unknown"            # 其它未归类异常

_last_fail_code_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "_eatwhat_last_ai_fail_code",
    default=None,
)


# ---------- P5-09：模块级 ContextVar helpers ----------

def _reset_last_fail_code() -> None:
    """每次 generate_* 入口调用：清除上次遗留的失败码。"""
    _last_fail_code_var.set(None)


def _set_last_fail_code(code: str) -> None:
    _last_fail_code_var.set(code)


def _classify_http_status_code(status_code: int) -> str:
    """将 httpx.HTTPStatusError 的 status_code 映射为细分失败码。"""
    if status_code in {401, 403}:
        return FAIL_UNAUTHORIZED
    if status_code in {429, 503, 529}:
        # 429 TooManyRequests / 503 Unavailable / 529 Overload（常用云厂商过载码）
        return FAIL_REMOTE_QUOTA
    return FAIL_UNKNOWN


def take_last_fail_code() -> str | None:
    """读取并清除：返回最后一次失败码；上一次调用成功时返回 None。

    协程/线程安全：基于 ContextVar 实现，每条请求/协程独立副本，不会互相覆盖。
    """
    value = _last_fail_code_var.get(None)
    _last_fail_code_var.set(None)
    return value


class ChatService:
    """统一门面。不要在业务代码里直接引用 mock_provider / deepseek_provider。"""

    @staticmethod
    def take_last_fail_code() -> str | None:
        """读取并清除最后一次 AI 调用的细分失败码（ContextVar 协程隔离）。"""
        return take_last_fail_code()

    def __init__(
        self,
        *,
        settings: Settings,
        provider_override: AIProvider | None = None,
        rate_limiter: AIRateLimiter | None = None,
        default_timeout_ms: int | None = None,
        extra_telemetry: Mapping[str, Any] | None = None,
    ) -> None:
        self._settings = settings
        # 未显式指定时，优先用配置里的 ai_timeout_ms（DeepSeek 高峰期 15-30s 都可能），
        # 兜底用模块级 DEFAULT_CHAT_TIMEOUT_MS。
        timeout = (
            default_timeout_ms
            if default_timeout_ms is not None
            else int(getattr(settings, "ai_timeout_ms", DEFAULT_CHAT_TIMEOUT_MS))
        )
        self._default_timeout_ms = int(timeout)
        self._telemetry_base: dict[str, Any] = dict(extra_telemetry or {})
        self._rate_limiter = rate_limiter
        # 允许测试/演示注入 mock 实例（例如 out_of_bounds_food_code 模式）
        self._provider_override = provider_override

    @property
    def rate_limiter(self) -> AIRateLimiter | None:
        """只读：给外层业务层 peek 当前用户额度或执行自定义 rollback。"""
        return self._rate_limiter

    def peek_quota(self, *, user_id: str | None) -> dict[str, int]:
        """返回当前用户今日额度使用情况（仅查询，不扣）。
        返回 {user_used, user_limit, global_used, global_limit}。
        未登录（user_id=None）返回 0 used 但保留 limit。"""
        from .rate_limiter import (
            AIRateLimiterLocal,
        )
        from .rate_limiter import (
            _day_key as _rl_day_key,  # 避免重名
        )

        rl = self._rate_limiter
        user_limit = int(getattr(self._settings, "ai_daily_user_limit", 0) or 0)
        global_limit = int(getattr(self._settings, "ai_global_daily_limit", 0) or 0)
        user_used = 0
        global_used = 0
        # 目前只支持直接查询 Local 实现（Redis 实现需要 GET 两次，
        # 若用户要 P5-07B 再补；默认 Local 足够 Demo/个人使用场景）
        if isinstance(rl, AIRateLimiterLocal):
            day = _rl_day_key()
            user_key = f"{day}:{user_id}" if user_id else None
            global_key = day
            if user_key is not None:
                user_used = int(rl._user_cache.get(user_key, 0) or 0)
            global_used = int(rl._global_cache.get(global_key, 0) or 0)
        return {
            "user_used": user_used,
            "user_limit": user_limit,
            "global_used": global_used,
            "global_limit": global_limit,
        }

    # ============== 对外：动态追问生成 ==============
    async def generate_follow_up(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        round_index_1based: int,
        timeout_ms: int | None = None,
        user_id: str | None = None,
    ) -> FollowUpQuestionOutput | None:
        """生成一道最多 3 轮的追问题。失败返回 None，业务层走默认追问/直接最终生成。

        注意：追问题阶段 P5-09 的细分失败码会照常写入 ContextVar，但业务层
        （RecommendationSessionManager）当前并不会基于它做进一步展示——毕竟
        追问题失败只是"切回默认题"，对用户是完全透明的 fail-open。
        """
        _reset_last_fail_code()
        if round_index_1based < 1 or round_index_1based > 3:
            # 超出轮次上限 → 判失败，让业务层直接生成最终推荐（等价于"信息已充分"）
            log.info(
                "ai_follow_up round_oob round=%s telemetry=%s",
                round_index_1based,
                self._telemetry_base,
            )
            return None

        prompt_block = (
            f"{system_prompt}\n\n"
            f"[ROUND_{round_index_1based}] 当前是第 {round_index_1based} 轮追问。\n"
            f"{user_prompt}"
        )
        messages = [
            ChatMessage(role="system", content=system_prompt),
            ChatMessage(role="user", content=prompt_block),
        ]

        raw = await self._raw_chat_catch_all(
            messages=messages,
            temperature=DEFAULT_FOLLOW_UP_TEMPERATURE,
            timeout_ms=timeout_ms or self._default_timeout_ms,
            telemetry_tag="follow_up",
            user_id=user_id,
        )
        if raw is None:
            return None
        validated = self._safe_validate_json(raw, FollowUpQuestionOutput, tag="follow_up")
        if validated is None:
            _set_last_fail_code(FAIL_SCHEMA)
            # P5-05：follow_up schema 失败同样回滚本次额度预占
            if self._rate_limiter is not None and user_id is not None:
                try:
                    await self._rate_limiter.rollback_consume(
                        user_id=user_id if user_id is not None else "__anon__"
                    )
                except Exception as exc:
                    log.warning(
                        "ai_quota_rollback_schema_fail tag=follow_up "
                        "err_type=%s",
                        type(exc).__name__,
                    )
        return validated

    # ============== 对外：最终 5 候选生成 ==============
    async def generate_final_recommendation(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        timeout_ms: int | None = None,
        user_id: str | None = None,
    ) -> FinalRecommendationOutput | None:
        """生成最终 5 候选。失败返回 None，业务层回退规则引擎。

        调用后请使用 :meth:`take_last_fail_code` 读取细分失败码（若本次是
        失败返回），用于 session.final_reason 落库细化。
        """
        _reset_last_fail_code()
        prompt_block = (
            f"{system_prompt}\n\n"
            f"[FINAL_GENERATION] 信息已充分，请直接给出最终 Top5 推荐，不要再追问。\n"
            f"{user_prompt}"
        )
        messages = [
            ChatMessage(role="system", content=system_prompt),
            ChatMessage(role="user", content=prompt_block),
        ]

        raw = await self._raw_chat_catch_all(
            messages=messages,
            temperature=DEFAULT_FINAL_TEMPERATURE,
            timeout_ms=timeout_ms or self._default_timeout_ms,
            telemetry_tag="final_recommendation",
            user_id=user_id,
        )
        if raw is None:
            return None
        validated = self._safe_validate_json(raw, FinalRecommendationOutput, tag="final_rec")
        if validated is None:
            _set_last_fail_code(FAIL_SCHEMA)
            # P5-05：schema 校验失败也算失败，把 _raw_chat_catch_all 中预占的额度回滚
            if self._rate_limiter is not None and user_id is not None:
                try:
                    await self._rate_limiter.rollback_consume(
                        user_id=user_id if user_id is not None else "__anon__"
                    )
                except Exception as exc:
                    log.warning(
                        "ai_quota_rollback_schema_fail tag=final_recommendation "
                        "err_type=%s",
                        type(exc).__name__,
                    )
        return validated

    # ============== 内部实现 ==============
    async def _raw_chat_catch_all(
        self,
        *,
        messages: list[ChatMessage],
        temperature: float,
        timeout_ms: int,
        telemetry_tag: str,
        user_id: str | None = None,
    ) -> str | None:
        """实际调用 Provider，所有异常 → 返回 None，并按 P5-09 分类写入 fail_code。

        P5-05：额度语义变更为"成功才扣、失败全退"——
            1. 先调用 consume_or_reject 预占 1 次额度（超限直接拒绝）
            2. 再调用真实 Provider
            3. 若最终返回结果为 None（无论什么原因：网络/schema/timeout 等），
               调用 rollback_consume 将本次预占退回，最终计数 +1 再 -1 = 0
        所以只有调用方真正拿到了一段可用于 Pydantic 校验的原始 JSON 字符串，
        才会最终让额度计数器 + 1 保留（不 rollback）。"""
        telemetry = {**self._telemetry_base, "tag": telemetry_tag, "n_msgs": len(messages)}
        try:
            provider = self._build_provider()
        except (AIKeyEncryptionError, ValueError) as exc:
            _set_last_fail_code(FAIL_BUILD)
            log.warning(
                "ai_provider_build_fail tag=%s err_type=%s telemetry=%s",
                telemetry_tag,
                type(exc).__name__,
                telemetry,
            )
            return None

        # P5-07：真实 provider 才消耗额度；Mock 本地生成不计入
        is_mock_provider = isinstance(provider, MockAIProvider)
        rate_limiter_applied = False
        quota_user_id = user_id if user_id is not None else "__anon__"
        if (not is_mock_provider) and self._rate_limiter is not None:
            rl: AIRateLimiter = self._rate_limiter
            result: AIRateLimitResult = await rl.consume_or_reject(user_id=quota_user_id)
            if not result.allowed:
                _set_last_fail_code(FAIL_LOCAL_QUOTA)
                log.warning(
                    "ai_quota_exceeded tag=%s reason=%s user_id=%s "
                    "user_today=%s/%s global_today=%s/%s telemetry=%s",
                    telemetry_tag,
                    result.reason,
                    user_id,
                    result.user_today_used,
                    result.user_limit,
                    result.global_today_used,
                    result.global_limit,
                    telemetry,
                )
                # fail-open → 返回 None，让上层自动切规则引擎兜底
                return None
            rate_limiter_applied = True

        raw_out: str | None = None
        try:
            async with asyncio.timeout(timeout_ms / 1000.0):
                raw_out = await provider.chat(
                    messages=messages,
                    temperature=temperature,
                    timeout_ms=timeout_ms,
                )
        except TimeoutError:
            _set_last_fail_code(FAIL_TIMEOUT)
            log.warning(
                "ai_timeout tag=%s timeout_ms=%s telemetry=%s",
                telemetry_tag,
                timeout_ms,
                telemetry,
            )
        except httpx.TimeoutException as exc:
            _set_last_fail_code(FAIL_TIMEOUT)
            log.warning(
                "ai_httpx_timeout tag=%s err_type=%s telemetry=%s",
                telemetry_tag,
                type(exc).__name__,
                telemetry,
            )
        except httpx.HTTPStatusError as exc:
            code = _classify_http_status_code(exc.response.status_code)
            _set_last_fail_code(code)
            log.warning(
                "ai_http_status tag=%s status=%s classified=%s telemetry=%s",
                telemetry_tag,
                exc.response.status_code,
                code,
                telemetry,
            )
        except httpx.HTTPError as exc:
            # ConnectError / NetworkError 等非 2xx / 非超时类网络错误
            _set_last_fail_code(FAIL_UNKNOWN)
            log.warning(
                "ai_httpx_error tag=%s err_type=%s telemetry=%s",
                telemetry_tag,
                type(exc).__name__,
                telemetry,
            )
        except Exception as exc:
            _set_last_fail_code(FAIL_UNKNOWN)
            log.warning(
                "ai_call_fail tag=%s err_type=%s telemetry=%s",
                telemetry_tag,
                type(exc).__name__,
                telemetry,
            )

        # P5-05：失败 rollback 额度（成功才扣；任何异常/超时全退）
        if raw_out is None and rate_limiter_applied and self._rate_limiter is not None:
            try:
                await self._rate_limiter.rollback_consume(user_id=quota_user_id)
            except Exception as exc:
                log.warning(
                    "ai_quota_rollback_fail tag=%s err_type=%s telemetry=%s",
                    telemetry_tag,
                    type(exc).__name__,
                    telemetry,
                )
        return raw_out

    def _build_provider(self) -> AIProvider:
        """按 settings 选择具体 Provider。DeepSeek 分支里密钥只在这里局部变量存活。"""
        if self._provider_override is not None:
            return self._provider_override

        cfg = self._settings
        choice = cfg.ai_provider
        if choice == "mock":
            return MockAIProvider(
                mode=cfg.mock_ai_mode,
                seed=cfg.mock_ai_seed,
            )
        if choice in {"deepseek", "auto"}:
            # 解密密钥——只保留在局部变量里，不赋值给 self._xxx，防止意外泄漏
            key = resolve_encrypted_api_key(
                encrypted_value=cfg.ai_api_key,
                passphrase=cfg.ew_ai_key_passphrase or None,
                salt_override=cfg.ew_ai_salt or None,
            )
            if not key:
                # auto 模式允许未配置 → 优雅回退 mock
                if choice == "auto":
                    log.info("ai_provider_auto_fallback reason=missing_key")
                    return MockAIProvider(
                        mode=cfg.mock_ai_mode,
                        seed=cfg.mock_ai_seed,
                    )
                raise ValueError(
                    "AI_PROVIDER=deepseek 但 AI_API_KEY 未配置或加密密钥/口令不匹配；"
                    "请运行 backend/scripts/encrypt_ai_key.py 加密后填写 .env。"
                )
            return DeepSeekProvider(
                api_key=key,
                model=cfg.ai_model,
            )
        # 理论上 Literal 类型已收紧，这里只是兜底
        raise ValueError(f"未知的 AI_PROVIDER: {choice!r}")

    def _safe_validate_json(
        self,
        raw: str,
        schema: type[BaseModel],
        *,
        tag: str,
    ) -> Any | None:
        try:
            return schema.model_validate_json(raw)
        except ValidationError as exc:
            errors = exc.errors()
            # 只记录前两条 error type + loc，不 log 完整 raw（可能含 PII 或提示信息）
            log.warning(
                "ai_schema_validate_fail tag=%s type=%s first_errors=%s telemetry=%s",
                tag,
                schema.__name__,
                [
                    {"type": e.get("type"), "loc": e.get("loc")}
                    for e in errors[:2]
                ],
                self._telemetry_base,
            )
            return None

