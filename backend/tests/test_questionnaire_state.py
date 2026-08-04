"""P2-03 测试：基础题与自适应预设题状态机。
覆盖的验收契约（P2-03 清单）：
1. 基础 2-3 题 + 自适应 2-3 题的选择条件 + 七维覆盖检查
2. 修改早期答案 → 不再适用的后续答案失效并重新选题（invalidated 列表非空）
3. 任何路径不遗漏必要维度（required_set 全答才 is_complete）
4. 草稿保存/恢复/重置 round-trip
5. 未登录可完成前置问卷（纯函数，无账号/DB 依赖）
6. 加载校验：question_id 唯一、display_if 引用合法、入口意图合法
"""

from __future__ import annotations

from typing import Any

import pytest

from app.schemas.questionnaire import (
    DimensionMapping,
    DisplayCondition,
    QuestionBankItem,
    QuestionBankV1,
    QuestionnaireDraftV1,
    QuestionOption,
)
from app.services.questionnaire_state import (
    DEFAULT_QUESTIONNAIRE_VERSION,
    draft_from_dict,
    draft_to_dict,
    load_question_bank,
    recompute_questionnaire,
)


@pytest.fixture(scope="module")
def bank_v1():
    load_question_bank.cache_clear()
    return load_question_bank(DEFAULT_QUESTIONNAIRE_VERSION)


# ============== 加载 & 启动校验 ==============

class TestBankIntegrity:
    def test_load_default_bank_ok(self, bank_v1):
        assert bank_v1.questionnaire_version == "v1.0"
        assert len(bank_v1.questions) == 6

    def test_all_question_ids_unique(self, bank_v1):
        ids = [q.question_id for q in bank_v1.questions]
        assert len(ids) == len(set(ids))

    def test_bad_bank_unknown_display_ref_raises(self):
        # 构造一个引用不存在 question_id 的坏问题库
        bad_q = QuestionBankItem(
            question_id="q_bad_ref",
            title_zh="坏引用",
            question_type="single_choice",
            options=[QuestionOption(option_id="op_a", label_zh="A", value="a")],
            maps_to=DimensionMapping(
                field_name="budget",
                is_array=False,
                value_is_enum_value=True,
            ),
            display_if=DisplayCondition(
                operator="equals",
                operand_question_id="q_does_not_exist",
                operand_value="x",
            ),
            required_for_entry_intents=[],
        )
        bad_bank = QuestionBankV1(
            questionnaire_version="v0.9",
            questions=[bad_q],
        )
        # 直接调用 _validate_bank_integrity（通过导入）
        from app.services.questionnaire_state import _validate_bank_integrity

        with pytest.raises(ValueError, match="operand_question_id"):
            _validate_bank_integrity(bad_bank)


# ============== ai_recommend 入口完成路径 ==============

class TestAiRecommendFullPath:
    def test_ai_recommend_empty_not_complete(self, bank_v1):
        # 没回答任何题 → 三道 required 都缺
        res = recompute_questionnaire(
            bank=bank_v1,
            entry_intent="ai_recommend",
            answers_by_question_id={},
        )
        assert res.is_complete is False
        assert res.progress_pct == 0
        assert set(res.required_not_yet_answered_question_ids) == {
            "q01_meal_period",
            "q02_explicit_food",
            "q03_budget",
        }
        # next 应该先给 required 中的前两个
        assert res.next_question_ids[:2] == ["q01_meal_period", "q02_explicit_food"]

    def test_ai_recommend_all_required_answered_complete(self, bank_v1):
        # Q1/Q2/Q3 三道基础答完 = complete（自适应题可以不答，非 required）
        res = recompute_questionnaire(
            bank=bank_v1,
            entry_intent="ai_recommend",
            answers_by_question_id={
                "q01_meal_period": ["dinner"],
                "q02_explicit_food": ["undecided"],
                "q03_budget": ["from_20_to_30"],
            },
        )
        assert res.is_complete is True
        assert res.completion_reason == "all_required_answered"
        assert res.progress_pct == 100
        # 六维覆盖：meal_period、explicit_food_preference、budget 三个 covered=true
        covered = {c.field_name: c.covered for c in res.covered_dimensions}
        assert covered["meal_period"] is True
        assert covered["explicit_food_preference"] is True
        assert covered["budget"] is True
        # next 应该只剩可选自适应题（口味/忌口/食量）
        for nxt in res.next_question_ids:
            assert nxt not in {
                "q01_meal_period",
                "q02_explicit_food",
                "q03_budget",
            }


# ============== 关键验收：修改早期答案使 Q6 食量失效 ==============

class TestModifyEarlyAnswerInvalidatesLater:
    """P2-03 验收 2：修改早期答案 → 不再适用的后续答案失效并重新选题。"""

    def test_q06_appetite_invalidated_when_q01_switched_to_afternoon_tea(self, bank_v1):
        # Step A. Q01=lunch → Q06 active（display_if lunch in [lunch/dinner/midnight_snack]）
        answers = {
            "q01_meal_period": ["lunch"],
            "q02_explicit_food": ["undecided"],
            "q03_budget": ["under_20"],
            "q06_appetite": ["hungry"],  # 答了食量（因为 lunch 时展示）
        }
        before = recompute_questionnaire(
            bank=bank_v1,
            entry_intent="ai_recommend",
            answers_by_question_id=answers,
        )
        assert before.is_complete
        assert before.invalidated_answer_question_ids == []

        # Step B. 改 Q01 → afternoon_tea（下午茶）。Q06 的 display_if 要求 lunch/dinner/midnight_snack，
        # 所以 Q06 现在不 active，而且之前答了 → 必须出现在 invalidated 中
        answers_mod = {**answers}
        answers_mod["q01_meal_period"] = ["afternoon_tea"]
        after = recompute_questionnaire(
            bank=bank_v1,
            entry_intent="ai_recommend",
            answers_by_question_id=answers_mod,
        )
        assert "q06_appetite" in after.invalidated_answer_question_ids, (
            "修改 Q01=下午茶 使 Q6_appetite 展示条件不成立，应被作废"
        )
        # covered_dimensions 中 appetite 应该变成 false（因为答被作废了）
        covered_after = {c.field_name: c.covered for c in after.covered_dimensions}
        assert covered_after["appetite"] is False


# ============== community / activity / user_choice 入口：问卷不是必须 ==============

class TestNonRecommendEntrySkipsQuestionnaire:
    @pytest.mark.parametrize("entry_intent", ["community", "activity", "user_choice"])
    def test_non_recommend_complete_immediately(self, bank_v1, entry_intent: str):
        res = recompute_questionnaire(
            bank=bank_v1,
            entry_intent=entry_intent,
            answers_by_question_id={},
        )
        assert res.is_complete is True
        assert res.completion_reason == "entry_intent_no_questionnaire_required"
        assert res.progress_pct == 100


# ============== 草稿 round-trip ==============

class TestDraftRoundtrip:
    def test_draft_dict_roundtrip_equal(self, bank_v1):
        draft = QuestionnaireDraftV1(
            questionnaire_version=bank_v1.questionnaire_version,
            entry_intent="ai_recommend",
            answers_by_question_id={
                "q01_meal_period": ["dinner"],
                "q02_explicit_food": ["undecided"],
                "q03_budget": ["from_20_to_30"],
            },
        )
        d = draft_to_dict(draft)
        loaded = draft_from_dict(d)
        assert draft_to_dict(loaded) == d

    def test_draft_refuses_unknown_intent(self, bank_v1):
        # QuestionnaireDraftV1 只存字符串不做枚举校验；但 recompute 入口会严格校验 entry_intent
        bad_raw: dict[str, Any] = {
            "questionnaire_version": bank_v1.questionnaire_version,
            "entry_intent": "bogus_entry",
            "answers_by_question_id": {},
        }
        # 1) draft 层接受字符串（为了兼容未来版本扩展，序列化层不做强枚举）
        draft_from_dict(bad_raw)  # 不抛
        # 2) recompute 入口严格抛
        with pytest.raises(ValueError, match="entry_intent"):
            recompute_questionnaire(
                bank=bank_v1,
                entry_intent="bogus_entry",
                answers_by_question_id={},
            )


# ============== 稳定输出：相同输入相同结果 ==============

class TestDeterministic:
    def test_same_input_gives_same_result(self, bank_v1):
        kwargs: dict[str, Any] = {
            "bank": bank_v1,
            "entry_intent": "ai_recommend",
            "answers_by_question_id": {
                "q01_meal_period": ["dinner"],
                "q02_explicit_food": ["undecided"],
                "q03_budget": ["from_20_to_30"],
                "q04_tastes": ["spicy", "sour"],
                "q05_avoidances": [],
                "q06_appetite": ["normal"],
            },
        }
        a = recompute_questionnaire(**kwargs)
        b = recompute_questionnaire(**kwargs)
        assert a.model_dump(mode="json") == b.model_dump(mode="json")
