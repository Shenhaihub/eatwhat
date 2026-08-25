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
        # P1：新增 q07_cuisine_preference，题库扩展到 7 题
        assert len(bank_v1.questions) == 7

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
        assert res.progress == 0
        assert set(res.required_not_yet_answered_question_ids) == {
            "q01_meal_period",
            "q02_explicit_food",
            "q03_budget",
        }
        # next 应该先给 required 中的第 1 个（P1 改成 MAX_QUESTIONS_PER_STEP = 1，单题单列聚焦）
        assert res.next_question_ids[:1] == ["q01_meal_period"]

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
        assert res.progress == 100
        assert res.progress_pct == res.progress  # 兼容冗余字段恒等
        assert res.next_action == "proceed_generate_recommendations"
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
        assert before.invalidated_answer_ids == []
        # 冗余兼容字段必须与主字段恒等
        assert before.invalidated_answer_question_ids == before.invalidated_answer_ids
        assert before.progress_pct == before.progress

        # Step B. 改 Q01 → afternoon_tea（下午茶）。Q06 的 display_if 要求 lunch/dinner/midnight_snack，
        # 所以 Q06 现在不 active，而且之前答了 → 必须出现在 invalidated 中
        answers_mod = {**answers}
        answers_mod["q01_meal_period"] = ["afternoon_tea"]
        after = recompute_questionnaire(
            bank=bank_v1,
            entry_intent="ai_recommend",
            answers_by_question_id=answers_mod,
        )
        assert "q06_appetite" in after.invalidated_answer_ids, (
            "修改 Q01=下午茶 使 Q6_appetite 展示条件不成立，应被作废"
        )
        # 兼容字段恒等
        assert after.invalidated_answer_question_ids == after.invalidated_answer_ids
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
        assert res.progress == 100
        assert res.progress_pct == res.progress  # 兼容冗余字段恒等
        assert res.next_action == "redirect_no_questionnaire_required"


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


# ============== 新字段与 P2-03A 清单对齐验证 ==============


class TestFieldsAlignedWithP03A:
    """确保状态机返回 1:1 映射到 P2-03A /api/v1/questionnaire/next 响应，避免 API 层再做别名转换。"""

    def test_next_questions_object_array_matches_next_ids(self, bank_v1):
        """next_questions（对象数组）的 question_id 顺序必须与 next_question_ids 恒等。"""
        # 空答案 → 第一步 next 是 q01/q02（前两个必填）
        res = recompute_questionnaire(
            bank=bank_v1,
            entry_intent="ai_recommend",
            answers_by_question_id={},
        )
        assert [q.question_id for q in res.next_questions] == res.next_question_ids
        # 题对象不为空、extra=forbid（Pydantic 默认不会传不该传的字段）
        for q in res.next_questions:
            assert q.question_id
            assert q.title_zh
            assert len(q.options) >= 1

    def test_next_action_matrix(self, bank_v1):
        """next_action 三值矩阵：proceed_questionnaire / proceed_generate_recommendations / redirect_no_questionnaire_required。"""
        # 1) 未答任何题 → 继续答题
        r1 = recompute_questionnaire(
            bank=bank_v1, entry_intent="ai_recommend", answers_by_question_id={}
        )
        assert r1.next_action == "proceed_questionnaire"
        assert r1.is_complete is False
        # 2) 三题必答都答完 → 完整后 proceed_generate_recommendations
        r2 = recompute_questionnaire(
            bank=bank_v1,
            entry_intent="ai_recommend",
            answers_by_question_id={
                "q01_meal_period": ["breakfast"],
                "q02_explicit_food": ["undecided"],
                "q03_budget": ["under_20"],
            },
        )
        assert r2.next_action == "proceed_generate_recommendations"
        assert r2.is_complete is True
        # 3) community 入口 → redirect_no_questionnaire_required
        r3 = recompute_questionnaire(
            bank=bank_v1, entry_intent="community", answers_by_question_id={}
        )
        assert r3.next_action == "redirect_no_questionnaire_required"
        assert r3.is_complete is True
        # 4) activity 入口 → 同上
        r4 = recompute_questionnaire(
            bank=bank_v1, entry_intent="activity", answers_by_question_id={}
        )
        assert r4.next_action == "redirect_no_questionnaire_required"

    def test_deprecated_alias_fields_equals_main_fields(self, bank_v1):
        """兼容老字段：progress_pct == progress / invalidated_answer_question_ids == invalidated_answer_ids。"""
        res = recompute_questionnaire(
            bank=bank_v1,
            entry_intent="ai_recommend",
            answers_by_question_id={
                "q01_meal_period": ["afternoon_tea"],  # appetite 题 display=false → Q6 答了就作废
                "q02_explicit_food": ["undecided"],
                "q03_budget": ["from_20_to_30"],
                "q06_appetite": ["big_eater"],  # active=false → 进 invalidated
            },
        )
        assert res.progress == res.progress_pct
        assert res.invalidated_answer_ids == res.invalidated_answer_question_ids
        assert "q06_appetite" in res.invalidated_answer_ids
        # 新字段 invalidated_answer_ids 是主名，已在测试中覆盖

    def test_new_fields_in_json_dump_include_next_action_and_next_questions(self, bank_v1):
        """model_dump(mode=json) 必须包含 next_action / next_questions 字段，API 层直接 1:1 透出。"""
        res = recompute_questionnaire(
            bank=bank_v1, entry_intent="ai_recommend", answers_by_question_id={}
        )
        d = res.model_dump(mode="json")
        # 清单约定的关键字段必须存在，且没有被 exclude。这样 API 层只需直接 return res 即可。
        for k in (
            "questionnaire_version",
            "next_questions",
            "next_question_ids",
            "invalidated_answer_ids",
            "is_complete",
            "progress",
            "covered_dimensions",
            "next_action",
        ):
            assert k in d, f"key {k!r} missing in model_dump json，将导致 API 层需手工补字段"
        # 清单名 invalidated_answer_question_ids 作为 deprecated 也允许保留，
        # 但不再作为 P2-03A 文档推荐字段（API response_model 里可以 exclude）
        assert "invalidated_answer_question_ids" in d
        assert "progress_pct" in d
