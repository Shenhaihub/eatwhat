/**
 * P2-03B 问卷决策接口类型。
 * 与后端 `app/schemas/questionnaire.py` 严格 1:1。
 *
 * 关键约束：
 * - 入口意图使用 ai_recommend / community / activity / user_choice（与 food.ts 的 EntryPointIntent 是两套）
 * - 问卷答案仅依赖 question_id → list[value] 的扁平结构，草稿按 value 持久化，不存 option_id
 * - next_questions 已包含完整题对象，前端渲染不再做二次查询
 */

// ============ 请求体（POST /questionnaire/next） ============

export const ENTRY_INTENT_VALUES = [
  'ai_recommend',
  'community',
  'activity',
  'user_choice',
] as const;

export type EntryIntent = (typeof ENTRY_INTENT_VALUES)[number];

export function isValidEntryIntent(value: string): value is EntryIntent {
  return (ENTRY_INTENT_VALUES as readonly string[]).includes(value);
}

export interface QuestionnaireNextRequestV1 {
  entry_intent: EntryIntent;
  questionnaire_version: string; // 正则 ^v[0-9]+\.[0-9]+$
  answers_by_question_id: Record<string, string[]>;
}

// ============ 题库嵌套结构 ============

export interface QuestionOption {
  option_id: string; // ^[a-z0-9_]{2,32}$
  label_zh: string; // 1..32
  value: string; // 1..32；语义稳定的枚举值，草稿持久化用这个
}

export type DisplayConditionOperator =
  | 'equals'
  | 'not_equals'
  | 'in'
  | 'not_in'
  | 'always_true';

export interface DisplayCondition {
  operator: DisplayConditionOperator;
  operand_question_id: string | null;
  operand_value: unknown;
}

export type MappableDimensionField =
  | 'meal_period'
  | 'appetite'
  | 'avoidances'
  | 'tastes'
  | 'budget'
  | 'explicit_food_preference';

export interface DimensionMapping {
  field_name: MappableDimensionField;
  is_array: boolean;
  value_is_enum_value: boolean;
}

export type QuestionType = 'single_choice' | 'multi_choice';

export interface QuestionBankItem {
  question_id: string; // ^[a-z0-9_]{2,40}$
  title_zh: string; // 1..64
  question_type: QuestionType;
  options: QuestionOption[]; // 1..16
  maps_to: DimensionMapping;
  display_if: DisplayCondition | null;
  required_for_entry_intents: string[];
}

// ============ 状态机响应 ============

export interface DimensionCoverage {
  field_name: string; // MAPPABLE_DIMENSION_FIELDS 中的一个
  covered: boolean;
}

export type CompletionReason =
  | 'all_required_answered'
  | 'entry_intent_no_questionnaire_required'
  | 'not_complete';

export type NextAction =
  | 'proceed_questionnaire'
  | 'proceed_generate_recommendations'
  | 'redirect_no_questionnaire_required';

export interface QuestionnaireRecomputeResult {
  questionnaire_version: string;

  // 题面 & ID
  next_questions: QuestionBankItem[];
  next_question_ids: string[];

  // 作废答案
  invalidated_answer_ids: string[];
  // 后端 schema 兼容保留 deprecated 字段；但 API 层默认不返回它们，前端直接忽略
  invalidated_answer_question_ids?: string[];

  // 完整度
  is_complete: boolean;
  progress: number; // 0..100
  progress_pct?: number; // deprecated，值与 progress 恒等

  // 覆盖度 & 剩余必填 & 原因
  covered_dimensions: DimensionCoverage[];
  completion_reason: CompletionReason;
  required_not_yet_answered_question_ids: string[];

  // 下一步动作指引（清单硬性要求）
  next_action: NextAction;
}
