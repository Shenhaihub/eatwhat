/**
 * P2-01 问卷答案与推荐输出类型。
 * 与后端 `app/schemas/food.py` 严格 1:1。
 *
 * 关键约束（契约上与后端一致，前端只消费不派生）：
 * - 客户端不能传 `source_type`（G-07）；只能传入口意图 `entry_point`。
 * - 食物字典条目禁止使用中文当主键，只认 `food_code`（蛇形小写英文+数字+下划线）。
 * - 医学过敏与一般忌口分层，两者绝不混用（G-11）。
 * - 推荐理由必须给出至少 1 个可追溯信号 matched_signals（G-12）。
 * - 推荐候选必须正好 5 个，priority 1–5（G-02/P2 验收）。
 */

import type {
  Appetite,
  Avoidance,
  BudgetFitStatus,
  BudgetTier,
  CuisineGroup,
  GenerationMode,
  MealPeriod,
  MealTimeTag,
  MedicalAllergen,
  SatietyTag,
  SourceType,
  Taste,
} from './enums';

// ============== 入口意图（客户端只能传这个，不传 source_type，G-07） ==============

export const ENTRY_POINT_INTENT_VALUES = [
  'home_recommend',
  'home_community',
  'home_activity',
  'direct_food',
] as const;

export type EntryPointIntent = (typeof ENTRY_POINT_INTENT_VALUES)[number];

export function isValidEntryPoint(value: string): value is EntryPointIntent {
  return (ENTRY_POINT_INTENT_VALUES as readonly string[]).includes(value);
}

// ============== 问卷答案（输入） ==============

export interface QuestionnaireAnswers {
  questionnaire_version?: string; // 默认 v1.0
  meal_period?: MealPeriod | null;
  appetite?: Appetite | null;
  avoidances?: Avoidance[];
  tastes?: Taste[];
  budget?: BudgetTier | null;
  max_distance_m?: number | null; // 范围 500~50000
  explicit_food_preference?: ExplicitPreferenceBackend | null;
  ai_follow_up_answers?: Record<string, unknown>;
}

// 后端 ExplicitPreference enum 的前端映射
export type ExplicitPreferenceBackend = 'undecided' | 'malatang' | 'beef_noodles';

// ============== 食物字典条目（只读数据，由后端提供） ==============

export interface FoodDictionaryItem {
  dictionary_version: string;
  food_code: string; // 蛇形小写英文/数字/下划线，2-40 字符
  display_name_zh: string;
  display_name_en?: string | null;
  emoji?: string | null;

  cuisine_groups?: CuisineGroup[];
  meal_times?: MealTimeTag[];
  satiety_tags?: SatietyTag[];
  taste_tags?: Taste[];

  // G-11 两件套分层
  common_avoidance_tags?: Avoidance[];
  medical_allergen_tags?: MedicalAllergen[];
  medical_safety_note_zh?: string | null; // 若设了 allergens 则必须非空

  // G-11 预算三件套：没有 min/max 数字区间
  supported_budget_tiers?: BudgetTier[];
  budget_fit_status?: BudgetFitStatus;
  budget_fit_source?: string | null;
  budget_fit_updated_at?: string | null;

  is_enabled?: boolean;
  priority_boost?: number; // -5..5
}

// ============== 推荐候选（输出） ==============

export interface RecommendationReason {
  summary_zh: string; // 1..160 字符
  matched_signals: string[]; // ≥1；G-12 可追溯信号
}

export interface RecommendationItem {
  priority: 1 | 2 | 3 | 4 | 5;
  food_code: string;
  food_name_zh?: string | null;
  source_type: SourceType; // 由服务端派生，前端不写
  generation_mode: GenerationMode; // ai / rule
  reason: RecommendationReason;

  budget_fit?: BudgetFitStatus;
  budget_fit_note_zh?: string | null; // 只标注平台参考，不承诺商户价格（G-10）
}
