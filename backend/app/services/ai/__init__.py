"""P5-03 AI 服务统一出口。

模块职责（单一职责 & 方便 monkeypatch 测试）：
    - base:           抽象契约（Protocol + 入参/出参轻量类型）
    - mock_provider:  MockAIProvider（四种可参数化模式，纯内存/无网络）
    - deepseek_provider: DeepSeek V4 Flash 真实 provider（依赖 httpx，P5-04 实现）
    - service:        ChatService 门面（按 settings.ai_provider 选择 provider、
                      超时控制、schema model_validate_json 校验、异常统一回退 None）

禁止业务代码直接 import 某一个具体 provider，一律走 service 层。
"""
from __future__ import annotations
