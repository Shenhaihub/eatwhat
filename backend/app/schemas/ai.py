"""P5 AI 输出 schema（服务端强校验边界，AI 输出是"建议"，最终真源是规则引擎）。

所有模型都使用 ``extra="forbid"``，AI 吐出的未约定字段一律视为"越界输出"，
ChatService 会判定失败回退规则（G-12 可追溯 & G-08 不空保障）。
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.repositories.food_dictionary import (
    get_food_dictionary_repository,
)

# ---------- 动态追问（P5-02）输出 ----------

FOLLOW_UP_QUESTION_ID_MAX_LEN = 32
FOLLOW_UP_TITLE_MAX_LEN = 64
FOLLOW_UP_OPTION_TEXT_MAX_LEN = 32
FOLLOW_UP_OPTION_VALUE_MAX_LEN = 32
FOLLOW_UP_PURPOSE_MAX_LEN = 80
FOLLOW_UP_OPTIONS_MIN_COUNT = 2
FOLLOW_UP_OPTIONS_MAX_COUNT = 6


class FollowUpOption(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: str = Field(..., max_length=FOLLOW_UP_OPTION_VALUE_MAX_LEN)
    label_zh: str = Field(..., max_length=FOLLOW_UP_OPTION_TEXT_MAX_LEN)


class FollowUpQuestionOutput(BaseModel):
    """AI 生成的下一题。"""

    model_config = ConfigDict(extra="forbid")

    # 会话内稳定 id（ai_fu_<3 位序号>_<slug>）——最多 3 轮，所以最长序号是 003
    question_id: str = Field(..., max_length=FOLLOW_UP_QUESTION_ID_MAX_LEN,
                            pattern=r"^ai_fu_00[1-3]_[a-z0-9_]{1,16}$")

    title_zh: str = Field(..., max_length=FOLLOW_UP_TITLE_MAX_LEN, min_length=4)

    # 单选就够，目前 P5 不做多选（避免 UI 复杂度 & 处理 & 规则引擎复杂化）
    options: list[FollowUpOption] = Field(
        ...,
        min_length=FOLLOW_UP_OPTIONS_MIN_COUNT,
        max_length=FOLLOW_UP_OPTIONS_MAX_COUNT,
    )

    # 判别维度 / 目的：告诉服务端与用户这道题在补哪个信息维度
    purpose_zh: str = Field(..., max_length=FOLLOW_UP_PURPOSE_MAX_LEN, min_length=2)

    # 信息充足时是否提前终止（true = 不再追问，直接进入最终推荐生成）
    should_continue: bool = Field(
        ...,
        description="True=继续问下一题/生成推荐；False=信息已充分，直接生成最终 5 候选",
    )

    @field_validator("options")
    @classmethod
    def _options_values_unique(cls, v: list[FollowUpOption]) -> list[FollowUpOption]:
        values = [o.value for o in v]
        if len(set(values)) != len(values):
            raise ValueError("follow_up option value 不能重复")
        return v


# ---------- 最终推荐（P5-04/P5-04A）输出 ----------

FINAL_REASON_MAX_LEN = 200
FINAL_FOOD_COUNT = 5


class FinalFoodCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    food_code: str = Field(..., max_length=64)
    # 中文推荐理由摘要（1-2 句，不超过 200 字）
    reason_zh: str = Field(..., min_length=4, max_length=FINAL_REASON_MAX_LEN)

    # （可选）命中的偏好标签，用来后续 UI 高亮"为什么推荐给你"
    # —— 统一用字符串，避免枚举/str 混用时的 Pydantic 强类型问题
    matched_tags: list[str] = Field(
        default_factory=list,
        description="命中的偏好标签：菜系/预算/食性等，后续 UI 用作'为什么推荐给你'的高亮展示",
    )


class FinalRecommendationOutput(BaseModel):
    """AI 生成的 5 个最终候选。"""

    model_config = ConfigDict(extra="forbid")

    # Top5，顺序即 priority 1→5（下标 0 = priority 1）
    candidates: list[FinalFoodCandidate] = Field(
        ...,
        min_length=FINAL_FOOD_COUNT,
        max_length=FINAL_FOOD_COUNT,
    )

    @field_validator("candidates")
    @classmethod
    def _all_food_codes_in_dictionary_and_unique(
        cls, v: list[FinalFoodCandidate]
    ) -> list[FinalFoodCandidate]:
        """服务端强校验：food_code 必须全部在启用字典中且互不相同。

        这是 P5 安全底线的一部分：AI 无权"发明"新菜，所有候选都必须是食物真源
        （food_dictionary）内启用过的条目，否则视为越界输出 → ChatService 判失败
        回退规则引擎。这样即使模型被 prompt injection 也不会把越界数据送进 UI/DB。
        """
        repo = get_food_dictionary_repository()
        codes: list[str] = []
        for c in v:
            if not repo.contains_enabled(c.food_code):
                raise ValueError(
                    f"AI 输出的 food_code 不在启用字典中：{c.food_code!r}，已拒绝并回退规则"
                )
            codes.append(c.food_code)
        if len(set(codes)) != len(codes):
            raise ValueError("AI 输出的 5 个候选 food_code 存在重复，已拒绝并回退规则")
        return v
