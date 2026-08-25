"""项目级枚举：严格对应 00 名词表、22 收敛清单 G-07~G-16。
仅保留首版实际使用的枚举值，不提前扩展。
"""

from enum import StrEnum


# G-10 三档预算
class BudgetTier(StrEnum):
    UNDER_20 = "under_20"
    FROM_20_TO_30 = "from_20_to_30"
    OVER_30 = "over_30"


# 名词表 §3.1 七个信息维度（问卷答案值的子集）
class MealPeriod(StrEnum):
    BREAKFAST = "breakfast"
    LUNCH = "lunch"
    AFTERNOON_TEA = "afternoon_tea"
    DINNER = "dinner"
    MIDNIGHT_SNACK = "midnight_snack"


class Appetite(StrEnum):
    LIGHT = "light"
    NORMAL = "normal"
    HUNGRY = "hungry"


# G-11 忌口只记录"日常不吃"，不构成过敏/医疗保证
class Avoidance(StrEnum):
    NONE = "none"
    SEAFOOD = "seafood"
    NO_MEAT = "meat"
    VEGETARIAN = "vegetarian"


class Taste(StrEnum):
    ANY = "any"
    LIGHT = "light"
    SPICY = "spicy"
    SOUR = "sour"
    SWEET = "sweet"
    SALTY = "salty"
    GARLIC = "garlic"  # P1 扩充：蒜香（韩式烤肉/蒜蓉菜常见，用户可能明确说"不要蒜"）


class ExplicitPreference(StrEnum):
    UNDECIDED = "undecided"
    MALATANG = "malatang"
    BEEF_NOODLES = "beef_noodles"


# 标签系统：区分"医学安全承诺"和"普通偏好"（G-11）
# MedicalAvoidance 只用于 allergens 字段，不与 avoidances 混用
class MedicalAllergen(StrEnum):
    PEANUT = "peanut"
    SHELLFISH = "shellfish"
    SOY = "soy"
    DAIRY = "dairy"
    GLUTEN = "gluten"
    EGG = "egg"
    # P1 扩充：常见东亚饮食过敏原/敏感成分（非 8 大类但免责提醒很重要）
    SESAME = "sesame"        # 芝麻（FDA 2023 起列为美国主要过敏原第 9 类）
    BUCKWHEAT = "buckwheat"  # 荞麦（日本/韩国常见过敏原标签）
    GARLIC = "garlic"        # 大蒜（敏感人群不少，归到allergen用于免责）
    PORK = "pork"            # 猪肉（宗教/过敏 双重原因）
    SAUSAGE = "sausage"      # 香肠/加工肉（含亚硝酸盐+猪成分，免责标识用）


# 食物菜系/分类族（为规则引擎和 G-12 差异化准备）
class CuisineGroup(StrEnum):
    CHINESE_STAPLE = "chinese_staple"
    NOODLE = "noodle"
    HOTPOT = "hotpot"
    GRILL = "grill"
    FAST_FOOD = "fast_food"
    SALAD = "salad"
    ASIAN = "asian"
    SNACK = "snack"
    BAKERY = "bakery"
    BEVERAGE = "beverage"
    # P1 修复：菜系偏好显式字段——用户在问卷里选择"日韩/西餐/中餐"时，规则引擎能据此强惩罚不匹配
    JAPANESE = "japanese"
    KOREAN = "korean"
    WESTERN = "western"


# 常见食用时段（由食物自然场景决定，规则引擎再按用户偏好加权）
# 注意：与用户餐段 MealPeriod（breakfast/lunch/afternoon_tea/dinner/midnight_snack）区分：
# 后者是用户"此时此刻在吃哪顿饭"的问题答案；前者是食物"通常出现在哪些时段场景"的标签。
class MealTimeTag(StrEnum):
    MORNING = "morning"
    MIDDAY = "midday"
    TEA_TIME = "tea_time"
    EVENING = "evening"
    LATE_NIGHT = "late_night"
    ANYTIME = "anytime"


# 软/饱腹等食性标签（用于 appetite 维度匹配）
class SatietyTag(StrEnum):
    LIGHT_MEAL = "light_meal"
    REGULAR_MEAL = "regular_meal"
    HEAVY_MEAL = "heavy_meal"
    FINGER_FOOD = "finger_food"


# 预算软匹配状态（G-11：不确定时标 uncertain，不硬写符合）
class BudgetFitStatus(StrEnum):
    FITS = "fits"
    UNCERTAIN = "uncertain"
    UNLIKELY = "unlikely"


# G-07 source_type 只允许这四值，由服务端派生
class SourceType(StrEnum):
    AI_RECOMMENDED = "ai_recommended"
    USER_SELECTED = "user_selected"
    COMMUNITY_SELECTED = "community_selected"
    ACTIVITY_SELECTED = "activity_selected"


# 生成模式（不是 source_type，单独放 generation_mode，G-07）
class GenerationMode(StrEnum):
    AI = "ai"
    RULE = "rule"
