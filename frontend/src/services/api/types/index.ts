/**
 * 后端接口 1:1 类型包出口。
 * 业务代码从这里 import，不要直接从子文件 import（方便以后换源）。
 */

export type {
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
export {
  ENTRY_POINT_INTENT_VALUES,
  isValidEntryPoint,
  type EntryPointIntent,
  type ExplicitPreferenceBackend,
  type FoodDictionaryItem,
  type QuestionnaireAnswers,
  type RecommendationItem,
  type RecommendationReason,
} from './food';
