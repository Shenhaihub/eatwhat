"""P5-03 DeepSeek V4 Flash Provider 占位实现（Live 请求在 P5-04 完成）。

为什么先做占位？
    - 让 ChatService 的 Provider 选择逻辑（ai_provider=auto/deepseek 时的分支）可以完整
      跑通 CI，不出现"ImportError：deepseek_provider 不存在"
    - 所有 Live HTTP 调用逻辑（httpx、endpoint、Authorization 头、error 映射、
      token 估算）集中放到 P5-04 统一实现，避免和 P5-03 的契约/回退逻辑混同。
    - 占位的实现保证了：目前如果把 AI_PROVIDER=deepseek 打开，会返回明确错误
      给 ChatService（ChatService 捕获后回退规则引擎）。
"""
from __future__ import annotations

from .base import AIProvider, ChatMessage


class DeepSeekProvider(AIProvider):
    """DeepSeek V4 Flash Provider（P5-04 才完成真实 HTTP 调用）。"""

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "deepseek-v4-flash",
        base_url: str = "https://api.deepseek.com/v1/chat/completions",
    ) -> None:
        # 只存储参数，不做网络访问。
        self._api_key = api_key
        self._model = model
        self._base_url = base_url

    async def chat(
        self,
        *,
        messages: list[ChatMessage],
        temperature: float,
        timeout_ms: int,
    ) -> str:
        """占位：抛出 NotImplementedError，由 ChatService 捕获后回退规则。"""
        msg = (
            "DeepSeekProvider 真实 HTTP 调用尚未实现（计划在 P5-04 接 live）。"
            f"模型={self._model!r} 消息数={len(messages)} T={temperature:.2f} "
            f"timeout_ms={timeout_ms}。当前回退规则引擎处理。"
        )
        raise NotImplementedError(msg)
