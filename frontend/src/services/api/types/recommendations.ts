/**
 * P2-04: 推荐生成接口类型（1:1 映射后端 RecommendationsGenerateRequestV1 / DirectRecommendationsResponseV1）。
 *
 * 请求体字段与后端 RecommendationsGenerateRequestV1 严格一致：
 * - entry_intent: P2 阶段仅允许 ai_recommend
 * - questionnaire_version: 与 /questionnaire/next 一致，v1.0
 * - answers_by_question_id: 与 /questionnaire/next 的入参形状完全一致
 * - dictionary_version: 可选，不传 = 默认食物字典版本
 *
 * 响应体：DirectRecommendationsResponseV1 = {
 *   items: RecommendationItem[5],  // 正好 5 条（G-08）
 *   merged_pref_fields: MergedPrefField[],  // P7-07：P6-02 冷启动画像合并实际改变的字段（空数组 = 未合并）
 * }
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
  /** P5-04A：用户是否希望使用 AI 优化推荐（G-07：仅用户偏好指示，最终 generation_mode 由服务端派生）。
   * - true：登录态 → 后端派生 generation_mode='ai'；未登录 → 401。
   * - false（默认）→ 后端派生 generation_mode='rule'，走免费确定性规则引擎。
   */
  readonly prefer_ai_gain?: boolean;
  /**
   * B：前端扩展字段（预留给本周主题 PK「按菜系生成」场景）。
   * 社区页点「就按『日料』给我生成推荐」时，前端会把主题的 key（如 japanese / korean / sichuan …）
   * 同时塞进 answers[q07_cuisine_preference]（问卷映射）和这里的顶级 cuisine_preferences（直接语义）。
   * 后端只要支持读取该字段，就能直接把"菜系维度"提前注入规则引擎 / AI prompt，无需等问卷答案映射。
   * 注意：G-07 只禁止传 source_type，此字段不在限制列表内。
   */
  readonly cuisine_preferences?: readonly string[];
}

/** P7-07：P6-02 冷启动画像合并单条差异。 */
export type MergedPrefFieldKind = 'single' | 'list' | 'ai_follow_up';
export interface MergedPrefField {
  readonly field: string;
  readonly kind: MergedPrefFieldKind;
  readonly before: unknown;
  readonly after: unknown;
  readonly change: 'filled' | 'appended';
  /** kind=list：具体追加到 tastes/avoidances 的项。 */
  readonly added_items?: readonly unknown[];
  /** kind=ai_follow_up：新增的 answer key 列表。 */
  readonly added_keys?: readonly string[];
  /** kind=ai_follow_up：{ key: value } 新增长答案明细。 */
  readonly added_items_map?: Readonly<Record<string, unknown>>;
}

/** AI 额度使用概览（今日）。 */
export interface AiQuotaInfo {
  /** 当前登录用户今日已消耗的 AI 增益次数（未登录 = 0）。 */
  readonly user_used: number;
  /** 当前登录用户每日额度上限（通常 = 3）。 */
  readonly user_limit: number;
  /** 全局今日已消耗 AI 增益次数（用于展示"今天还剩多少全局额度" / 后台限流）。 */
  readonly global_used: number;
  /** 全局每日额度上限。 */
  readonly global_limit: number;
}

/** 响应体 = 正好 5 条 RecommendationItem + 画像合并差异（按 priority 升序返回）。 */
export interface RecommendationsGenerateResponseV1 {
  readonly items: readonly RecommendationItem[];
  readonly merged_pref_fields: readonly MergedPrefField[];
  /** P0 修复：后端自动写入画像/历史的结果（前端据此显示保存状态/按钮）。 */
  readonly autowrite?: {
    /** 是否为登录态（未登录时后端不自动保存）。 */
    readonly logged_in: boolean;
    /** 推荐历史是否写入成功。 */
    readonly history_saved: boolean;
    /** 画像快照是否写入成功（=Timeline 有数据）。 */
    readonly preference_saved: boolean;
    /** 历史记录 id（可用于「查看本条推荐详情」跳转）。 */
    readonly history_id: string | null;
    /** 画像快照 id（可用于 Settings→画像 Tab 高亮）。 */
    readonly preference_id: string | null;
    /** 中文简短说明，可直接 toast / banner 展示。 */
    readonly reason: string;
  };
  /** P5-04A：若为 true，表示本次最终 5 条确实走了 AI 生成 = final_reason == 'ai_gain'。 */
  readonly used_ai?: boolean;
  /** P5-07：今日 AI 额度使用情况（前端直接渲染「今日 2/3」）。 */
  readonly ai_quota?: AiQuotaInfo;
  /** P5-04A：来源徽章的后端真源。 */
  readonly final_reason?: string | null;
}

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
  /** trace：final 是来自 AI（ai_gain）或规则回退（rule_engine_fallback_ai_fail）或 legacy_rule_engine。 */
  readonly final_reason: string | null;
  /** P7-07：冷启动画像合并实际改变的 answers 字段（仅 session/start 可能非空）。 */
  readonly merged_pref_fields: readonly MergedPrefField[];
  /** P0 修复：stage=final 时有值，前端据此显示保存状态/按钮。 */
  readonly autowrite?: {
    readonly logged_in: boolean;
    readonly history_saved: boolean;
    readonly preference_saved: boolean;
    readonly history_id: string | null;
    readonly preference_id: string | null;
    readonly reason: string;
  };
  /** P5-04A：本次 final 实际走了 AI = final_reason === 'ai_gain'。 */
  readonly used_ai?: boolean;
  /** P5-07：今日 AI 额度情况。 */
  readonly ai_quota?: AiQuotaInfo;
}

export interface SessionAnswerRequestV1 {
  readonly question_id: string;
  readonly selected_option_value: string;
}
