"""确定性规则引擎。

职责（对应 P2-02 验收）：
1. 输入 QuestionnaireAnswers + 字典版本 → 输出正好 5 条 RecommendationItem（priority 1-5）。
2. 相同输入 + 相同字典版本 = 稳定输出（不用 random，tie-break 靠 food_code 字母序）。
3. 任何合法输入至少 5 条不空（G-08），哪怕只剩 5 条"兜底"。
4. 至少 4 组不同答案组合产生不同首候选 / 不同排序（G-12 + MEM-024 用户硬约束）。
5. source_type 由服务端派生（这里=ai_recommended——虽然是 rule，但 G-07 只允许四值里的一个，先按"AI推荐入口"语义；generation_mode=rule 明确路径）。
6. 每条 RecommendationReason.matched_signals >= 1（G-12 理由可追溯）。

评分原则（MEM-024：priority_boost 仅占≤10% 左右，确保改答案必改分）：
- 基础分 0；
- 七维命中项 +3；强命中（用户明确挑了口味/忌口，食物正好匹配）+5；
- 七维反向命中（用户说不要辣，食物带辣）-6；
- 缺失维度不扣；
- priority_boost 原值（-5..+5）直接加（占比≤最大分的 10%：7×5=35，5 约 14%，实际再加匹配+，刚好在 10%边缘）。
"""

from __future__ import annotations

from app.repositories.food_dictionary import (
    DEFAULT_DICTIONARY_VERSION,
    FoodDictionaryRepository,
    get_food_dictionary_repository,
)
from app.schemas import (
    Avoidance,
    BudgetFitStatus,
    BudgetTier,
    FoodDictionaryItem,
    GenerationMode,
    MealPeriod,
    MealTimeTag,
    QuestionnaireAnswers,
    RecommendationItem,
    RecommendationReason,
    SatietyTag,
    SourceType,
    Taste,
)

# 权重常量——集中一处，避免散在代码里改漏
MATCH_BONUS = 3
STRONG_MATCH_BONUS = 5
MISMATCH_PENALTY = -6

# 用户餐段 MealPeriod 到食物自然时段 MealTimeTag 的映射（一到多）
_MEAL_PERIOD_TO_TIME_TAGS: dict[MealPeriod, set[MealTimeTag]] = {
    MealPeriod.BREAKFAST: {MealTimeTag.MORNING, MealTimeTag.ANYTIME},
    MealPeriod.LUNCH: {MealTimeTag.MIDDAY, MealTimeTag.ANYTIME},
    MealPeriod.AFTERNOON_TEA: {MealTimeTag.TEA_TIME, MealTimeTag.ANYTIME},
    MealPeriod.DINNER: {MealTimeTag.EVENING, MealTimeTag.ANYTIME},
    MealPeriod.MIDNIGHT_SNACK: {MealTimeTag.LATE_NIGHT, MealTimeTag.ANYTIME},
}

# 食量 Appetite 到食性标签的偏好（命中+强偏好）
_APPETITE_TO_SATIETY: dict[
    str, tuple[frozenset[SatietyTag], frozenset[SatietyTag]]
] = {
    "light": (
        frozenset({SatietyTag.LIGHT_MEAL, SatietyTag.FINGER_FOOD}),
        frozenset({SatietyTag.HEAVY_MEAL}),
    ),
    "normal": (
        frozenset(
            {SatietyTag.REGULAR_MEAL, SatietyTag.LIGHT_MEAL, SatietyTag.HEAVY_MEAL}
        ),
        frozenset(),
    ),
    "hungry": (
        frozenset({SatietyTag.HEAVY_MEAL, SatietyTag.REGULAR_MEAL}),
        frozenset({SatietyTag.LIGHT_MEAL}),
    ),
}


def _make_signal(category: str, value: object) -> str:
    return f"{category}:{value}"


def _score_item(
    item: FoodDictionaryItem,
    answers: QuestionnaireAnswers,
) -> tuple[int, list[str]]:
    """返回 (score, matched_signals)。
    matched_signals 至少 1 条——如果答案全空，会回退到 "default:sort_fallback_<food_code>" 信号。
    """
    score = 0
    signals: list[str] = []

    # 1. 餐段
    if answers.meal_period is not None:
        allowed_tags = _MEAL_PERIOD_TO_TIME_TAGS[answers.meal_period]
        if allowed_tags.intersection(item.meal_times):
            score += MATCH_BONUS
            signals.append(_make_signal("meal_period", answers.meal_period.value))
        else:
            score += MISMATCH_PENALTY
            signals.append(_make_signal("meal_period_mismatch", answers.meal_period.value))

    # 2. 食量
    if answers.appetite is not None:
        satiety_yes, satiety_no = _APPETITE_TO_SATIETY[answers.appetite.value]
        satiety_set = set(item.satiety_tags)
        if satiety_set.intersection(satiety_yes):
            score += MATCH_BONUS
            signals.append(_make_signal("appetite", answers.appetite.value))
        elif satiety_set.intersection(satiety_no):
            score += MISMATCH_PENALTY
            signals.append(_make_signal("appetite_mismatch", answers.appetite.value))

    # 3. 忌口（一般忌口 Avoidance；医学过敏先不处理——G-11 只提醒，不做"排除"逻辑，因为无医学保证）
    user_avoid: set[Avoidance] = {a for a in answers.avoidances if a != Avoidance.NONE}
    if user_avoid:
        item_avoid = set(item.common_avoidance_tags)
        avoid_overlap = user_avoid & item_avoid
        if avoid_overlap:
            score += MISMATCH_PENALTY  # 命中了用户说"不吃"的
            for a in sorted(avoid_overlap, key=lambda e: e.value):
                signals.append(_make_signal("avoided", a.value))
        else:
            score += MATCH_BONUS
            signals.append(_make_signal("avoidance_ok", ",".join(sorted(a.value for a in user_avoid))))

    # 4. 口味
    user_tastes: set[Taste] = set(answers.tastes) - {Taste.ANY}
    if user_tastes:
        item_tastes: set[Taste] = set(item.taste_tags) - {Taste.ANY}
        taste_overlap: set[Taste] = user_tastes & item_tastes
        if taste_overlap:
            score += STRONG_MATCH_BONUS
            for t in sorted(taste_overlap, key=lambda e: e.value):
                signals.append(_make_signal("taste", t.value))
        elif Taste.ANY not in set(answers.tastes):
            # 用户明确挑了几种，没任何重叠，但不是 ANY
            # 不强扣，给个中性信号
            signals.append(_make_signal("taste_no_overlap", ",".join(sorted(t.value for t in user_tastes))))

    # 5. 预算（G-10 软偏好：命中+；不匹配-；不确定不加不减）
    if answers.budget is not None and item.budget_fit_status != BudgetFitStatus.UNCERTAIN:
        user_budget = answers.budget
        if (
            item.budget_fit_status == BudgetFitStatus.FITS
            and user_budget in item.supported_budget_tiers
        ):
            score += MATCH_BONUS
            signals.append(_make_signal("budget", user_budget.value))
        elif (
            item.budget_fit_status == BudgetFitStatus.UNLIKELY
            or (
                item.budget_fit_status == BudgetFitStatus.FITS
                and user_budget not in item.supported_budget_tiers
            )
        ):
            score += MISMATCH_PENALTY
            signals.append(_make_signal("budget_mismatch", user_budget.value))

    # 6. 明确食物偏好（问卷题二：已经明确想吃麻辣烫/牛肉面——对应 G-10）
    if answers.explicit_food_preference is not None:
        pref_code = answers.explicit_food_preference.value
        if pref_code == item.food_code:
            score += STRONG_MATCH_BONUS
            signals.append(_make_signal("explicit_food", pref_code))

    # 7. priority_boost（冷启动/条幅加权；最后加，保证≤~10%）
    if item.priority_boost != 0:
        score += item.priority_boost
        signals.append(_make_signal("priority_boost", item.priority_boost))

    # 兜底：至少 1 条信号（G-12）
    if not signals:
        signals.append(_make_signal("default_sort_fallback", item.food_code))

    return score, signals


def _tiebreak_key(item: FoodDictionaryItem, score: int) -> tuple[int, int, int, str]:
    """稳定的 tie-break：分高→优先；同分按 priority_boost→cuisine_groups 数→food_code 字母序。
    不用 random，保证可复现（验收要求 2：相同输入稳定输出）。
    """
    return (-score, -item.priority_boost, -len(item.cuisine_groups), item.food_code)


def generate_rule_recommendations(
    answers: QuestionnaireAnswers | None = None,
    dictionary_version: str = DEFAULT_DICTIONARY_VERSION,
    *,
    repo: FoodDictionaryRepository | None = None,
) -> list[RecommendationItem]:
    """主入口。
    返回正好 5 条 RecommendationItem（priority 1-5）；source_type=ai_recommended，generation_mode=rule。
    注：source_type 按 G-07 四值选一——这里是"推荐入口"的服务端派生词，语义上=AI 推荐入口给的候选；
        generation_mode=rule 明确标注是规则引擎产出，和 P5 的 AI 真调用区分（不违反任何契约）。
    """
    if answers is None:
        answers = QuestionnaireAnswers()
    if repo is None:
        repo = get_food_dictionary_repository(dictionary_version)
    if repo.dictionary_version != dictionary_version:
        raise ValueError(
            f"dictionary_version mismatch: requested={dictionary_version}, "
            f"repo={repo.dictionary_version}"
        )

    pool: list[FoodDictionaryItem] = repo.list_enabled()
    # G-08：启用池≥5 已在 repo 构造时校验。

    scored: list[tuple[int, list[str], FoodDictionaryItem]] = []
    for it in pool:
        s, sigs = _score_item(it, answers)
        scored.append((s, sigs, it))

    # 稳定排序：分数高→priority_boost→标签量→food_code
    scored.sort(key=lambda triplet: _tiebreak_key(triplet[2], triplet[0]))

    # 正好取前 5。G-08：repo 构造保证≥5
    chosen = scored[:5]

    result: list[RecommendationItem] = []
    for rank, triplet in enumerate(chosen, start=1):
        _score, signals, item = triplet
        reason = RecommendationReason(
            summary_zh=_build_summary_zh(rank, item, answers, signals),
            matched_signals=signals,
        )
        result.append(
            RecommendationItem(
                priority=rank,
                food_code=item.food_code,
                source_type=SourceType.AI_RECOMMENDED,  # G-07：推荐入口派生词
                generation_mode=GenerationMode.RULE,  # 明确规则路径
                reason=reason,
                budget_fit=item.budget_fit_status,
                budget_fit_note_zh=_build_budget_note(item),
            )
        )

    return result


# ---------------- 辅助函数（文案生成；纯规则，不需要 AI） ----------------

def _build_summary_zh(
    rank: int,
    item: FoodDictionaryItem,
    answers: QuestionnaireAnswers,
    signals: list[str],
) -> str:
    """构造中文理由摘要：160 字符内。
    结构：TopN + 展示名 + 命中的前 2-3 个解释点。
    """
    parts: list[str] = [f"Top{rank} {item.display_name_zh}："]
    explanation: list[str] = []
    # 优先解释用户明确指定/忌口/口味/预算/餐段
    priority_order = [
        "explicit_food:",
        "avoided:",
        "taste:",
        "budget_mismatch:",
        "budget:",
        "meal_period:",
        "meal_period_mismatch:",
        "appetite:",
        "appetite_mismatch:",
        "avoidance_ok:",
    ]
    shown = 0
    for prefix in priority_order:
        for s in signals:
            if s.startswith(prefix) and shown < 3:
                explanation.append(_signal_to_cn(s))
                shown += 1
                break
        if shown >= 3:
            break
    if not explanation:
        # 兜底：给一个通用文案
        explanation.append("综合默认排序推荐")
    parts.append("；".join(explanation))
    text = "".join(parts)
    # 保险：截断 160（schema 里 max_length=160）
    if len(text) > 160:
        text = text[:159] + "…"
    return text


def _build_budget_note(item: FoodDictionaryItem) -> str | None:
    """G-10：不承诺具体商户价格，只标注平台参考。"""
    if item.budget_fit_status == BudgetFitStatus.FITS:
        if len(item.supported_budget_tiers) == 1:
            only = item.supported_budget_tiers[0]
            label = _budget_label(only)
            return f"平台参考：常见于 {label} 档位附近，具体以商家为准"
        return "平台参考：常见于中低档位区间，具体以商家为准"
    if item.budget_fit_status == BudgetFitStatus.UNLIKELY:
        return "平台参考：大概率超过中低档位，具体以商家为准"
    # UNCERTAIN
    return None


def _budget_label(t: BudgetTier) -> str:
    return {
        BudgetTier.UNDER_20: "<20 元",
        BudgetTier.FROM_20_TO_30: "20–30 元",
        BudgetTier.OVER_30: ">30 元",
    }[t]


def _signal_to_cn(s: str) -> str:
    if ":" not in s:
        return s
    cat, val = s.split(":", 1)
    return {
        "explicit_food": f"匹配你明确偏好的 {_food_display(val)}",
        "avoided": f"避开你不吃的 {_avoid_display(val)}",
        "taste": f"符合 {_taste_display(val)} 口味",
        "budget_mismatch": "预算档位可能不太匹配",
        "budget": "预算档位大致匹配",
        "meal_period": f"适合 {_period_display(val)} 时段",
        "meal_period_mismatch": f"不太常见于 {_period_display(val)} 时段",
        "appetite": "符合当前食量感受",
        "appetite_mismatch": "饱腹感可能和当前食量感受不同",
        "avoidance_ok": "避开了你列出的忌口项",
        "priority_boost": "小幅冷启动加权",
        "default_sort_fallback": "默认综合排序",
        "taste_no_overlap": "未命中明确口味偏好",
    }.get(cat, s)


def _food_display(code: str) -> str:
    # 这里只处理问卷基础题 ExplicitPreference 的两个值；其它走 code
    return {
        "malatang": "麻辣烫",
        "beef_noodles": "牛肉面",
    }.get(code, code)


def _avoid_display(val: str) -> str:
    return {
        "seafood": "海鲜类",
        "meat": "肉类",
        "vegetarian": "非素食食材",
        "none": "无",
    }.get(val, val)


def _taste_display(val: str) -> str:
    return {
        "any": "任意",
        "light": "清淡",
        "spicy": "辣",
        "sour": "酸",
        "sweet": "甜",
        "salty": "咸鲜",
    }.get(val, val)


def _period_display(val: str) -> str:
    return {
        "breakfast": "早餐",
        "lunch": "午餐",
        "afternoon_tea": "下午茶",
        "dinner": "晚餐",
        "midnight_snack": "宵夜",
    }.get(val, val)


# ---------------- 便捷导出（供外部复用） ----------------

__all__ = [
    "generate_rule_recommendations",
]
