/**
 * P2-04: 推荐生成接口类型（1:1 映射后端 RecommendationsGenerateRequestV1 / list[RecommendationItem]）。
 *
 * 请求体字段与后端 RecommendationsGenerateRequestV1 严格一致：
 * - entry_intent: P2 阶段仅允许 ai_recommend
 * - questionnaire_version: 与 /questionnaire/next 一致，v1.0
 * - answers_by_question_id: 与 /questionnaire/next 的入参形状完全一致
 * - dictionary_version: 可选，不传 = 默认食物字典版本
 *
 * 响应体：list<RecommendationItem>，长度固定 5（G-08）。
 */

import type { EntryIntent } from './questionnaire';
import type { RecommendationItem } from './food';

export const QUESTIONNAIRE_VERSION_PATTERN: Readonly<RegExp> = /^v\d+\.\d+$/;
export const DICTIONARY_VERSION_PATTERN: Readonly<RegExp> = /^v\d+\.\d+$/;

/** 入口：P2 仅支持 ai_recommend；其他入口 P3/P4 预留。 */
export const RECOMMENDATIONS_SUPPORTED_ENTRY_INTENTS: EntryIntent[] = ['ai_recommend'] as const;

export interface RecommendationsGenerateRequestV1 {
  readonly entry_intent: EntryIntent;
  readonly questionnaire_version: string;
  readonly answers_by_question_id: Readonly<Record<string, readonly string[]>>;
  readonly dictionary_version?: string;
}

/** 响应体 = 正好 5 条 RecommendationItem，按 priority 升序返回。 */
export type RecommendationsGenerateResponseV1 = readonly RecommendationItem[];
