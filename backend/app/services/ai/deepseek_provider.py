"""P5-05 DeepSeek V4 Flash Provider 真实 HTTP 实现。

对接：DeepSeek OpenAI 兼容端点 ``POST https://api.deepseek.com/chat/completions``。
契约完全对齐 `AIProvider` Protocol：只接受 `list[ChatMessage]` + `temperature` +
`timeout_ms`，返回 AI 的原始文本（由上层 ChatService 负责 JSON 校验/越界检测）。

安全底线（违反 = 高危泄露）：
    1. `api_key` 参数只在 HTTP 请求头 Authorization 中使用，**绝不**：
       - 打印 / 日志 / 异常消息 / traceback
       - 持久化（settings 单例、缓存、文件）
       - 拼接进 URL query string
    2. 异常消息只包含 HTTP status + 前 200 字符的 response body，不包含任何 header 值。
    3. provider 实例的私有 `_api_key` 字段是高敏数据。测试中请使用
       ``httpx.MockTransport`` 验证请求头是否正确包含 Bearer 即可，不要断言
       key 的字面量字符串完整值。

超时防御（双重保险）：
    - ``httpx.Timeout(connect, read, write, pool)`` — HTTP 层硬卡
    - 上层 ``ChatService._raw_chat_catch_all`` 再套一层 ``asyncio.timeout()``，
      防止 httpx 内部依赖链路出现遗漏。
"""
from __future__ import annotations

from typing import Any

import httpx

from .base import AIProvider, ChatMessage


class DeepSeekAPIError(RuntimeError):
    """DeepSeek 非 2xx、HTTP 失败、或响应 schema 不符时抛出。

    文案中严禁包含 api_key、Authorization 头或任何敏感信息。"""


class DeepSeekProvider(AIProvider):
    """DeepSeek V4 Flash 真实 Provider（httpx.AsyncClient 直连官方网关）。"""

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "deepseek-v4-flash",
        base_url: str = "https://api.deepseek.com",
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("DeepSeekProvider: api_key 不能为空。")
        if not model:
            raise ValueError("DeepSeekProvider: model 不能为空。")
        self._api_key = api_key
        self._model = model
        self._base_url = base_url.rstrip("/")
        # 允许测试注入 MockTransport，生产环境默认 None 让 httpx 自己选
        self._transport = transport

    async def chat(
        self,
        *,
        messages: list[ChatMessage],
        temperature: float,
        timeout_ms: int,
    ) -> str:
        """调用 DeepSeek /chat/completions，返回 message.content。

        异常：
            - 任何网络/HTTP 非 2xx / 响应 schema 异常 → 抛 DeepSeekAPIError
            - 其他异常（比如参数异常）直接向上抛出（上层 ChatService 统一 catch-all 后回退）
        """
        if timeout_ms <= 0:
            raise ValueError(f"timeout_ms 必须正数，当前={timeout_ms}")
        if temperature < 0.0 or temperature > 2.0:
            # DeepSeek 官方允许 [0, 2]；此处做 fail-fast，避免 gateway 静默改值
            raise ValueError(f"temperature 必须在 [0.0, 2.0]，当前={temperature}")

        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [m.to_dict() for m in messages],
            "temperature": float(temperature),
            "stream": False,
            # 期望上层 prompt 里强制要求 JSON 输出；response_format 作为双重保险。
            "response_format": {"type": "json_object"},
            # 单轮不要太长，避免烧 token；最多 4k tokens（足够 5 条 food_code + reasons）
            "max_tokens": 4096,
        }

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "eatwhat-ai/1.0",
        }

        timeout_s = float(timeout_ms) / 1000.0
        http_timeout = httpx.Timeout(
            connect=5.0,
            read=timeout_s,
            write=5.0,
            pool=5.0,
        )

        client_kwargs: dict[str, Any] = {
            "timeout": http_timeout,
            "limits": httpx.Limits(max_keepalive_connections=2, max_connections=4),
        }
        if self._transport is not None:
            client_kwargs["transport"] = self._transport

        try:
            async with httpx.AsyncClient(**client_kwargs) as client:
                resp = await client.post(
                    f"{self._base_url}/chat/completions",
                    json=payload,
                    headers=headers,
                )
        except httpx.TimeoutException as exc:
            raise DeepSeekAPIError(
                f"DeepSeek HTTP 超时：type={type(exc).__name__} timeout_ms={timeout_ms}"
            ) from exc
        except httpx.HTTPError as exc:
            # httpx 连接错误、DNS 失败、SSL 错误等
            raise DeepSeekAPIError(
                f"DeepSeek HTTP 失败：type={type(exc).__name__} msg={exc.__class__.__name__}"
            ) from exc

        if resp.status_code != 200:
            # 错误体截断到 200 字符，避免泄漏 API 错误详情中可能夹带的敏感信息（虽然官方错误一般不含）
            body_snippet = resp.text[:200].replace("\n", " ")
            raise DeepSeekAPIError(
                f"DeepSeek HTTP {resp.status_code}（非 200）响应片段：{body_snippet}"
            )

        try:
            data = resp.json()
        except ValueError as exc:
            # JSON 解析失败
            body_snippet = resp.text[:200].replace("\n", " ")
            raise DeepSeekAPIError(
                f"DeepSeek HTTP 200 但响应不是合法 JSON：片段 {body_snippet}"
            ) from exc

        if not isinstance(data, dict):
            raise DeepSeekAPIError("DeepSeek 响应不是 JSON 对象。")

        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            raise DeepSeekAPIError("DeepSeek 响应缺少 choices[].")

        first = choices[0]
        if not isinstance(first, dict):
            raise DeepSeekAPIError("DeepSeek choices[0] 不是 JSON 对象。")

        message = first.get("message")
        if not isinstance(message, dict):
            raise DeepSeekAPIError("DeepSeek choices[0].message 缺失或不是对象。")

        content = message.get("content")
        if not isinstance(content, str):
            # content=None 或非字符串（极端情况）→ 视为不可信输出，抛错让上层回退
            raise DeepSeekAPIError("DeepSeek message.content 缺失或非字符串。")

        content_stripped = content.strip()
        if not content_stripped:
            raise DeepSeekAPIError("DeepSeek message.content 为空字符串。")

        # 安全：content 中不会包含密钥，但为保险起见，确保后续使用的是局部副本，
        # 不把原始 data / message 对象长时间留用（避免 heap inspection 泄露更多信息）。
        del data, message, choices, first, content
        return content_stripped
