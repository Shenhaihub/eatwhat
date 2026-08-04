"""问卷状态机专用 schema：问题库、选项、展示条件、重算结果。

P2-03 专用。所有类均 extra="forbid"，与 P2 整体契约一致。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# 入口意图：必须与 P2-01 的 ENTRY_POINT_INTENT_VALUES 一致（字符串形式），
# 避免在 schema 层造成循环依赖：QuestionnaireAnswers 里已定义八维字段，这里不再重复。
ENTRY_INTENT_VALUES: tuple[str, ...] = (
    "ai_recommend",
    "community",
    "activity",
    "user_choice",
)

# P2 状态机显式支持的七维字段（前 6 个）。
# max_distance_m 与 ai_follow_up_answers 在 P3/P5 再纳入问卷，当前不强制、不出题。
MAPPABLE_DIMENSION_FIELDS: tuple[str, ...] = (
    "meal_period",
    "appetite",
    "avoidances",
    "tastes",
    "budget",
    "explicit_food_preference",
)


class QuestionOption(BaseModel):
    model_config = ConfigDict(extra="forbid")

    option_id: str = Field(..., pattern=r"^[a-z0-9_]{2,32}$")
    label_zh: str = Field(..., min_length=1, max_length=32)
    value: str = Field(..., min_length=1, max_length=32)


class DisplayCondition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operator: Literal["equals", "not_equals", "in", "not_in", "always_true"]
    # 对 single_choice 比对单个值；对 multi_choice 比对"是否包含任一"
    operand_question_id: str | None = Field(
        default=None, pattern=r"^[a-z0-9_]{2,40}$"
    )
    operand_value: Any = Field(default=None)


class DimensionMapping(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field_name: Literal[
        "meal_period",
        "appetite",
        "avoidances",
        "tastes",
        "budget",
        "explicit_food_preference",
    ]
    is_array: bool = False
    # 所有 Q 的 value 都是枚举的 .value 字面量（例如 "lunch" 对应 MealPeriod.LUNCH）
    value_is_enum_value: bool = True


class QuestionBankItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_id: str = Field(..., pattern=r"^[a-z0-9_]{2,40}$")
    title_zh: str = Field(..., min_length=1, max_length=64)  # G-09 字段长度软约束
    question_type: Literal["single_choice", "multi_choice"]
    options: list[QuestionOption] = Field(..., min_length=1, max_length=16)
    maps_to: DimensionMapping
    display_if: DisplayCondition | None = Field(default=None)
    # 哪些入口意图下，本题是"完成判定的必要条件"；空列表=所有入口都不需答
    required_for_entry_intents: list[str] = Field(default_factory=list)


class QuestionBankV1(BaseModel):
    """整个问题库 JSON 的外层。"""

    model_config = ConfigDict(extra="forbid")

    questionnaire_version: str = Field(..., pattern=r"^v[0-9]+\.[0-9]+$")
    questions: list[QuestionBankItem] = Field(..., min_length=1, max_length=64)


# ============== 状态机返回结果 ==============

class DimensionCoverage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field_name: str
    covered: bool


class QuestionnaireRecomputeResult(BaseModel):
    """recompute 每次重算的结果。纯确定性。"""

    model_config = ConfigDict(extra="forbid")

    questionnaire_version: str
    next_question_ids: list[str] = Field(default_factory=list)
    # 因前置条件不再满足、被作废的已答 question_id 列表。由 P2-03A 路由返回前端。
    invalidated_answer_question_ids: list[str] = Field(default_factory=list)
    is_complete: bool
    progress_pct: int = Field(..., ge=0, le=100)
    covered_dimensions: list[DimensionCoverage] = Field(default_factory=list)
    completion_reason: Literal[
        "all_required_answered",
        "entry_intent_no_questionnaire_required",
        "not_complete",
    ]
    required_not_yet_answered_question_ids: list[str] = Field(default_factory=list)


# ============== 草稿：round-trip 序列化 ==============

class QuestionnaireDraftV1(BaseModel):
    """草稿保存的最小集合（localStorage 级即可）。纯数据，无函数。"""

    model_config = ConfigDict(extra="forbid")

    questionnaire_version: str
    entry_intent: str
    # answers_by_question_id: 每道题的用户答案，单题是 list[str]（single 则 len=1；multi 则 len≥1）
    # 与 options.value 对应；不存 option_id，option_id 可能跨版本变、value 是语义稳定的 enum.value
    answers_by_question_id: dict[str, list[str]] = Field(default_factory=dict)


__all__ = [
    "ENTRY_INTENT_VALUES",
    "MAPPABLE_DIMENSION_FIELDS",
    "DimensionCoverage",
    "DimensionMapping",
    "DisplayCondition",
    "QuestionBankItem",
    "QuestionBankV1",
    "QuestionOption",
    "QuestionnaireDraftV1",
    "QuestionnaireRecomputeResult",
]
