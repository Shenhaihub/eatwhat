"""问卷状态机专用 schema：问题库、选项、展示条件、重算结果。

P2-03 专用。所有类均 extra="forbid"，与 P2 整体契约一致。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

# 入口意图：必须与 P2-01 的 ENTRY_POINT_INTENT_VALUES 一致（字符串形式），
# 避免在 schema 层造成循环依赖：QuestionnaireAnswers 里已定义八维字段，这里不再重复。
ENTRY_INTENT_VALUES: tuple[str, ...] = (
    "ai_recommend",
    "community",
    "activity",
    "user_choice",
)

# P3-01 起：七维全部纳入 covered_dimensions 追踪。
# max_distance_m 不出问卷题（在地点选择页收集），但需要在 covered_dimensions 中可见。
# ai_follow_up_answers 在 P5 再纳入。
MAPPABLE_DIMENSION_FIELDS: tuple[str, ...] = (
    "meal_period",
    "appetite",
    "avoidances",
    "tastes",
    "budget",
    "explicit_food_preference",
    "max_distance_m",
    # P1 修复：菜系偏好（新增 q07_cuisine_preference 题）
    "cuisine_preferences",
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
        "max_distance_m",
        "cuisine_preferences",
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
    """recompute 每次重算的结果。纯确定性。字段 1:1 对应 P2-03A /api/v1/questionnaire/next 的响应。
    设计原则（与清单严格对齐，避免 API 层再做"别名转换"）：
    - next_questions：直接返回题对象列表（QuestionBankItem），前端无需再发 /question GET 拉题面/选项。
    - next_question_ids：冗余保留，方便前端仅用 id 做缓存/草稿比对。
    - invalidated_answer_ids：清单里约定的短名，替代旧 invalidated_answer_question_ids；
      保留旧字段作为 deprecated alias，兼容 P2-03 时期的调用方。
    - progress：清单要求的短名，替代旧 progress_pct，语义仍为整数百分比 0-100。
    - next_action：P2-03A 清单要求的下一步动作指引，前端无需再根据 is_complete+completion_reason 自己猜。
    """

    model_config = ConfigDict(extra="forbid")

    questionnaire_version: str

    # ============== 题面与题 ID ==============
    # 下一步建议展示的 1~2 道题（直接给题对象，前端无需二次查询）
    next_questions: list[QuestionBankItem] = Field(default_factory=list)
    # next_questions 的 question_id 列表（冗余，方便纯比对旧草稿用）
    next_question_ids: list[str] = Field(default_factory=list)

    # ============== 作废答案 ID ==============
    # 主名字：与 P2-03/P2-03A 两处清单里 "invalidated_answer_ids" 完全一致
    invalidated_answer_ids: list[str] = Field(default_factory=list)
    # 旧名字：兼容 P2-03 阶段已有调用方，值与 invalidated_answer_ids 恒等。
    # API 层不暴露此字段（P2-03A 返回时允许缺省，或通过 response_model_exclude 屏蔽）
    invalidated_answer_question_ids: list[str] = Field(default_factory=list)

    # ============== 完整度 ==============
    is_complete: bool
    # 整数百分比 0-100；与清单字段名 progress 一致。
    progress: int = Field(..., ge=0, le=100)
    # 兼容旧字段（值与 progress 恒等）。API 层禁止暴露 progress_pct。
    progress_pct: int = Field(0, ge=0, le=100)

    # ============== 覆盖度 & 原因 & 剩余必填 ==============
    covered_dimensions: list[DimensionCoverage] = Field(default_factory=list)
    completion_reason: Literal[
        "all_required_answered",
        "entry_intent_no_questionnaire_required",
        "not_complete",
    ]
    # 当前 entry 下"必填但还没答"的 question_id 列表（按问题库原始顺序）
    required_not_yet_answered_question_ids: list[str] = Field(default_factory=list)

    # ============== 下一步动作指引（P2-03A 清单硬性要求） ==============
    # proceed_questionnaire: 还没做完，继续答题（next_questions 非空时为主）
    # proceed_generate_recommendations: is_complete=true 且 entry_intent=ai_recommend，允许调用推荐接口
    # redirect_no_questionnaire_required: is_complete=true 且 entry=community/activity/user_choice，前端无需问卷
    next_action: Literal[
        "proceed_questionnaire",
        "proceed_generate_recommendations",
        "redirect_no_questionnaire_required",
    ]


# ============== 草稿：round-trip 序列化 ==============

class QuestionnaireDraftV1(BaseModel):
    """草稿保存的最小集合（localStorage 级即可）。纯数据，无函数。"""

    model_config = ConfigDict(extra="forbid")

    questionnaire_version: str
    entry_intent: str
    # answers_by_question_id: 每道题的用户答案，单题是 list[str]（single 则 len=1；multi 则 len≥1）
    # 与 options.value 对应；不存 option_id，option_id 可能跨版本变、value 是语义稳定的 enum.value
    answers_by_question_id: dict[str, list[str]] = Field(default_factory=dict)


# ============== P2-03A HTTP 请求体 ==============


class QuestionnaireNextRequestV1(BaseModel):
    """POST /api/v1/questionnaire/next 的请求体。

    严格校验 + extra=forbid：防止客户端把"推荐相关字段"（如 source_type）混进请求。
    对应 28_P2-03A_API设计_v1.0.md §2.1。
    """

    model_config = ConfigDict(extra="forbid")

    entry_intent: Literal[
        "ai_recommend",
        "community",
        "activity",
        "user_choice",
    ]
    # 版本正则；实际文件存在性由路由层再判断（不存在 → 404）。
    questionnaire_version: str = Field(
        ...,
        pattern=r"^v[0-9]+\.[0-9]+$",
    )
    answers_by_question_id: dict[str, list[str]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_answers_shapes(self) -> QuestionnaireNextRequestV1:
        """answers_by_question_id 的 key/value 形状校验（question_id 正则 + answer 长度 1-32）。"""
        import re

        qid_re = re.compile(r"^[a-z0-9_]{2,40}$")
        for qid, vals in self.answers_by_question_id.items():
            if not qid_re.match(qid):
                raise ValueError(
                    f"answers_by_question_id key={qid!r} not match "
                    f"question_id pattern ^[a-z0-9_]{{2,40}}$ (G-09)"
                )
            if not isinstance(vals, list):  # pragma: no cover - Pydantic 会先把非 list 拦掉
                raise TypeError(f"answers_by_question_id[{qid!r}] must be a list")
            for idx, v in enumerate(vals):
                if not isinstance(v, str):  # pragma: no cover
                    raise TypeError(
                        f"answers_by_question_id[{qid!r}][{idx}] must be str"
                    )
                if len(v) < 1 or len(v) > 32:
                    raise ValueError(
                        f"answers_by_question_id[{qid!r}][{idx}] length out of "
                        f"range [1,32]: {len(v)} (G-09)"
                    )
        return self


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
    "QuestionnaireNextRequestV1",
    "QuestionnaireRecomputeResult",
]
