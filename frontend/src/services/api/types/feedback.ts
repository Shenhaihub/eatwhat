/**
 * P6-04 反馈 API 类型定义。
 *
 * 与 backend/app/api/v1/feedback.py 的 Pydantic Schema 1:1 对齐。
 */

export type FeedbackType = 'bug_report' | 'feature_request' | 'content_report' | 'general';

export interface FeedbackTypeOption {
  readonly key: FeedbackType;
  readonly label: string;
  readonly description: string;
}

export interface FeedbackSubmitRequest {
  readonly feedback_type: FeedbackType;
  readonly content: string;
  readonly page_url?: string | null;
  readonly app_version?: string | null;
  readonly context?: Record<string, string> | null;
}

export interface FeedbackSubmitResponse {
  readonly ok: boolean;
  readonly feedback_id: string;
  readonly message: string;
}

export type ReportReason = 'spam' | 'inappropriate' | 'misinformation' | 'harassment' | 'other';
export type ReportTargetType = 'feed' | 'comment' | 'user';

export interface ReportRequest {
  readonly target_type: ReportTargetType;
  readonly target_id: string;
  readonly reason: ReportReason;
  readonly description?: string | null;
}

export interface ReportResponse {
  readonly ok: boolean;
  readonly report_id: string;
  readonly message: string;
}
