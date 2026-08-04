"""P2-01 测试：食物字典、枚举边界、加载器行为。
覆盖的契约点：
- G-08：启用池 ≥5 条（规则回退不空）
- G-11：医学忌口分层（allergens ↔ avoidances）、safety note 不能绝对化承诺
- G-12：至少存在 ≥2 个不同 cuisine_group、≥2 种预算支持模式，为差异化做准备
- food_code 唯一性、版本号
"""

from __future__ import annotations

import pytest

from app.repositories.food_dictionary import (
    DEFAULT_DICTIONARY_VERSION,
    get_food_dictionary_repository,
)
from app.schemas import (
    Avoidance,
    BudgetTier,
    FoodDictionaryItem,
    GenerationMode,
    MedicalAllergen,
    RecommendationItem,
    RecommendationReason,
    SourceType,
    ValidationHelpers,
)

# ========== 契约枚举 ==========

class TestEnums:
    def test_budget_tier_three_values_only(self):
        assert {e.value for e in BudgetTier} == {"under_20", "from_20_to_30", "over_30"}

    def test_source_type_four_values_only(self):
        assert {e.value for e in SourceType} == {
            "ai_recommended",
            "user_selected",
            "community_selected",
            "activity_selected",
        }

    def test_generation_mode_excludes_rule_from_source(self):
        """G-07：rule_fallback 不是 source_type，是 generation_mode。"""
        assert "rule" in {e.value for e in GenerationMode}
        assert "rule_fallback" not in {e.value for e in SourceType}

    def test_medical_allergen_not_in_avoidance(self):
        """G-11：医学过敏项不应出现在一般忌口枚举中，反之亦然。"""
        a = {e.value for e in Avoidance}
        m = {e.value for e in MedicalAllergen}
        assert a.isdisjoint(m), f"忌口与过敏枚举交叉：{a & m}"


# ========== 真实字典校验 ==========

@pytest.fixture(scope="module")
def dict_repo():
    get_food_dictionary_repository.cache_clear()
    return get_food_dictionary_repository(DEFAULT_DICTIONARY_VERSION)


class TestRealFoodDictionary:
    def test_enabled_pool_at_least_five(self, dict_repo):
        # G-08：启用条目至少 5 条
        assert dict_repo.enabled_count() >= 5

    def test_all_food_codes_unique(self, dict_repo):
        codes = [it.food_code for it in dict_repo.list_all()]
        assert len(codes) == len(set(codes))

    def test_medical_boundary_passed_on_load(self, dict_repo):
        # 构造时会抛；这里显式调用一遍说明契约
        ValidationHelpers.validate_medical_boundary(dict_repo.list_all())

    def test_cuisine_group_variety_for_g12(self, dict_repo):
        # G-12 准备：至少 2 个菜系族，保证后续规则引擎有差异化素材
        groups = {g for it in dict_repo.list_enabled() for g in it.cuisine_groups}
        assert len(groups) >= 2, f"菜系族单一：{groups}"

    def test_budget_support_variety(self, dict_repo):
        # 至少有一种食物 under_20，至少有一种 over_30，保证预算差异化素材
        tiers = {t for it in dict_repo.list_enabled() for t in it.supported_budget_tiers}
        assert {BudgetTier.UNDER_20, BudgetTier.OVER_30} <= tiers

    def test_no_number_range_fields(self, dict_repo):
        # G-11：食物条目不使用 budget_min/max 数字区间
        for it in dict_repo.list_all():
            raw = it.model_dump()
            assert "budget_min" not in raw
            assert "budget_max" not in raw

    def test_prototype_foods_present(self, dict_repo):
        # 原型推荐/社区/活动里的 11 种食物类型都必须在字典里
        required = {
            "xiaowan_cai",  # 小碗菜
            "beef_noodles",  # 牛肉面
            "huangmen_chicken",  # 黄焖鸡
            "casserole_rice_noodles",  # 砂锅米线
            "salad_rice_bowl",  # 轻食饭碗（推荐）
            "malatang",  # 麻辣烫（社区+问卷）
            "burger",  # 汉堡（社区）
            "salad",  # 轻食沙拉（社区）
            "bbq",  # 烧烤（社区）
            "fried_chicken",  # 炸鸡（活动：周四）
            "sushi",  # 寿司（差异化高价）
        }
        missing = [c for c in required if not dict_repo.contains_enabled(c)]
        assert not missing, f"原型中的关键食物缺失：{missing}"


# ========== 加载器行为 ==========

class TestFoodDictionaryRepository:
    def test_get_and_require(self, dict_repo):
        item = dict_repo.get("malatang")
        assert item is not None
        assert item.display_name_zh == "麻辣烫"
        assert dict_repo.require("malatang").food_code == "malatang"

    def test_require_raises_missing(self, dict_repo):
        with pytest.raises(KeyError):
            dict_repo.require("not_exist_xyz")

    def test_validate_food_codes_mixed(self, dict_repo):
        bad = dict_repo.validate_food_codes(["malatang", "burger", "ghost_code", "xiaowan_cai"])
        assert bad == ["ghost_code"]

    def test_codes_enabled_deterministic_and_nonempty(self, dict_repo):
        codes = dict_repo.codes_enabled()
        assert len(codes) >= 5
        # 顺序稳定（按 JSON 出现顺序）
        assert codes[0] == "xiaowan_cai"
        assert codes[-1] == "pizza"


# ========== ValidationHelpers 负向 ==========

def _make_item(**overrides: object) -> FoodDictionaryItem:
    base = {
        "dictionary_version": "v1.0",
        "food_code": "f_x",
        "display_name_zh": "示例",
        "emoji": "🍜",
        "cuisine_groups": ["chinese_staple"],
        "meal_times": ["midday"],
        "satiety_tags": ["regular_meal"],
        "taste_tags": ["any"],
        "common_avoidance_tags": [],
        "medical_allergen_tags": [],
        "supported_budget_tiers": ["from_20_to_30"],
        "budget_fit_status": "uncertain",
        "budget_fit_source": "unit-test",
        "is_enabled": True,
    }
    base.update(overrides)  # type: ignore[arg-type]
    return FoodDictionaryItem.model_validate(base)


class TestValidationHelpersNegative:
    def test_duplicate_food_codes_raises(self):
        a = _make_item(food_code="dup")
        b = _make_item(food_code="dup")
        with pytest.raises(ValueError, match="重复的 food_code"):
            ValidationHelpers.validate_unique_food_codes([a, b])

    def test_medical_allergen_without_note_raises(self):
        item = _make_item(
            medical_allergen_tags=[MedicalAllergen.SOY.value],
            medical_safety_note_zh=None,
        )
        with pytest.raises(ValueError, match="缺少 medical_safety_note_zh"):
            ValidationHelpers.validate_medical_boundary([item])

    def test_medical_safety_absolute_promise_raises(self):
        item = _make_item(
            medical_allergen_tags=[MedicalAllergen.SOY.value],
            medical_safety_note_zh="本品绝对安全，100% 不含过敏原",
        )
        with pytest.raises(ValueError, match="绝对化承诺词"):
            ValidationHelpers.validate_medical_boundary([item])

    def test_enabled_pool_too_small_raises(self):
        disabled = _make_item(food_code="f1", is_enabled=False)
        ok = _make_item(food_code="f2", is_enabled=True)
        with pytest.raises(ValueError, match="只有 1 条"):
            ValidationHelpers.validate_enabled_pool_size([disabled, ok], min_size=5)


# ========== Recommendation schema 契约 ==========

class TestRecommendationSchema:
    def test_priority_must_be_1_to_5(self):
        payload = {
            "priority": 0,
            "food_code": "xiaowan_cai",
            "source_type": SourceType.RULE.value if False else SourceType.AI_RECOMMENDED.value,
            "generation_mode": GenerationMode.AI.value,
            "reason": {"summary_zh": "示例", "matched_signals": ["meal_period:lunch"]},
        }
        with pytest.raises(ValueError):
            RecommendationItem.model_validate(payload)

    def test_reason_requires_at_least_one_signal_g12(self):
        # G-12：理由必须至少 1 个可追溯信号，不允许"推荐了但无法解释为什么"
        with pytest.raises(ValueError):
            RecommendationReason.model_validate(
                {"summary_zh": "没有任何信号", "matched_signals": []}
            )

    def test_extra_fields_forbidden(self):
        # schema 统一用 extra="forbid"，防止客户端/服务端悄悄塞未约定字段
        payload = {
            "priority": 1,
            "food_code": "xiaowan_cai",
            "source_type": SourceType.AI_RECOMMENDED.value,
            "generation_mode": GenerationMode.AI.value,
            "reason": {"summary_zh": "示例", "matched_signals": ["x"]},
            "unknown_extra": "oops",
        }
        with pytest.raises(ValueError):
            RecommendationItem.model_validate(payload)
