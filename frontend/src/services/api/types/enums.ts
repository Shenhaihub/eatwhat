/**
 * P2-01 枚举定义。
 * 与后端 `app/schemas/enums.py` 严格 1:1 对应。
 * 任何新增/改名必须先过后端，前端必须后于后端同步，不允许"前端自行扩一个枚举值"。
 */

// G-10 三档预算
export type BudgetTier = 'under_20' | 'from_20_to_30' | 'over_30';

// 名词表 §3.1 七维之一：meal_period
export type MealPeriod =
  | 'breakfast'
  | 'lunch'
  | 'afternoon_tea'
  | 'dinner'
  | 'midnight_snack';

export type Appetite = 'light' | 'normal' | 'hungry';

// G-11 一般忌口（≠ 医学过敏）
export type Avoidance = 'none' | 'seafood' | 'meat' | 'vegetarian';

export type Taste =
  | 'any'
  | 'light'
  | 'spicy'
  | 'sour'
  | 'sweet'
  | 'salty';

// 明确已有 food_code 偏好（G-10：问卷基础题第二题）
export type ExplicitPreference = 'undecided' | 'malatang' | 'beef_noodles';

// G-11 医学过敏项（只在 allergens 出现，不在 avoidances 出现）
export type MedicalAllergen =
  | 'peanut'
  | 'shellfish'
  | 'soy'
  | 'dairy'
  | 'gluten'
  | 'egg';

// 菜系/分类族
export type CuisineGroup =
  | 'chinese_staple'
  | 'noodle'
  | 'hotpot'
  | 'grill'
  | 'fast_food'
  | 'salad'
  | 'asian'
  | 'snack'
  | 'bakery'
  | 'beverage';

// 食物自然时段标签（≠ 用户 MealPeriod）
export type MealTimeTag =
  | 'morning'
  | 'midday'
  | 'tea_time'
  | 'evening'
  | 'late_night'
  | 'anytime';

export type SatietyTag =
  | 'light_meal'
  | 'regular_meal'
  | 'heavy_meal'
  | 'finger_food';

export type BudgetFitStatus = 'fits' | 'uncertain' | 'unlikely';

// G-07 source_type：只允许这四值，由服务端派生
export type SourceType =
  | 'ai_recommended'
  | 'user_selected'
  | 'community_selected'
  | 'activity_selected';

// generation_mode（不是 source_type）
export type GenerationMode = 'ai' | 'rule';
