"""P5-03 MockAIProvider：四模式可控输出 + seed 参数化。

测试/演示友好的设计：
    - normal：               返回合法 JSON（追问或最终推荐，按 messages 里的标记判断）
    - slow：                 延迟 9 秒再返回 normal（触发 ChatService 8000ms 超时）
    - invalid_json：         返回损坏 JSON 字符串（或半损坏）→ ChatService model_validate_json 失败
    - out_of_bounds_food_code：最终推荐模式下 5 条 food_code 其中一条是字典外的值

关于 MEM-024 "反固定首候选"：
    MockAIProvider.normal 模式的最终推荐使用 seed 参数化排序：
    seed=0、1、2、3 会产生 4 组彼此不同的 Top5 顺序（单元测试用 pytest.param 覆盖）。
    seed 默认从 settings.mock_ai_seed 读取，也可在构造时注入。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import random
from typing import Literal

from app.repositories.food_dictionary import (
    get_food_dictionary_repository,
)
from app.schemas.ai import (
    FinalFoodCandidate,
    FinalRecommendationOutput,
    FollowUpOption,
    FollowUpQuestionOutput,
)

from .base import AIProvider, ChatMessage

log = logging.getLogger("app.services.ai.mock")

MockMode = Literal["normal", "slow", "invalid_json", "out_of_bounds_food_code"]

SLOW_MODE_DELAY_SECONDS = 16  # "慢"语义：明显慢于正常响应；是否触发超时取决于调用方传入的 timeout_ms

# 追问模板：3 轮预置题（Mock 不用真的理解，保证结构合法即可；真实 DeepSeek 才会动态生成）
FOLLOW_UP_TEMPLATES: list[FollowUpQuestionOutput] = [
    FollowUpQuestionOutput(
        question_id="ai_fu_001_cuisine",
        title_zh="今天想吃哪种菜系风格？",
        options=[
            FollowUpOption(value="chinese_north", label_zh="北方家常（面/粥/饼/炖菜）"),
            FollowUpOption(value="chinese_south", label_zh="南方家常（米饭/小炒/汤）"),
            FollowUpOption(value="western", label_zh="西式（汉堡/三明治/披萨/沙拉）"),
            FollowUpOption(value="japanese_korean", label_zh="日韩（寿司/冷面/炸鸡）"),
            FollowUpOption(value="spicy", label_zh="只要辣（川菜/麻辣烫/烧烤）"),
        ],
        purpose_zh="补充菜系偏好维度，避免推荐不随地域偏好变化",
        should_continue=True,
    ),
    FollowUpQuestionOutput(
        question_id="ai_fu_002_flavor",
        title_zh="今天的口味更偏向？",
        options=[
            FollowUpOption(value="light", label_zh="清淡/少盐少油"),
            FollowUpOption(value="savory", label_zh="咸香浓郁"),
            FollowUpOption(value="sour_spicy", label_zh="酸辣/开胃"),
            FollowUpOption(value="sweet", label_zh="有一点甜口（番茄/照烧/糖醋）"),
        ],
        purpose_zh="补充口味偏好维度，让后续 5 候选理由更贴近心情",
        should_continue=True,
    ),
    FollowUpQuestionOutput(
        question_id="ai_fu_003_vibe",
        title_zh="这一顿更看重什么？",
        options=[
            FollowUpOption(value="speed", label_zh="出餐快/不排队（刚需优先）"),
            FollowUpOption(value="fullness", label_zh="吃得饱，能量足"),
            FollowUpOption(value="balanced", label_zh="均衡一点，不油腻"),
            FollowUpOption(value="treat_yourself", label_zh="犒劳自己，好吃最重要"),
        ],
        purpose_zh="补充用餐氛围维度，影响推荐理由的情感语气",
        should_continue=False,  # 第 3 题默认信息充分，之后进入最终生成
    ),
]


def _seed_shuffled_five(*, seed: int) -> list[str]:
    """从启用字典选择 5 条食物 code，按 seed 扰动产生稳定排序。

    MEM-024：不同 seed 必须产生不同排序（及不同的首候选）。
    """
    repo = get_food_dictionary_repository()
    codes = [it.food_code for it in repo.list_enabled()]
    rng = random.Random(hashlib.md5(str(seed).encode("utf-8")).digest()[:8])
    rng.shuffle(codes)
    return codes[:5]


class MockAIProvider(AIProvider):
    """Mock AI Provider：四种模式 + seed 参数化。"""

    def __init__(
        self,
        *,
        mode: MockMode = "normal",
        seed: int = 0,
        slow_delay_seconds: int = SLOW_MODE_DELAY_SECONDS,
    ) -> None:
        self.mode: MockMode = mode
        self.seed = int(seed)
        self.slow_delay_seconds = int(slow_delay_seconds)

    # ============== AIProvider Protocol 实现 ==============
    async def chat(
        self,
        *,
        messages: list[ChatMessage],
        temperature: float,
        timeout_ms: int,
    ) -> str:
        if self.mode == "slow":
            await asyncio.sleep(self.slow_delay_seconds)

        is_final = _is_final_generation_request(messages)
        round_index = _detect_follow_up_round(messages)  # 0-based（0 = 第 1 题）

        if self.mode == "invalid_json":
            return _build_invalid_json(is_final=is_final)

        if self.mode == "out_of_bounds_food_code":
            if is_final:
                return _build_out_of_bounds_final_json(seed=self.seed)
            # 追问模式没有 food_code 越界一说，直接走 normal
            self.mode = "normal"

        return await self._chat_normal(is_final=is_final, round_index=round_index)

    # ============== 内部方法 ==============
    async def _chat_normal(self, *, is_final: bool, round_index: int) -> str:
        if is_final:
            data = _build_normal_final_output(seed=self.seed)
            return data.model_dump_json(ensure_ascii=False)
        # 追问：最多 3 轮；超出则返回 should_continue=False 的最后一题占位（等价于提前结束）
        round_index = max(round_index, 0)
        if round_index >= len(FOLLOW_UP_TEMPLATES):
            last = FOLLOW_UP_TEMPLATES[-1].model_copy(update={"should_continue": False})
            return last.model_dump_json(ensure_ascii=False)
        return FOLLOW_UP_TEMPLATES[round_index].model_dump_json(ensure_ascii=False)


# ============== 模块级 helper（纯函数，方便单测）==============

_FINAL_MARKER = "[FINAL_GENERATION]"
_ROUND_PREFIX = "[ROUND_"  # 示例：[ROUND_1] 表示第一轮追问


def _is_final_generation_request(messages: list[ChatMessage]) -> bool:
    if not messages:
        return False
    # 判断最新 user 消息 或 system prompt 里是否包含 FINAL 标记
    for m in reversed(messages):
        if (m.role == "user" or m.role == "system") and _FINAL_MARKER in m.content:
            return True
    return False


def _detect_follow_up_round(messages: list[ChatMessage]) -> int:
    """返回 0-based 轮次索引（0 = 第一轮追问）。找不到则返回 0。"""
    for m in reversed(messages):
        if m.role != "user" and m.role != "system":
            continue
        idx = m.content.find(_ROUND_PREFIX)
        if idx >= 0:
            tail = m.content[idx + len(_ROUND_PREFIX):]
            # 读数字，遇到非数字停止
            digits = []
            for ch in tail:
                if ch.isdigit():
                    digits.append(ch)
                else:
                    break
            if digits:
                try:
                    n = int("".join(digits))
                    return max(n - 1, 0)
                except ValueError:
                    return 0
    return 0


def _build_invalid_json(*, is_final: bool) -> str:
    if is_final:
        # 半损坏：缺少 candidates 闭合，有 { 但无 }
        return '{"candidates": [{"food_code": "xiaowan_cai", "reason_zh": "今天比较适合", "matched_tags":[]}, {"food_code": "beef_noodle", "reason_zh": "热乎的"'
    return '{"question_id": "broken", "title_zh": "半损坏'


def _build_normal_final_output(*, seed: int) -> FinalRecommendationOutput:
    codes = _seed_shuffled_five(seed=seed)
    repo = get_food_dictionary_repository()
    candidates: list[FinalFoodCandidate] = []
    for i, code in enumerate(codes):
        item = repo.require(code)
        # 兼容：cuisine_groups 是 list[CuisineGroup]（枚举）；supported_budget_tiers 是 list[BudgetTier]
        # 都转成字符串给 matched_tags（Pydantic 会自动把枚举 value 写入 JSON）
        tags: list[str] = [str(x) for x in list(item.cuisine_groups)[:2]]
        tags.extend(str(x) for x in list(item.supported_budget_tiers)[:1])
        candidates.append(
            FinalFoodCandidate(
                food_code=code,
                reason_zh=(
                    f"根据你选择的风味（Mock seed={seed}，第 {i + 1} 位）"
                    f"{item.display_name_zh} 是最近匹配的均衡选项。"
                ),
                matched_tags=tags,
            )
        )
    return FinalRecommendationOutput(candidates=candidates)


def _build_out_of_bounds_final_json(*, seed: int) -> str:
    """构造合法 JSON 结构，但第 3 条 food_code 是字典外的值。"""
    base = _build_normal_final_output(seed=seed).model_dump(mode="json")
    # 保证长度仍为 5，替换索引 2
    base["candidates"][2]["food_code"] = "definitely_not_in_dictionary_xyz"
    return json.dumps(base, ensure_ascii=False)
