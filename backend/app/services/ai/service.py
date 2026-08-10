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

用法示例（推荐路由 P5-04 接入）：
    service = ChatService(settings=settings)
    ai_result: FinalRecommendationOutput | None = (
        await service.generate_final_recommendation(profile=..., history=...)
    )
    if ai_result is None:
        # 回退规则引擎（真源）
        candidates = rule_engine.generate(profile, history, k=5)
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ValidationError

from app.core.config import Settings
from app.core.encryption import AIKeyEncryptionError, resolve_encrypted_api_key
from app.schemas.ai import FinalRecommendationOutput, FollowUpQuestionOutput

from .base import AIProvider, ChatMessage
from .deepseek_provider import DeepSeekProvider
from .mock_provider import MockAIProvider

log = logging.getLogger("app.services.ai.service")

DEFAULT_CHAT_TIMEOUT_MS = 8_000
DEFAULT_FINAL_TEMPERATURE = 0.3
DEFAULT_FOLLOW_UP_TEMPERATURE = 0.5


class ChatService:
    """统一门面。不要在业务代码里直接引用 mock_provider / deepseek_provider。"""

    def __init__(
        self,
        *,
        settings: Settings,
        provider_override: AIProvider | None = None,
        default_timeout_ms: int = DEFAULT_CHAT_TIMEOUT_MS,
        extra_telemetry: Mapping[str, Any] | None = None,
    ) -> None:
        self._settings = settings
        self._default_timeout_ms = int(default_timeout_ms)
        self._telemetry_base: dict[str, Any] = dict(extra_telemetry or {})
        # 允许测试/演示注入 mock 实例（例如 out_of_bounds_food_code 模式）
        self._provider_override = provider_override

    # ============== 对外：动态追问生成 ==============
    async def generate_follow_up(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        round_index_1based: int,
        timeout_ms: int | None = None,
    ) -> FollowUpQuestionOutput | None:
        """生成一道最多 3 轮的追问题。失败返回 None，业务层走默认追问/直接最终生成。"""
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
        )
        if raw is None:
            return None
        return self._safe_validate_json(raw, FollowUpQuestionOutput, tag="follow_up")

    # ============== 对外：最终 5 候选生成 ==============
    async def generate_final_recommendation(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        timeout_ms: int | None = None,
    ) -> FinalRecommendationOutput | None:
        """生成最终 5 候选。失败返回 None，业务层回退规则引擎。"""
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
        )
        if raw is None:
            return None
        return self._safe_validate_json(raw, FinalRecommendationOutput, tag="final_rec")

    # ============== 内部实现 ==============
    async def _raw_chat_catch_all(
        self,
        *,
        messages: list[ChatMessage],
        temperature: float,
        timeout_ms: int,
        telemetry_tag: str,
    ) -> str | None:
        """实际调用 Provider，所有异常 → 返回 None。"""
        telemetry = {**self._telemetry_base, "tag": telemetry_tag, "n_msgs": len(messages)}
        try:
            provider = self._build_provider()
        except (AIKeyEncryptionError, ValueError) as exc:
            log.warning(
                "ai_provider_build_fail tag=%s err_type=%s telemetry=%s",
                telemetry_tag,
                type(exc).__name__,
                telemetry,
            )
            return None

        try:
            async with asyncio.timeout(timeout_ms / 1000.0):
                return await provider.chat(
                    messages=messages,
                    temperature=temperature,
                    timeout_ms=timeout_ms,
                )
        except TimeoutError:
            log.warning(
                "ai_timeout tag=%s timeout_ms=%s telemetry=%s",
                telemetry_tag,
                timeout_ms,
                telemetry,
            )
            return None
        except Exception as exc:  # noqa: BLE001 - 设计上，任何 AI 失败都不能把推荐打挂
            log.warning(
                "ai_call_fail tag=%s err_type=%s telemetry=%s",
                telemetry_tag,
                type(exc).__name__,
                telemetry,
            )
            return None

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
