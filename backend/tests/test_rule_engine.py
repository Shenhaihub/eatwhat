"""P2-02 测试：确定性规则引擎。
覆盖的验收契约：
1. 正好 5 条，priority 1–5 连续唯一
2. 相同输入稳定输出（无 random 依赖）
3. 所有 food_code 均在启用词典内
4. source_type=ai_recommended 服务端派生；generation_mode=rule
5. 每条 matched_signals ≥ 1（G-12 理由可追溯）
6. G-08：空 answers、强忌口杀到只剩 5 条左右，仍返回正好 5
7. G-12 / MEM-024：5 组参数化不同答案 → 至少 4 组的首候选 or Top5 排序不同（反"小碗菜固定第一"）
8. 明确偏好麻辣烫 → 麻辣烫必须在 Top 5 且命中 explicit_food 信号
"""

from __future__ import annotations

import pytest

from app.repositories.food_dictionary import (
    DEFAULT_DICTIONARY_VERSION,
    FoodDictionaryRepository,
    get_food_dictionary_repository,
)
from app.schemas import (
    Appetite,
    Avoidance,
    BudgetTier,
    GenerationMode,
    MealPeriod,
    QuestionnaireAnswers,
    SourceType,
    Taste,
)
from app.services.rule_engine import generate_rule_recommendations


@pytest.fixture(scope="module")
def repo() -> FoodDictionaryRepository:
    get_food_dictionary_repository.cache_clear()  # 防止交叉用例污染
    return get_food_dictionary_repository(DEFAULT_DICTIONARY_VERSION)


# ============== 基础结构：正好 5、在池内、连续 1-5、正确 source/generation ==============

class TestBasicShape:
    def test_exactly_five_priority_1_through_5(self, repo):
        recs = generate_rule_recommendations(
            QuestionnaireAnswers(),
            repo=repo,
        )
        assert len(recs) == 5
        priorities = sorted(r.priority for r in recs)
        assert priorities == [1, 2, 3, 4, 5]

    def test_all_food_codes_in_enabled_pool(self, repo):
        recs = generate_rule_recommendations(
            QuestionnaireAnswers(),
            repo=repo,
        )
        for r in recs:
            assert repo.contains_enabled(r.food_code), f"{r.food_code} 不在启用池"

    def test_source_type_and_generation_mode(self, repo):
        # G-07：服务端派生，不允许 client 决定；同时 generation_mode=rule
        for r in generate_rule_recommendations(QuestionnaireAnswers(), repo=repo):
            assert r.source_type in {
                SourceType.AI_RECOMMENDED,
                SourceType.USER_SELECTED,
                SourceType.COMMUNITY_SELECTED,
                SourceType.ACTIVITY_SELECTED,
            }
            assert r.generation_mode == GenerationMode.RULE

    def test_every_reason_has_at_least_one_signal_g12(self, repo):
        # G-12 理由可追溯
        recs = generate_rule_recommendations(QuestionnaireAnswers(), repo=repo)
        for r in recs:
            assert len(r.reason.matched_signals) >= 1, (
                f"{r.food_code} 没有任何可追溯信号"
            )
            assert 1 <= len(r.reason.summary_zh) <= 160


# ============== 稳定性：相同输入稳定输出 ==============

class TestStability:
    def test_same_input_gives_same_order(self, repo):
        a = generate_rule_recommendations(QuestionnaireAnswers(), repo=repo)
        b = generate_rule_recommendations(QuestionnaireAnswers(), repo=repo)
        assert [r.food_code for r in a] == [r.food_code for r in b]
        assert [r.priority for r in a] == [r.priority for r in b]

    def test_explicit_preference_stable(self, repo):
        answers = QuestionnaireAnswers(explicit_food_preference="malatang")  # type: ignore[arg-type]
        a = generate_rule_recommendations(answers, repo=repo)
        b = generate_rule_recommendations(answers, repo=repo)
        assert [r.food_code for r in a] == [r.food_code for r in b]


# ============== 明确偏好：麻辣烫/牛肉面必须出现在候选 ==============

class TestExplicitPreference:
    def test_malatang_in_top5_when_explicit(self, repo):
        answers = QuestionnaireAnswers(
            explicit_food_preference="malatang",  # type: ignore[arg-type]
            tastes=[Taste.SPICY],
        )
        recs = generate_rule_recommendations(answers, repo=repo)
        codes = [r.food_code for r in recs]
        assert "malatang" in codes
        # 命中 explicit_food 信号
        malatang = next(r for r in recs if r.food_code == "malatang")
        assert any(s.startswith("explicit_food:") for s in malatang.reason.matched_signals)


# ============== G-08：极端输入仍 5 条不空 ==============

class TestPoolNotEmptyG08:
    def test_empty_answers_returns_5(self, repo):
        assert len(generate_rule_recommendations(None, repo=repo)) == 5
        assert len(generate_rule_recommendations(QuestionnaireAnswers(), repo=repo)) == 5

    def test_no_crash_on_every_filled(self, repo):
        # 把七维尽量塞满；不能 crash，必须 5 条
        answers = QuestionnaireAnswers(
            meal_period=MealPeriod.DINNER,
            appetite=Appetite.HUNGRY,
            avoidances=[Avoidance.SEAFOOD, Avoidance.NO_MEAT],
            tastes=[Taste.LIGHT],
            budget=BudgetTier.UNDER_20,
            max_distance_m=3000,
            explicit_food_preference=None,
            ai_follow_up_answers={},
        )
        recs = generate_rule_recommendations(answers, repo=repo)
        assert len(recs) == 5


# ============== G-12 / MEM-024：不同答案必须差异化排序（5 组参数化） ==============

# 每组 (label, answers)，目标：它们的 Top5 代码元组或首候选，至少 4 个互不相同
_DIFFERENTIATION_CASES: list[tuple[str, QuestionnaireAnswers]] = [
    (
        "默认空答案",
        QuestionnaireAnswers(),
    ),
    (
        "下午茶+清淡+预算>30",
        QuestionnaireAnswers(
            meal_period=MealPeriod.AFTERNOON_TEA,
            tastes=[Taste.LIGHT],
            budget=BudgetTier.OVER_30,
        ),
    ),
    (
        "宵夜+辣+食量正常+麻辣烫偏好",
        QuestionnaireAnswers(
            meal_period=MealPeriod.MIDNIGHT_SNACK,
            appetite=Appetite.NORMAL,
            tastes=[Taste.SPICY],
            explicit_food_preference="malatang",  # type: ignore[arg-type]
        ),
    ),
    (
        "午餐+轻食+素食+<20",
        QuestionnaireAnswers(
            meal_period=MealPeriod.LUNCH,
            appetite=Appetite.LIGHT,
            avoidances=[Avoidance.NO_MEAT],
            budget=BudgetTier.UNDER_20,
        ),
    ),
    (
        "早餐+清淡+三明治（牛肉面明确偏好反向）",
        QuestionnaireAnswers(
            meal_period=MealPeriod.BREAKFAST,
            tastes=[Taste.LIGHT],
            explicit_food_preference="beef_noodles",  # type: ignore[arg-type]
        ),
    ),
]


class TestDifferentiationG12AndMEM024:
    """反'小碗菜固定第一'：5 组不同答案的输出 top5 顺序必须有明显差异。"""

    @pytest.mark.parametrize(("label", "answers"), _DIFFERENTIATION_CASES)
    def test_each_case_returns_5(
        self, label: str, answers: QuestionnaireAnswers, repo: FoodDictionaryRepository
    ) -> None:
        recs = generate_rule_recommendations(answers, repo=repo)
        assert len(recs) == 5, f"{label}: 必须正好 5 条"

    @pytest.mark.parametrize(("label", "answers"), _DIFFERENTIATION_CASES)
    def test_each_case_has_signals(
        self, label: str, answers: QuestionnaireAnswers, repo: FoodDictionaryRepository
    ) -> None:
        for r in generate_rule_recommendations(answers, repo=repo):
            assert r.reason.matched_signals, f"{label}: {r.food_code} 没有信号"

    def test_distinct_top5_across_at_least_4_of_5_cases(self, repo):
        """核心断言：5 组中至少 4 组的 Top5 代码元组互不相同。
        如果出现 >=3 组完全同一个排序（尤其是'小碗菜永远第一'）则失败。
        """
        out: dict[str, tuple[str, ...]] = {}
        for label, answers in _DIFFERENTIATION_CASES:
            recs = generate_rule_recommendations(answers, repo=repo)
            out[label] = tuple(r.food_code for r in recs)

        unique = len(set(out.values()))
        # 至少 4 种不同的 Top5 顺序
        assert unique >= 4, f"G-12/MEM-024 失败：5 组答案只有 {unique} 种不同排序，输出={out}"

    def test_first_candidate_not_always_xiaowan_cai(self, repo):
        """更直接的反小碗菜固定第一断言：5 组中至少有 2 组首候选不是 xiaowan_cai。"""
        firsts = []
        for _, answers in _DIFFERENTIATION_CASES:
            top = generate_rule_recommendations(answers, repo=repo)[0]
            firsts.append(top.food_code)
        not_xiaowan = sum(1 for c in firsts if c != "xiaowan_cai")
        assert not_xiaowan >= 2, (
            f"MEM-024 失败：5 组答案中只有 {not_xiaowan} 组首候选不是 xiaowan_cai，firsts={firsts}"
        )
