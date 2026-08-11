/**
 * P5-08 + P5-09：final_reason → 来源 badge 标签 + 视觉变体映射。
 * 共享给 Recommend 结果态顶栏 & History 卡片头两处使用。
 *
 * 规则（与后端 session.final_reason 写入值保持严格一致）：
 *   - "ai_gain"
 *       → 绿色 chip "AI 生成"（真实 AI 成功生成 Top5）
 *   - "rule_engine_fallback_ai_local_quota"    /
 *     "rule_engine_fallback_ai_remote_quota"   /
 *     "rule_engine_fallback_ai_unauthorized"   /
 *     "rule_engine_fallback_ai_timeout"        /
 *     "rule_engine_fallback_ai_schema"         /
 *     "rule_engine_fallback_ai_build_fail"     /
 *     "rule_engine_fallback_ai_fail"（旧默认）
 *       → 黄色 chip "规则引擎（AI 回退）"，并在 accessibleLabel 细化说明具体回退原因
 *   - "rule_engine_fallback_empty_ai" / "legacy_rule_engine" / null / 其他
 *       → 灰色 chip "规则引擎"（P2 旧路径或未写元信息）
 */

export type SourceBadgeVariant = 'ai' | 'fallback' | 'legacy';

export interface FinalReasonMeta {
  readonly variant: SourceBadgeVariant;
  readonly label: string;
  /** aria-label 用更口语化的中文，适合无障碍读屏 */
  readonly accessibleLabel: string;
  /** 给 Recommend 结果态顶部 microcopy 用的原因摘要；History 卡片可忽略 */
  readonly summaryText?: string;
}

// ---- 对齐后端写入常量（见 app/services/recommendation_session.py） ----
const AI_GAIN = 'ai_gain' as const;

/** P5-09 细分回退原因 → 更友好的中文标签（用于 chip 内显示，不影响 variant 配色） */
const FALLBACK_LABELS: Readonly<Record<string, string>> = {
  rule_engine_fallback_ai_local_quota: '规则引擎 · AI 日额度已用',
  rule_engine_fallback_ai_remote_quota: '规则引擎 · AI 平台限流',
  rule_engine_fallback_ai_unauthorized: '规则引擎 · AI 鉴权失败',
  rule_engine_fallback_ai_timeout: '规则引擎 · AI 响应超时',
  rule_engine_fallback_ai_schema: '规则引擎 · AI 结果不可用',
  rule_engine_fallback_ai_build_fail: '规则引擎 · AI 未配置',
  rule_engine_fallback_ai_fail: '规则引擎 · AI 回退',
};

/** 细化无障碍读屏文案，让视障用户知道"为什么切回了规则引擎" */
const FALLBACK_ACCESSIBLE: Readonly<Record<string, string>> = {
  rule_engine_fallback_ai_local_quota:
    '本账号今日的 AI 调用额度已用完，以下推荐已自动切换为确定性规则引擎生成（结果依然可靠）',
  rule_engine_fallback_ai_remote_quota:
    'AI 平台此刻访问量过大，以下推荐已自动切换为确定性规则引擎生成（结果依然可靠）',
  rule_engine_fallback_ai_unauthorized:
    'AI 服务授权校验未通过，以下推荐已自动切换为确定性规则引擎生成（结果依然可靠）；请联系管理员检查 API 密钥配置',
  rule_engine_fallback_ai_timeout:
    'AI 服务响应超时，以下推荐已自动切换为确定性规则引擎生成（结果依然可靠）',
  rule_engine_fallback_ai_schema:
    'AI 输出结果不在可信范围内，以下推荐已自动切换为确定性规则引擎生成（结果依然可靠）',
  rule_engine_fallback_ai_build_fail:
    'AI 服务未完成初始化，以下推荐已自动切换为确定性规则引擎生成（结果依然可靠）',
  rule_engine_fallback_ai_fail:
    'AI 服务暂时不可用，以下推荐已自动切换为确定性规则引擎生成（结果依然可靠）',
};

/** 用于 Recommend 结果态顶部"更具体一段中文摘要"的映射；找不到就走通用 fallback */
const FALLBACK_SUMMARY: Readonly<Record<string, string>> = {
  rule_engine_fallback_ai_local_quota:
    '今日 AI 额度已用罄，为你自动切换为确定性规则引擎生成（结果依然可靠）；明天 AI 链路会自动恢复。',
  rule_engine_fallback_ai_remote_quota:
    'AI 平台此刻访问量过大触发限流，以下推荐已自动切换为确定性规则引擎（结果依然可靠）。',
  rule_engine_fallback_ai_unauthorized:
    'AI 服务授权校验未通过，以下推荐已自动切换为确定性规则引擎（结果依然可靠）。',
  rule_engine_fallback_ai_timeout:
    'AI 服务响应超时，以下推荐已自动切换为确定性规则引擎（结果依然可靠）。',
  rule_engine_fallback_ai_schema:
    'AI 输出超出可信范围，以下推荐已自动切换为确定性规则引擎（结果依然可靠）。',
  rule_engine_fallback_ai_build_fail:
    'AI 服务未完成初始化，以下推荐已自动切换为确定性规则引擎（结果依然可靠）。',
  rule_engine_fallback_ai_fail:
    'AI 服务暂时不可用，以下推荐已自动切换为确定性规则引擎（结果依然可靠）。',
};

const FALLBACK_KEYS = new Set<string>(Object.keys(FALLBACK_LABELS));

export function describeFinalReason(
  finalReason: string | null | undefined,
): FinalReasonMeta {
  if (finalReason === AI_GAIN) {
    return {
      variant: 'ai',
      label: 'AI 生成',
      accessibleLabel: '本推荐由大模型生成并经确定性规则二次排序；越靠前越匹配你当下的偏好',
      summaryText: '以下推荐由 AI 生成并经确定性规则二次排序；越靠前越匹配你当下的偏好。',
    };
  }

  if (typeof finalReason === 'string' && FALLBACK_KEYS.has(finalReason)) {
    return {
      variant: 'fallback',
      label: FALLBACK_LABELS[finalReason] ?? '规则引擎 · AI 回退',
      accessibleLabel:
        FALLBACK_ACCESSIBLE[finalReason] ??
        'AI 服务暂时不可用，以下推荐已自动切换为确定性规则引擎生成（结果依然可靠）',
      summaryText:
        FALLBACK_SUMMARY[finalReason] ??
        'AI 服务暂时不可用，以下推荐已自动切换为确定性规则引擎（结果依然可靠）。',
    };
  }

  // legacy / empty_ai / 未知 key → 灰色规则引擎
  return {
    variant: 'legacy',
    label: '规则引擎',
    accessibleLabel: '本推荐由确定性规则引擎生成（未经过 AI 生成链路）',
    summaryText: '以下推荐来源于确定性规则引擎；越靠前的越匹配你刚刚回答的偏好。',
  };
}
