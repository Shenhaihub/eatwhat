"""问卷 answers_by_question_id → 规则引擎 QuestionnaireAnswers 的纯映射服务。

职责：
1. 从 QuestionBankV1 中查到每道题的 maps_to（field_name、is_array、value_is_enum_value）；
2. 把单/多选题的 `list[str]` 答案组装进 QuestionnaireAnswers（七维字段严格映射，没有任何启发式推导）；
3. 不做 display_if 判断（上层 recompute 已做；这里直接把传入的答案都当成"有效答案"）。

语义稳定性：
- 同一 QuestionBankV1 + answers_by_question_id → 同一 QuestionnaireAnswers（100% 确定）；
- question_id 在 bank 不存在时跳过，不抛错（遵循"只使用可追溯映射"的原则）；
- is_array=false 时：answer 长度 >=1 取第 1 个；其余忽略。
"""

from __future__ import annotations

from typing import Any

from app.schemas import (
    Appetite,
    Avoidance,
    BudgetTier,
    ExplicitPreference,
    MealPeriod,
    QuestionnaireAnswers,
    Taste,
)
from app.schemas.questionnaire import (
    MAPPABLE_DIMENSION_FIELDS,
    QuestionBankV1,
)

_SCALAR_ENUM_BY_FIELD: dict[str, type[Any]] = {
    "meal_period": MealPeriod,
    "appetite": Appetite,
    "budget": BudgetTier,
    "explicit_food_preference": ExplicitPreference,
}

_ARRAY_ENUM_BY_FIELD: dict[str, type[Any]] = {
    "avoidances": Avoidance,
    "tastes": Taste,
}


def _to_scalar_enum(field_name: str, values: list[str]) -> Any:
    """单选型答案 → 枚举实例或 None。

    - 答案空 → None；
    - 第一个 value 能被 enum 解析 → 枚举实例；
    - 解析失败（非法 value）→ None，交由规则引擎的"空=未覆盖"语义处理。
    """
    if not values:
        return None
    value = values[0]
    enum_cls = _SCALAR_ENUM_BY_FIELD[field_name]
    try:
        return enum_cls(value)
    except ValueError:
        return None


def _to_array_enum(field_name: str, values: list[str]) -> list[Any]:
    """多选型答案 → 枚举列表；会去重、保留原始顺序、过滤解析失败项。"""
    enum_cls = _ARRAY_ENUM_BY_FIELD[field_name]
    out: list[Any] = []
    seen: set[str] = set()
    for v in values:
        if v in seen:
            continue
        try:
            enum_val = enum_cls(v)
        except ValueError:
            continue
        out.append(enum_val)
        seen.add(v)
    return out


def questionnaire_answers_by_qid_to_rule_input(
    *,
    bank: QuestionBankV1,
    answers_by_question_id: dict[str, list[str]],
    questionnaire_version: str,
) -> QuestionnaireAnswers:
    """把问卷 answers_by_qid 映射成 QuestionnaireAnswers。

    - 不会抛错；非法 value 直接跳过（返回对应字段 None/[]）。
    - 只取 MAPPABLE_DIMENSION_FIELDS 六维，max_distance_m / ai_follow_up_answers 留空（P2 不对这些维度出题）。
    """
    q_by_id = {q.question_id: q for q in bank.questions}
    # 先按 field_name 聚合所有答案（用于多题映射到同一字段时保留多个值）
    collected_by_field: dict[str, list[str]] = {f: [] for f in MAPPABLE_DIMENSION_FIELDS}

    for qid, vals in answers_by_question_id.items():
        q = q_by_id.get(qid)
        if q is None:
            continue
        if not vals:
            continue
        field_name = q.maps_to.field_name
        if q.maps_to.is_array:
            collected_by_field[field_name] = [*collected_by_field[field_name], *vals]
        else:
            # 单选型：同一字段若有多个题映射（当前 v1.0 不会发生），则以最后一道为准（replace 语义）
            collected_by_field[field_name] = list(vals)

    return QuestionnaireAnswers(
        questionnaire_version=questionnaire_version,
        meal_period=_to_scalar_enum("meal_period", collected_by_field["meal_period"]),
        appetite=_to_scalar_enum("appetite", collected_by_field["appetite"]),
        avoidances=_to_array_enum("avoidances", collected_by_field["avoidances"]),
        tastes=_to_array_enum("tastes", collected_by_field["tastes"]),
        budget=_to_scalar_enum("budget", collected_by_field["budget"]),
        explicit_food_preference=_to_scalar_enum(
            "explicit_food_preference",
            collected_by_field["explicit_food_preference"],
        ),
        # P3/P5 才会出这两维的题
        max_distance_m=None,
        ai_follow_up_answers={},
    )


__all__ = [
    "questionnaire_answers_by_qid_to_rule_input",
]
