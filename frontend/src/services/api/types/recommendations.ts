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
 *
 * P5-02 动态追问：
 *   POST /recommendations/session/start → 返回 SessionStateResponseV1
 *   GET  /recommendations/session/{id} → 返回 SessionStateResponseV1
 *   POST /recommendations/session/{id}/answer → 返回 SessionStateResponseV1
 *
 *   最多 3 轮 follow_up 后强制进入 final；若 AI 判定信息充分可随时提前 final。
 *   final 阶段 candidates 非空（正好 5 条，G-08 不空保障）。
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

// ========== P5-02 动态追问 ==========

export interface FollowUpOptionV1 {
  readonly value: string;
  readonly label_zh: string;
}

export interface FollowUpQuestionV1 {
  readonly question_id: string;
  readonly title_zh: string;
  readonly options: readonly FollowUpOptionV1[];
  /** 告诉用户这道题在补什么维度信息。 */
  readonly purpose_zh: string;
  /** true=继续追问/final；false=信息充分，直接出最终 5 候选。（目前前端仅作展示） */
  readonly should_continue: boolean;
}

export type SessionStageV1 = 'follow_up' | 'final';

/** 统一会话响应（start / get / answer 三个路由都返回它）。 */
export interface SessionStateResponseV1 {
  readonly session_id: string;
  readonly stage: SessionStageV1;
  /** follow_up 阶段非空；final 阶段 null。 */
  readonly question: FollowUpQuestionV1 | null;
  /** 已完成的追问轮次（0..3）。 */
  readonly rounds_completed: number;
  /** 最多 3 轮（目前硬编码 3）。 */
  readonly max_rounds: number;
  /** stage=final 非空，正好 5 条 RecommendationItem。 */
  readonly candidates: readonly RecommendationItem[] | null;
  /** trace：final 是来自 AI（ai_finalized）或规则回退（rule_engine_fallback_ai_fail）。 */
  readonly final_reason: string | null;
}

export interface SessionAnswerRequestV1 {
  readonly question_id: string;
  readonly selected_option_value: string;
}
