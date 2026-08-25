// P7-03 / P7-09：观测仪表盘 AI Stats 类型（严格对齐后端 app/core/ai_stats.py::compute_stats 结果）

export interface StageBreakdownEntry {
  calls: number;
  pref_used_rate: number; // 0..1
  avg_pref_snaps: number;
  avg_total_prompt_chars: number;
}

export interface StatsWindow {
  oldest_ts: string | null;
  newest_ts: string | null;
  oldest_epoch?: number;
  newest_epoch?: number;
}

/** 对齐 AiCallMetaRecord.to_dict() */
export interface AiStatsRecordLite {
  // 注意：后端 system route 在返回前会把 user_id/session_id 转 sha1_10 重命名为 user_id_sha1_10 / session_id_sha1_10
  user_id_sha1_10?: string | null;
  session_id_sha1_10?: string | null;
  user_id?: string | null;
  session_id?: string | null;
  ts: number;
  ts_iso: string;
  ai_stage: 'follow_up' | 'final' | string;
  ai_round_1based?: number | null;
  preference_context_used: boolean;
  preference_context_snapshot_count: number;
  preference_context_chars: number;
  preference_context_lines: number;
  system_prompt_chars: number;
  user_prompt_chars: number;
  total_prompt_chars: number;
  ai_outcome: 'ok' | 'fallback_rules_engine' | 'fail' | string;
  ai_fail_code?: string | null;
  final_reason?: string | null;
  [k: string]: unknown;
}

export interface SystemAiStatsResponse {
  queried_records: number;        // 本次样本窗口大小 N（即 sample_size / total_calls 同义）
  window: StatsWindow;
  pref_context_used_rate: number; // 0..1
  avg_snapshot_count_used: number; // 只在画像命中的样本上算平均快照数
  avg_total_prompt_chars: number;  // 平均总 prompt 长度（字）
  breakdown_by_stage: Record<string, StageBreakdownEntry>;
  outcome_breakdown: Record<string, number>; // key: ai_outcome，value: 次数
  sample_records: AiStatsRecordLite[];       // 最近 5 条（时间倒序）
}
