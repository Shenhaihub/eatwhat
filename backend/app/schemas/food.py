"""问卷答案与推荐输出的 Pydantic schema。
严格对应 00 名词表 §3.1 七维字段；不使用字典代替 schema。
注意：客户端只能传入口意图（entry_point），不能传 source_type（G-07）。
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.enums import (
    Appetite,
    Avoidance,
    BudgetFitStatus,
    BudgetTier,
    CuisineGroup,
    ExplicitPreference,
    GenerationMode,
    MealPeriod,
    MealTimeTag,
    MedicalAllergen,
    SatietyTag,
    SourceType,
    Taste,
)

# =============== 入口意图（客户端只能传这个，不传 source_type，G-07） ===============

ENTRY_POINT_INTENT_VALUES = frozenset(
    {"home_recommend", "home_community", "home_activity", "direct_food"}
)


def is_valid_entry_point(value: str) -> bool:
    return value in ENTRY_POINT_INTENT_VALUES


# =============== 问卷答案（输入） ===============

class QuestionnaireAnswers(BaseModel):
    """预设问卷 + AI 追问的完整答案快照。
    未回答的维度留空；服务端每次重算时从 answers_so_far 推断。
    """

    model_config = ConfigDict(extra="forbid")

    questionnaire_version: str = Field(
        default="v1.0",
        min_length=1,
        max_length=32,
        description="问卷版本，决定问题库、条件表和解释方式",
    )
    meal_period: MealPeriod | None = None
    appetite: Appetite | None = None
    avoidances: list[Avoidance] = Field(
        default_factory=list,
        description="一般忌口（日常不吃），不构成医学过敏保证（G-11）",
    )
    tastes: list[Taste] = Field(default_factory=list)
    budget: BudgetTier | None = None
    # P1 修复：菜系偏好（多选）—— 来自新增 q07_cuisine_preference 题目或 AI 追问
    # 匹配逻辑：若用户显式选择且与 food.cuisine_groups 无交集 → 大扣分；有交集 → 大加分
    cuisine_preferences: list[CuisineGroup] = Field(default_factory=list)
    max_distance_m: int | None = Field(
        default=None,
        ge=500,
        le=50_000,
        description="最大距离偏好，单位米（仅 POI 阶段使用；推荐引擎暂不处理）",
    )
    explicit_food_preference: ExplicitPreference | None = Field(
        default=None,
        description="是否已经有明确 food_code（ExplicitPreference.UNDECIDED 表示仍需要推荐）",
    )
    ai_follow_up_answers: dict[str, Any] = Field(
        default_factory=dict,
        description="AI 逐题追问的答案，question_id -> value；业务 schema 在 P2-03/P5 再细化",
    )


# =============== 食物字典条目 ===============

class FoodDictionaryItem(BaseModel):
    """P2-01 核心交付物：食物字典条目。
    字段命名严格遵循 00 名词表 + 22 收敛清单 G-07~G-16：
    - food_code（稳定机器码，不用中文当主键）
    - allergens 单独保留，不和 avoidances 混（G-11 医学边界）
    - budget 用 supported_budget_tiers + budget_fit_* 三件套，不用数字区间（G-11）
    - cuisine_group / meal_time / satiety 为 G-12（差异化）准备的可追溯标签
    """

    model_config = ConfigDict(extra="forbid")

    dictionary_version: str = Field(
        min_length=1,
        max_length=32,
        description="字典版本号，规则引擎/AI 生成均绑定具体版本",
    )
    food_code: str = Field(
        ...,
        pattern=r"^[a-z0-9_]{2,40}$",
        description="稳定机器代码；不使用中文作为主键（名词表 §2）",
    )
    display_name_zh: str = Field(..., min_length=1, max_length=32)
    display_name_en: str | None = Field(default=None, max_length=64)
    emoji: str | None = Field(default=None, max_length=4)

    cuisine_groups: list[CuisineGroup] = Field(default_factory=list)
    meal_times: list[MealTimeTag] = Field(default_factory=list)
    satiety_tags: list[SatietyTag] = Field(default_factory=list)
    taste_tags: list[Taste] = Field(default_factory=list)

    # G-11：两件套忌口/过敏，严格分层
    #   avoidances 列在食物自身"通常包含"的日常不吃项（非医学）
    #   allergens 列在"含过敏原"，是医学安全信息；两类绝不混用
    common_avoidance_tags: list[Avoidance] = Field(
        default_factory=list,
        description="食物通常包含哪些一般忌口项（不含过敏）；比如 SEAFOOD 表示通常含海鲜",
    )
    medical_allergen_tags: list[MedicalAllergen] = Field(
        default_factory=list,
        description="医学过敏原标签；不等于对用户的医学安全承诺（仍需要用户自行判断）",
    )
    medical_safety_note_zh: str | None = Field(
        default=None,
        max_length=128,
        description="（可选）对 allergens 的中文免责提醒，禁止写成医学安全保证",
    )

    # G-11 预算软匹配三件套；无 min/max 数字区间
    supported_budget_tiers: list[BudgetTier] = Field(default_factory=list)
    budget_fit_status: BudgetFitStatus = Field(
        default=BudgetFitStatus.UNCERTAIN,
        description="软匹配状态；依据不足时必须为 UNCERTAIN（G-11）",
    )
    budget_fit_source: str | None = Field(
        default=None,
        max_length=64,
        description="预算匹配依据来源（如'内部常识 v1.0'），来源为空或未知时整体 status 必须 UNCERTAIN",
    )
    budget_fit_updated_at: datetime | None = None

    is_enabled: bool = Field(
        default=True,
        description="是否参与推荐；用于灰度/下线，不物理删除（G-08 非空回退时仍可排除）",
    )
    priority_boost: int = Field(
        default=0,
        ge=-5,
        le=5,
        description="默认排序加权（冷启动/社区活动条幅优先），不改变 G-12 的差异化要求",
    )


# =============== 推荐候选（输出） ===============

class RecommendationReason(BaseModel):
    """推荐理由的结构化描述——为 G-12 差异化与可追溯准备。
    不允许自由文本理由无法对应具体答案组合。
    """

    model_config = ConfigDict(extra="forbid")

    summary_zh: str = Field(..., min_length=1, max_length=160)
    matched_signals: list[str] = Field(
        ...,
        min_length=1,
        description="命中的具体信号，来自用户答案/标签/时段，例如 'taste:spicy'、'meal_period:lunch'；至少 1 个（G-12）",
    )


class RecommendationItem(BaseModel):
    """G-02：正好 5 条，priority 1–5、food_code 唯一且均在启用词典内。
    G-07：source_type 由服务端派生，generation_mode 表示是否走了规则回退。
    """

    model_config = ConfigDict(extra="forbid")

    priority: int = Field(..., ge=1, le=5)
    food_code: str = Field(..., pattern=r"^[a-z0-9_]{2,40}$")
    source_type: SourceType
    generation_mode: GenerationMode
    reason: RecommendationReason

    # G-11：预算软匹配展示字段；不展示商家价格
    budget_fit: BudgetFitStatus = BudgetFitStatus.UNCERTAIN
    budget_fit_note_zh: str | None = Field(
        default=None,
        max_length=128,
        description="仅标注平台参考，不承诺具体商户价格；默认值为 null（G-10）",
    )


# =============== 入口意图（客户端只能传这个，不传 source_type，G-07） ===============

class EntryPointIntent(str):
    """字符串子类型占位；实际校验在 P2-04 API 路由层做。
    允许值：home_recommend / home_community / home_activity / direct_food。
    """


# =============== 共享工具校验器（跨文件复用） ===============

class ValidationHelpers:
    @staticmethod
    def validate_unique_food_codes(items: list[FoodDictionaryItem]) -> None:
        codes = [it.food_code for it in items]
        dupes = sorted({c for c in codes if codes.count(c) > 1})
        if dupes:
            raise ValueError(f"重复的 food_code：{dupes}")

    @staticmethod
    def validate_medical_boundary(items: list[FoodDictionaryItem]) -> None:
        """G-11：医学边界检查：
        1) 有 allergens 时必须有 medical_safety_note_zh（提醒用户不能当医学保证）；
        2) safety note 不能出现绝对化承诺词（治愈/安全/无过敏/100%/绝对安全/不含）。
        """
        forbidden = ("治愈", "绝对安全", "100%", "无过敏", "不含过敏原", "完全安全")
        for it in items:
            if it.medical_allergen_tags and not it.medical_safety_note_zh:
                raise ValueError(
                    f"{it.food_code} 设了 medical_allergen_tags 但缺少 medical_safety_note_zh"
                )
            note = it.medical_safety_note_zh or ""
            for w in forbidden:
                if w in note:
                    raise ValueError(
                        f"{it.food_code} 的 medical_safety_note_zh 含绝对化承诺词 '{w}'（G-11）"
                    )

    @staticmethod
    def validate_enabled_pool_size(items: list[FoodDictionaryItem], min_size: int = 5) -> None:
        """G-08：启用条目至少 5 条，保证规则回退不会空。"""
        enabled = [it for it in items if it.is_enabled]
        if len(enabled) < min_size:
            raise ValueError(
                f"启用条目只有 {len(enabled)} 条，少于 G-08 要求的最小 {min_size} 条"
            )
