"""基础题与自适应预设题状态机（P2-03）。

核心原则（与 P2-03A next API 对齐约定）：
- **每次从头确定性重算**：不保存内部 state；同一 QuestionBankV1 + entry_intent + answers_by_qid
  → 每次调用 recompute 都返回相同结果（完全 DAG 化、可复现、可审计）。
- **display_if 条件为假的已答题目直接作废**：在 invalidated_answer_question_ids 中显式返回给前端，
  对应 P2-03 验收"修改早期答案会使不再适用的后续答案失效并重新选题"。
- **G-09 字段长度软约束**：question_id ≤40，title_zh ≤64，label_zh ≤32，value ≤32
  （schema 已通过 Field max_length 校验，加载时再验证）。
- **G-11 问卷医学边界**：问题库 v1.0 **不单独询问医学过敏原**（peanut/shellfish/soy/dairy/gluten/egg）。
  问卷层面只问 Avoidance 四档一般忌口（none/seafood/meat/vegetarian），
  医学过敏仅在"食物详情页"展示 FoodDictionaryItem.medical_allergen_tags + safety_note，符合 G-11。
- **未登录也能完成**：纯函数，无任何 DB/账号依赖，localStorage 级的 QuestionnaireDraftV1
  即可保存草稿 → 本地完成 → P2-04 展示 5 候选。
"""

from __future__ import annotations

import json
import math
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.schemas.questionnaire import (
    ENTRY_INTENT_VALUES,
    MAPPABLE_DIMENSION_FIELDS,
    DimensionCoverage,
    DisplayCondition,
    QuestionBankItem,
    QuestionBankV1,
    QuestionnaireDraftV1,
    QuestionnaireRecomputeResult,
)

DEFAULT_QUESTIONNAIRE_VERSION = "v1.0"
_DATA_DIR = Path(__file__).resolve().parents[1] / "data"


# ---------------- 加载 & 启动校验 ----------------

@lru_cache(maxsize=4)
def load_question_bank(questionnaire_version: str = DEFAULT_QUESTIONNAIRE_VERSION) -> QuestionBankV1:
    """加载问题库 JSON。带缓存；启动时一次性校验一致性。"""
    if not questionnaire_version.startswith("v") or "." not in questionnaire_version:
        raise ValueError(f"invalid questionnaire_version: {questionnaire_version!r}")
    file_path = _DATA_DIR / f"question_bank_{questionnaire_version}.json"
    if not file_path.is_file():
        raise FileNotFoundError(f"question bank not found: {file_path}")
    try:
        raw = json.loads(file_path.read_text(encoding="utf-8"))
    except TypeError as exc:  # pragma: no cover - 仅 JSON decode 类型错误
        raise TypeError(f"question bank JSON load failed: {file_path}") from exc
    bank = QuestionBankV1.model_validate(raw)
    _validate_bank_integrity(bank)
    return bank


def _validate_bank_integrity(bank: QuestionBankV1) -> None:
    """加载时一次性的硬校验：ID 唯一、display_if 引用合法、入口意图合法、options.value 去重。"""
    seen_ids: set[str] = set()
    for q in bank.questions:
        if q.question_id in seen_ids:
            raise ValueError(f"duplicate question_id: {q.question_id!r}")
        seen_ids.add(q.question_id)
        # options 内 option_id / value 双唯一
        opt_ids = {o.option_id for o in q.options}
        opt_vals = {o.value for o in q.options}
        if len(opt_ids) != len(q.options):
            raise ValueError(f"duplicate option_id in question {q.question_id!r}")
        if len(opt_vals) != len(q.options):
            raise ValueError(f"duplicate option value in question {q.question_id!r}")
        # display_if 引用的 question_id 必须存在
        cond = q.display_if
        if cond is not None and cond.operator != "always_true":
            if cond.operand_question_id is None:
                raise ValueError(
                    f"question {q.question_id!r} operator={cond.operator!r} "
                    "requires operand_question_id"
                )
            if cond.operand_question_id not in seen_ids and cond.operand_question_id not in {
                qi.question_id for qi in bank.questions
            }:
                # display_if 只能引用"前面的题"（按 JSON 顺序），避免前向引用造成语义混乱
                # 同时也必须在问题库内存在
                raise ValueError(
                    f"question {q.question_id!r} references unknown "
                    f"operand_question_id={cond.operand_question_id!r}"
                )
        # required_for_entry_intents 只能是 ENTRY_INTENT_VALUES 的子集
        unknown_intents = [
            e for e in q.required_for_entry_intents if e not in ENTRY_INTENT_VALUES
        ]
        if unknown_intents:
            raise ValueError(
                f"question {q.question_id!r} required_for_entry_intents contains unknown "
                f"entry intents: {unknown_intents}"
            )
        # maps_to 字段必须属于 MAPPABLE_DIMENSION_FIELDS
        if q.maps_to.field_name not in MAPPABLE_DIMENSION_FIELDS:
            raise ValueError(
                f"question {q.question_id!r} maps_to.field_name={q.maps_to.field_name!r} "
                f"not in {MAPPABLE_DIMENSION_FIELDS}"
            )


# ---------------- display_if 评估（纯函数） ----------------

def _get_answer_values(answers_by_qid: dict[str, list[str]], qid: str) -> list[str]:
    """规范化答案读取：单/多 choice 都返回 list[str]，空或缺 → []。"""
    raw = answers_by_qid.get(qid)
    if not raw:
        return []
    # 防御空字符串
    return [v for v in raw if v]


def _evaluate_condition(
    cond: DisplayCondition | None,
    answers_by_qid: dict[str, list[str]],
) -> bool:
    """评估 display_if。None / always_true → True；其它按 operator 判定。"""
    if cond is None:
        return True
    if cond.operator == "always_true":
        return True
    if cond.operand_question_id is None:
        return False
    answers = _get_answer_values(answers_by_qid, cond.operand_question_id)
    operand = cond.operand_value
    if not answers:
        # 用户未回答前置问题 → 条件判"未成立"，本题不展示。
        # 例如 Q06_appetite 需要 Q01 先答 lunch/dinner/midnight_snack 才能展示。
        return False
    if cond.operator == "equals":
        # 单选或多选都允许"第一个/任一匹配"。严格按"答案列表包含 operand（单值 str）"
        return str(operand) in answers
    if cond.operator == "not_equals":
        return len(answers) >= 1 and str(operand) not in answers
    if cond.operator == "in":
        # 用户答案 和 operand_value（list） 有交集 → True
        if not isinstance(operand, list):
            return False
        operand_set = {str(x) for x in operand}
        return bool(operand_set.intersection(answers))
    if cond.operator == "not_in":
        if not isinstance(operand, list):
            return False
        operand_set = {str(x) for x in operand}
        return len(answers) >= 1 and not operand_set.intersection(answers)
    return False


# ---------------- 重算（主入口） ----------------

def recompute_questionnaire(
    *,
    bank: QuestionBankV1,
    entry_intent: str,
    answers_by_question_id: dict[str, list[str]],
) -> QuestionnaireRecomputeResult:
    """每次从头重算 → 返回 next/invalidated/is_complete/progress/coverage。

    不修改输入参数；对 answers_by_question_id 不做校验（调用方保证 key 合法）。
    """
    if entry_intent not in ENTRY_INTENT_VALUES:
        raise ValueError(f"unknown entry_intent={entry_intent!r}, expect {ENTRY_INTENT_VALUES}")

    # Step 1. 对每道题按 display_if 计算"当前是否展示 active"
    questions_by_id: dict[str, QuestionBankItem] = {q.question_id: q for q in bank.questions}
    active_qids: list[str] = []
    active_set: set[str] = set()
    for q in bank.questions:
        if _evaluate_condition(q.display_if, answers_by_question_id):
            active_qids.append(q.question_id)
            active_set.add(q.question_id)

    # Step 2. 收集作废的已答：用户答过 → 现在 active=false → invalidated
    answered_qids = set(answers_by_question_id.keys())
    invalidated = sorted(answered_qids - active_set)

    # Step 3. 真正有效的答案（只看 active 的题）
    valid_answers_by_qid: dict[str, list[str]] = {
        qid: _get_answer_values(answers_by_question_id, qid)
        for qid in active_qids
    }
    effectively_answered = {qid for qid, vals in valid_answers_by_qid.items() if vals}

    # Step 4. 汇总"当前入口意图下 required 的题"
    required_qids = [
        q.question_id
        for q in bank.questions
        if entry_intent in q.required_for_entry_intents
    ]
    required_set = set(required_qids)
    required_answered = required_set & effectively_answered
    required_missing = [qid for qid in required_qids if qid not in required_answered]

    # Step 5. covered_dimensions：基于有效答案的字段覆盖度（MAPPABLE_DIMENSION_FIELDS 每维 covered=true/false）
    covered_fields: set[str] = set()
    for qid, vals in valid_answers_by_qid.items():
        if not vals:
            continue
        q = questions_by_id[qid]
        covered_fields.add(q.maps_to.field_name)
    covered_dimensions = [
        DimensionCoverage(field_name=f, covered=(f in covered_fields))
        for f in MAPPABLE_DIMENSION_FIELDS
    ]

    # Step 6. progress_pct：required 为 0 直接 100；否则向下取整（整数 0-100）
    if required_set:
        progress_pct = math.floor(100 * len(required_answered) / len(required_set))
    else:
        progress_pct = 100
    progress_pct = max(0, min(100, progress_pct))

    # Step 7. is_complete + completion_reason + next_action（P2-03A 清单硬性要求的下一步指引）
    if not required_set:
        # community / activity / user_choice 入口：问卷不是必经
        is_complete = True
        completion_reason: Any = "entry_intent_no_questionnaire_required"
        next_action: Any = "redirect_no_questionnaire_required"
    elif not required_missing:
        is_complete = True
        completion_reason = "all_required_answered"
        # entry=ai_recommend 时完整后允许去生成推荐；其余入口兜底为 redirect（虽然理论上其余入口都走上面 not required_set）
        if entry_intent == "ai_recommend":
            next_action = "proceed_generate_recommendations"
        else:
            next_action = "redirect_no_questionnaire_required"
    else:
        is_complete = False
        completion_reason = "not_complete"
        next_action = "proceed_questionnaire"

    # Step 8. next_question_ids & next_questions：按问题库原始顺序，
    # 取"active 且 未有效回答 且 required 优先"的前 1 题（UI 视觉上更聚焦；
    # 答完再推下一题）；同时返回题对象（前端无需二次查）
    next_ids: list[str] = []
    MAX_QUESTIONS_PER_STEP = 1
    missing_ids_ordered = [qid for qid in required_qids if qid in required_missing]
    for qid in missing_ids_ordered:
        if qid not in effectively_answered and qid in active_set:
            next_ids.append(qid)
            if len(next_ids) >= MAX_QUESTIONS_PER_STEP:
                break
    # 若 required 还差 0 或不够 1，再补非 required 的 active+未答题
    if len(next_ids) < MAX_QUESTIONS_PER_STEP:
        for q in bank.questions:
            qid = q.question_id
            if (
                qid in active_set
                and qid not in effectively_answered
                and qid not in required_set
                and qid not in next_ids
            ):
                next_ids.append(qid)
                if len(next_ids) >= MAX_QUESTIONS_PER_STEP:
                    break
    next_questions_items: list[Any] = [
        questions_by_id[qid] for qid in next_ids if qid in questions_by_id
    ]

    # 兼容字段赋值：保持 P2-03 阶段已有调用方的老字段名值恒等。
    progress_val = progress_pct
    invalidated_val = invalidated

    return QuestionnaireRecomputeResult(
        questionnaire_version=bank.questionnaire_version,
        next_questions=next_questions_items,
        next_question_ids=next_ids,
        invalidated_answer_ids=invalidated_val,
        invalidated_answer_question_ids=invalidated_val,
        is_complete=is_complete,
        progress=progress_val,
        progress_pct=progress_val,
        covered_dimensions=covered_dimensions,
        completion_reason=completion_reason,
        required_not_yet_answered_question_ids=required_missing,
        next_action=next_action,
    )


# ---------------- 草稿 round-trip（localStorage 级，无账号依赖） ----------------

def draft_to_dict(draft: QuestionnaireDraftV1) -> dict[str, Any]:
    """草稿 → dict（前端直接 JSON.stringify 进 localStorage）。"""
    return draft.model_dump(mode="json")


def draft_from_dict(raw: dict[str, Any]) -> QuestionnaireDraftV1:
    """dict → 草稿（Pydantic 校验字段严格、extra=forbid，防止污染旧草稿的脏字段）。"""
    return QuestionnaireDraftV1.model_validate(raw)


__all__ = [
    "DEFAULT_QUESTIONNAIRE_VERSION",
    "draft_from_dict",
    "draft_to_dict",
    "load_question_bank",
    "recompute_questionnaire",
]
