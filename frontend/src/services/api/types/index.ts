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
export {
  ENTRY_INTENT_VALUES,
  isValidEntryIntent,
  type CompletionReason,
  type DimensionCoverage,
  type DimensionMapping,
  type DisplayCondition,
  type DisplayConditionOperator,
  type EntryIntent,
  type MappableDimensionField,
  type NextAction,
  type QuestionBankItem,
  type QuestionOption,
  type QuestionType,
  type QuestionnaireNextRequestV1,
  type QuestionnaireRecomputeResult,
} from './questionnaire';
export {
  DICTIONARY_VERSION_PATTERN,
  QUESTIONNAIRE_VERSION_PATTERN,
  RECOMMENDATIONS_SUPPORTED_ENTRY_INTENTS,
  type RecommendationsGenerateRequestV1,
  type RecommendationsGenerateResponseV1,
} from './recommendations';
export {
  type DemoLocationItem,
  type DemoLocationListResponse,
  type DemoLocationSelectResponse,
  type LocationReverseRequestV1,
  type LocationReverseResponseV1,
  type LocationSearchRequestV1,
  type LocationSearchResponseV1,
  type LocationSource,
  type LocationTokenInfo,
} from './location';
export {
  type MockMode,
  type POIItem,
  type POIProviderName,
  type RestaurantSearchMeta,
  type RestaurantSearchRequestV1,
  type RestaurantSearchResponseV1,
  type RestaurantSearchSuggestion,
} from './poi';
export {
  type HistoryDeleteAllResponse,
  type HistoryItemSnapshot,
  type HistoryListResponse,
  type HistoryLocation,
  type HistoryRecord,
  type HistoryWriteRequest,
} from './history';
