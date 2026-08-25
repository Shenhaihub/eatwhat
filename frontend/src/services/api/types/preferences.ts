/**P6-01 用户偏好画像接口类型 1:1。*/

export interface PreferenceSnapshot {
  id: string;
  user_id: string;
  questionnaire_version: string;
  dictionary_version: string;
  // P7-06：画像快照 schema 版本号（v1.0 起步），未来 snapshot 结构大版本变更时 bump
  snapshot_version: string;
  source_session_id: string | null;
  source_history_id: string | null;
  /** QuestionnaireAnswers.model_dump() — 七维画像 + ai_follow_up_answers + _meta */
  snapshot: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface PreferenceListResponse {
  items: PreferenceSnapshot[];
  total: number;
  limit: number;
  // Offset 模式才会回传数字；cursor 模式回传 null
  offset: number | null;
  // P7-02：cursor 分页（created_at DESC + id DESC，Base64URL 编码）
  // - next_cursor=null → 已到最后一页
  // - page_cursor 是本次请求使用的 before= 原文（原样回显，便于调试）
  next_cursor: string | null;
  page_cursor: string | null;
}

export interface PreferenceWriteRequest {
  questionnaire_version?: string;
  dictionary_version?: string;
  snapshot_version?: string;
  source_session_id?: string | null;
  source_history_id?: string | null;
  snapshot: Record<string, unknown>;
  created_at?: string;
}

export interface PreferenceDeleteAllResponse {
  deleted: number;
}
