"""P5-03A AI Provider 抽象契约（Protocol）。

设计原则：
    1. 接口保持最小。每个 Provider 只需要实现一个 `chat` 异步方法，返回原始字符串。
    2. 出参是纯字符串（模型原始文本），不在这里做 JSON 解析——解析/校验放在 ChatService
       里用 Pydantic `model_validate_json` 强约束（失败即视为越界输出，回退规则）。
    3. Provider 不负责"回退规则逻辑"。回退是业务责任，放在 ChatService 上层。
    4. 禁止在 Provider 中访问 settings 单例。运行时参数（key、model、timeout）由调用方
       显式注入（方便测试 monkeypatch，也避免某些 provider 没有对应 settings 字段）。
"""
from __future__ import annotations

from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

ChatRole = Literal["system", "user", "assistant"]


class ChatMessage(BaseModel):
    """OpenAI 兼容消息结构。

    用 Pydantic 而不是 dict 子类，保证 mypy 类型安全；
    需要传给 httpx 时调用 to_dict()（保证 JSON 序列化与官方 100% 对齐）。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")
    role: ChatRole
    content: str

    def to_dict(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content}


@runtime_checkable
class AIProvider(Protocol):
    """所有 AI 提供者都必须实现的统一接口。"""

    async def chat(
        self,
        *,
        messages: list[ChatMessage],
        temperature: float,
        timeout_ms: int,
    ) -> str:
        """向模型发消息，返回 assistant 的原始文本。

        参数：
            messages: 有序会话历史。
            temperature: 0.0 = 确定性；1.0 = 最大创造性。推荐追问用 0.5，
                         最终生成用 0.3（保证 food_code 稳定落在字典内）。
            timeout_ms: 单次 HTTP/模型调用总硬超时（毫秒）。由 ChatService 执行，
                        Provider 也应尽力遵守其内部 HTTP client 超时。

        返回：
            模型输出的原始字符串（通常是 JSON 文本；结构由上层校验）。

        异常语义：
            任何异常（网络错误、HTTP 非 2xx、模型服务限流、超时未在内部消化等）
            都应直接向上 re-raise。ChatService 会统一捕获并回退规则。
        """
        ...
