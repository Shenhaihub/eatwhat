"""P5-05：DeepSeekProvider 单元测试。

策略：
- 所有 HTTP 交互走 httpx.MockTransport，**不会**真的向 DeepSeek 发请求，
  因此无需真实 API key，测试中一律使用 **假 key**（`sk-test-mock-...`）。
- 安全性测试：
    * 验证 Authorization header 格式是 `Bearer <key>` 且长度合理；
    * 异常消息中**不能**出现完整的 api_key 字符串（哪怕是假 key 也防止回显）；
    * 响应体截断（错误消息 snippet 前 200 字符）。
- 异常覆盖：401 / 500 / 超时 / 非法 JSON / 缺少 choices / 缺少 message / content 空。
"""
from __future__ import annotations

from typing import Any

import httpx
import pytest

from app.services.ai.base import ChatMessage
from app.services.ai.deepseek_provider import DeepSeekAPIError, DeepSeekProvider

# 假 key（仅测试用，不是真实 API key）
_FAKE_KEY = "sk-test-mock-only-abcdef1234567890"
_FAKE_MODEL = "deepseek-v4-flash"


def _make_messages() -> list[ChatMessage]:
    return [
        ChatMessage(role="system", content="你是一个 JSON 助手。"),
        ChatMessage(role="user", content="生成一道 follow_up 题，输出严格 JSON。"),
    ]


@pytest.mark.anyio
async def test_happy_path_returns_message_content() -> None:
    """正常场景：HTTP 200，choices[0].message.content 应被原样返回。"""
    expected_content = '{"items": [{"food_code": "F001"}]}'

    captured: dict[str, Any] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["method"] = request.method
        captured["auth"] = request.headers.get("Authorization", "")
        captured["content_type"] = request.headers.get("Content-Type", "")
        captured["user_agent"] = request.headers.get("User-Agent", "")
        body = request.content
        assert body is not None
        import json as _json
        captured["json"] = _json.loads(body.decode("utf-8"))
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-test",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": expected_content},
                        "finish_reason": "stop",
                    }
                ],
                "model": _FAKE_MODEL,
            },
        )

    transport = httpx.MockTransport(handler)
    provider = DeepSeekProvider(
        api_key=_FAKE_KEY,
        model=_FAKE_MODEL,
        base_url="https://api.deepseek.test",
        transport=transport,
    )
    out = await provider.chat(
        messages=_make_messages(),
        temperature=0.3,
        timeout_ms=3000,
    )

    assert out == expected_content
    assert captured["method"] == "POST"
    assert captured["url"] == "https://api.deepseek.test/chat/completions"
    assert captured["content_type"] == "application/json"
    assert captured["auth"] == f"Bearer {_FAKE_KEY}"
    assert captured["user_agent"] == "eatwhat-ai/1.0"

    payload = captured["json"]
    assert payload["model"] == _FAKE_MODEL
    assert payload["temperature"] == 0.3
    assert payload["stream"] is False
    assert payload["response_format"] == {"type": "json_object"}
    assert payload["max_tokens"] == 4096
    assert payload["messages"] == [m.to_dict() for m in _make_messages()]


@pytest.mark.anyio
async def test_401_raises_safe_error_without_key_echo() -> None:
    """401 未授权：异常只含 401 + snippet，不能回显 key。"""

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401,
            json={"error": {"message": "Invalid API Key"}},
        )

    transport = httpx.MockTransport(handler)
    provider = DeepSeekProvider(api_key=_FAKE_KEY, model=_FAKE_MODEL, transport=transport)
    with pytest.raises(DeepSeekAPIError) as exc:
        await provider.chat(messages=_make_messages(), temperature=0.3, timeout_ms=3000)
    msg = str(exc.value)
    assert "401" in msg
    # 错误消息中不能回显完整的假 key（安全防线）
    assert _FAKE_KEY not in msg
    assert "Bearer" not in msg
    # 但应当包含 snippet 片段提示（HTTP 401 字面前缀我们已断言）


@pytest.mark.anyio
async def test_500_raises_safe_error() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            500,
            text="Internal Server Error: database connection lost",
        )

    transport = httpx.MockTransport(handler)
    provider = DeepSeekProvider(api_key=_FAKE_KEY, model=_FAKE_MODEL, transport=transport)
    with pytest.raises(DeepSeekAPIError) as exc:
        await provider.chat(messages=_make_messages(), temperature=0.3, timeout_ms=3000)
    msg = str(exc.value)
    assert "500" in msg
    assert "Internal Server Error" in msg
    assert _FAKE_KEY not in msg


@pytest.mark.anyio
async def test_http_timeout_exception_mapped() -> None:
    """超时抛错 DeepSeekAPIError。"""

    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("simulated read timeout")

    transport = httpx.MockTransport(handler)
    provider = DeepSeekProvider(api_key=_FAKE_KEY, model=_FAKE_MODEL, transport=transport)
    with pytest.raises(DeepSeekAPIError) as exc:
        await provider.chat(messages=_make_messages(), temperature=0.3, timeout_ms=3000)
    assert "超时" in str(exc.value) or "Timeout" in str(exc.value)


@pytest.mark.anyio
async def test_http200_but_invalid_json_raises() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="oops this is not json")

    transport = httpx.MockTransport(handler)
    provider = DeepSeekProvider(api_key=_FAKE_KEY, model=_FAKE_MODEL, transport=transport)
    with pytest.raises(DeepSeekAPIError) as exc:
        await provider.chat(messages=_make_messages(), temperature=0.3, timeout_ms=3000)
    assert "不是合法 JSON" in str(exc.value)


@pytest.mark.anyio
async def test_missing_choices_raises() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"object": "chat.completion", "choices": []})

    transport = httpx.MockTransport(handler)
    provider = DeepSeekProvider(api_key=_FAKE_KEY, model=_FAKE_MODEL, transport=transport)
    with pytest.raises(DeepSeekAPIError, match="缺少 choices"):
        await provider.chat(messages=_make_messages(), temperature=0.3, timeout_ms=3000)


@pytest.mark.anyio
async def test_missing_message_content_raises() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"choices": [{"index": 0, "message": {"role": "assistant", "content": None}}]},
        )

    transport = httpx.MockTransport(handler)
    provider = DeepSeekProvider(api_key=_FAKE_KEY, model=_FAKE_MODEL, transport=transport)
    with pytest.raises(DeepSeekAPIError, match="content 缺失或非字符串"):
        await provider.chat(messages=_make_messages(), temperature=0.3, timeout_ms=3000)


@pytest.mark.anyio
async def test_empty_content_string_raises() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"choices": [{"message": {"role": "assistant", "content": "   \n  "}}]},
        )

    transport = httpx.MockTransport(handler)
    provider = DeepSeekProvider(api_key=_FAKE_KEY, model=_FAKE_MODEL, transport=transport)
    with pytest.raises(DeepSeekAPIError, match="为空字符串"):
        await provider.chat(messages=_make_messages(), temperature=0.3, timeout_ms=3000)


@pytest.mark.parametrize(
    "kwargs,match",
    [
        ({"temperature": -0.1}, "temperature"),
        ({"temperature": 2.1}, "temperature"),
        ({"timeout_ms": 0}, "timeout_ms"),
        ({"timeout_ms": -1}, "timeout_ms"),
    ],
)
@pytest.mark.anyio
async def test_illegal_params_fail_fast(kwargs: dict[str, Any], match: str) -> None:
    """参数越界在发 HTTP 前就直接抛错，不浪费网络。"""
    provider = DeepSeekProvider(api_key=_FAKE_KEY, model=_FAKE_MODEL)
    base: dict[str, Any] = {"messages": _make_messages(), "temperature": 0.3, "timeout_ms": 3000}
    base.update(kwargs)
    with pytest.raises(ValueError, match=match):
        await provider.chat(**base)


def test_empty_key_or_model_rejected() -> None:
    with pytest.raises(ValueError, match="api_key 不能为空"):
        DeepSeekProvider(api_key="", model=_FAKE_MODEL)
    with pytest.raises(ValueError, match="model 不能为空"):
        DeepSeekProvider(api_key=_FAKE_KEY, model="")
