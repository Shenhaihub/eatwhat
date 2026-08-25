/**
 * API 客户端骨架。
 * - 统一基础路径（来自 VITE_API_BASE_URL，默认 /api/v1）；
 * - 统一 JSON 请求与错误解析（对齐后端统一错误结构）；
 * - 自动携带 Supabase access_token（Authorization: Bearer），由 AuthContext 注入；
 * - P2 起在此之上补充幂等键与业务请求。
 */

import type {
  CommunityFeedListResponse,
  CommunityFeedSort,
  CommunityLikeResponse,
  CommunityThemeResponse,
  CommunityThemeVoteRequest,
  CommunityThemeVoteResponse,
  CommunityTrendingResponse,
  DemoLocationListResponse,
  DemoLocationSelectResponse,
  FeedbackSubmitRequest,
  FeedbackSubmitResponse,
  FeedbackTypeOption,
  HistoryDeleteAllResponse,
  HistoryListResponse,
  HistoryRecord,
  HistoryWriteRequest,
  LocationReverseRequestV1,
  LocationReverseResponseV1,
  LocationSearchRequestV1,
  LocationSearchResponseV1,
  PreferenceDeleteAllResponse,
  PreferenceListResponse,
  PreferenceSnapshot,
  PreferenceWriteRequest,
  QuestionnaireNextRequestV1,
  QuestionnaireRecomputeResult,
  RecommendationsGenerateRequestV1,
  RecommendationsGenerateResponseV1,
  ReportRequest,
  ReportResponse,
  RestaurantSearchRequestV1,
  RestaurantSearchResponseV1,
  SessionAnswerRequestV1,
  SessionStateResponseV1,
  SystemAiStatsResponse,
} from './types';
import { createAccessTokenGetter } from '../../context/AuthContext';

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? '/api/v1';
const _getAccessToken = createAccessTokenGetter();

interface ApiErrorBody {
  error?: {
    code?: string;
    message?: string;
    request_id?: string;
  };
}

export class ApiError extends Error {
  readonly status: number;
  readonly code: string | null;
  readonly requestId: string | null;

  constructor(
    status: number,
    message: string,
    code: string | null = null,
    requestId: string | null = null,
  ) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
    this.requestId = requestId;
  }
}

interface RequestOptions {
  method?: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE';
  body?: unknown;
  headers?: Record<string, string>;
  signal?: AbortSignal;
}

export async function requestJson<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = 'GET', body, headers = {}, signal } = options;
  const token = _getAccessToken();
  const response = await fetch(`${API_BASE_URL}${path}`, {
    method,
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...headers,
    },
    body: body === undefined ? undefined : JSON.stringify(body),
    signal,
  });

  if (!response.ok) {
    let code: string | null = null;
    let requestId: string | null = null;
    let message = `请求失败（${response.status}）`;
    try {
      const data = (await response.json()) as ApiErrorBody;
      code = data.error?.code ?? null;
      message = data.error?.message ?? message;
      requestId = data.error?.request_id ?? null;
    } catch {
      // 非 JSON 响应体，保留默认错误信息
    }
    throw new ApiError(response.status, message, code, requestId);
  }

  // 204 No Content（例如 DELETE /auth/me、DELETE /history/:id）
  // 按标准是无响应体，直接返回，避免 .json() 抛 "Unexpected end of JSON input"
  if (response.status === 204) {
    return undefined as unknown as T;
  }

  return (await response.json()) as T;
}

export const api = {
  get<T>(path: string, options?: Omit<RequestOptions, 'method' | 'body'>): Promise<T> {
    return requestJson<T>(path, { ...options, method: 'GET' });
  },
  post<T>(
    path: string,
    body?: unknown,
    options?: Omit<RequestOptions, 'method' | 'body'>,
  ): Promise<T> {
    return requestJson<T>(path, { ...options, method: 'POST', body });
  },
  patch<T>(
    path: string,
    body?: unknown,
    options?: Omit<RequestOptions, 'method' | 'body'>,
  ): Promise<T> {
    return requestJson<T>(path, { ...options, method: 'PATCH', body });
  },

  // -------- 问卷决策 --------
  /**
   * P2-03B：POST /questionnaire/next
   * 调用问卷决策状态机，返回 next_questions / invalidated_answer_ids / is_complete / progress / covered_dimensions / next_action。
   *
   * 注意：G-07 调用方绝对不要在 body 里塞 source_type，会被后端 400 拒绝。
   */
  questionnaireNext(
    request: QuestionnaireNextRequestV1,
    options?: Omit<RequestOptions, 'method' | 'body'>,
  ): Promise<QuestionnaireRecomputeResult> {
    return api.post<QuestionnaireRecomputeResult>('/questionnaire/next', request, options);
  },

  // -------- 推荐生成 --------
  /**
   * P2-04：POST /recommendations
   * 把问卷完成态的 answers_by_question_id + questionnaire_version 直接提交，
   * 服务端：加载题库 → 映射到七维 → 规则引擎确定性输出 5 条（G-08：任何合法输入都返回正好 5）。
   *
   * 注意：G-07 禁止在请求体任何层级传 source_type；source_type=ai_recommended 由后端派生。
   */
  recommendationsGenerate(
    request: RecommendationsGenerateRequestV1,
    options?: Omit<RequestOptions, 'method' | 'body'>,
  ): Promise<RecommendationsGenerateResponseV1> {
    return api.post<RecommendationsGenerateResponseV1>('/recommendations', request, options);
  },

  // -------- P5-02：动态追问会话 --------
  /**
   * P5-02：POST /recommendations/session/start
   * 开始一个动态会话（最多 3 轮 AI 追问 + 最终 Top5）。
   *
   * 入参与 POST /recommendations 完全相同（answers_by_question_id + questionnaire_version）。
   * 返回统一 SessionState：
   *   - stage=follow_up + question：下一步显示追问 UI
   *   - stage=final + candidates：直接出最终 5 条
   *   - rounds_completed/max_rounds：前端显示"第 n/3 轮"
   */
  recommendationsSessionStart(
    request: RecommendationsGenerateRequestV1,
    options?: Omit<RequestOptions, 'method' | 'body'>,
  ): Promise<SessionStateResponseV1> {
    return api.post<SessionStateResponseV1>('/recommendations/session/start', request, options);
  },

  /**
   * P5-02：GET /recommendations/session/{session_id}
   * 幂等获取当前会话状态（刷新页面 / 断线重连用）。
   * 会话 TTL 15 分钟；过期返回 404。
   */
  recommendationsSessionGet(
    sessionId: string,
    options?: Omit<RequestOptions, 'method' | 'body'>,
  ): Promise<SessionStateResponseV1> {
    return api.get<SessionStateResponseV1>(`/recommendations/session/${encodeURIComponent(sessionId)}`, options);
  },

  /**
   * P5-02：POST /recommendations/session/{session_id}/answer
   * 回答一道 follow_up 题。
   *
   * - 幂等：同 question_id + option_value 多次提交返回同一次状态（HTTP 409 当重复非同一 value 时）。
   * - 返回统一 SessionState：
   *     stage=follow_up → 下一题 or 继续
   *     stage=final → 最终 Top5 候选
   */
  recommendationsSessionAnswer(
    sessionId: string,
    answer: SessionAnswerRequestV1,
    options?: Omit<RequestOptions, 'method' | 'body'>,
  ): Promise<SessionStateResponseV1> {
    return api.post<SessionStateResponseV1>(
      `/recommendations/session/${encodeURIComponent(sessionId)}/answer`,
      answer,
      options,
    );
  },

  // -------- 地点上下文（P3-01） --------
  /**
   * P3-01：POST /locations/search
   * 手动地点搜索（mock：本地匹配 demo 数据）。
   * 返回 LocationTokenInfo 列表（G-16：不含坐标，只有 location_token）。
   */
  locationSearch(
    request: LocationSearchRequestV1,
    options?: Omit<RequestOptions, 'method' | 'body'>,
  ): Promise<LocationSearchResponseV1> {
    return api.post<LocationSearchResponseV1>('/locations/search', request, options);
  },

  /**
   * P3-01：POST /locations/reverse
   * 浏览器定位反向地理编码（mock：WGS84→GCJ-02 后就近匹配 demo）。
   * G-16：坐标在 POST body，不在 URL；后端转换后只存内存。
   */
  locationReverse(
    request: LocationReverseRequestV1,
    options?: Omit<RequestOptions, 'method' | 'body'>,
  ): Promise<LocationReverseResponseV1> {
    return api.post<LocationReverseResponseV1>('/locations/reverse', request, options);
  },

  /**
   * P3-01：GET /locations/demo
   * 演示地点列表（不含坐标，不含 token）。
   */
  locationDemo(): Promise<DemoLocationListResponse> {
    return api.get<DemoLocationListResponse>('/locations/demo');
  },

  /**
   * P3-01：POST /locations/demo/{code}/select
   * 选择演示地点，签发 location_token。
   */
  locationDemoSelect(code: string): Promise<DemoLocationSelectResponse> {
    return api.post<DemoLocationSelectResponse>(`/locations/demo/${encodeURIComponent(code)}/select`);
  },

  // -------- 商户搜索（P3-02/P3-03） --------
  /**
   * P3-02：POST /restaurants/search
   * 用 location_token + food_code + radius_m 搜索附近商家。
   * G-16：响应不含坐标，只有 distance_m 粗略距离。
   * mock_mode 仅在 POI_PROVIDER=mock 时生效，用于 UI 重复触发四种状态。
   */
  restaurantsSearch(
    request: RestaurantSearchRequestV1,
    options?: Omit<RequestOptions, 'method' | 'body'>,
  ): Promise<RestaurantSearchResponseV1> {
    return api.post<RestaurantSearchResponseV1>('/restaurants/search', request, options);
  },

  // -------- 推荐历史（P4-03） --------
  /**
   * P4-03：GET /history
   * 当前用户的推荐历史（created_at DESC 分页）。需要登录。
   */
  historyList(
    params: { limit?: number; offset?: number } = {},
    options?: Omit<RequestOptions, 'method' | 'body'>,
  ): Promise<HistoryListResponse> {
    const q = new URLSearchParams();
    if (params.limit != null) q.set('limit', String(params.limit));
    if (params.offset != null) q.set('offset', String(params.offset));
    const query = q.toString();
    return api.get<HistoryListResponse>(`/history${query ? `?${query}` : ''}`, options);
  },

  /**
   * P4-03：POST /history
   * 前端一般不用手动调，后端 /recommendations 登录后会自动写。
   * 仅在前端离线/补录场景下使用。
   */
  historyCreate(
    request: HistoryWriteRequest,
    options?: Omit<RequestOptions, 'method' | 'body'>,
  ): Promise<HistoryRecord> {
    return api.post<HistoryRecord>('/history', request, options);
  },

  /**
   * P4-03：DELETE /history/{id}
   * 删除单条历史（不归你的会 404）。
   */
  historyDelete(id: string, options?: Omit<RequestOptions, 'method' | 'body'>): Promise<void> {
    return requestJson<void>(`/history/${encodeURIComponent(id)}`, { ...options, method: 'DELETE' });
  },

  /**
   * P4-03：DELETE /history
   * 清空当前用户的所有历史。返回删除条数。
   */
  historyDeleteAll(options?: Omit<RequestOptions, 'method' | 'body'>): Promise<HistoryDeleteAllResponse> {
    return requestJson<HistoryDeleteAllResponse>('/history', { ...options, method: 'DELETE' });
  },

  // -------- 账号（P4-04） --------
  /**
   * P4-04：DELETE /auth/me
   * 删除当前账号（GDPR 级联删除 auth.user + 历史记录）。
   * 后端会 revoke 所有 refresh_token；前端拿到 204 后直接清 session 跳首页。
   */
  accountDelete(options?: Omit<RequestOptions, 'method' | 'body'>): Promise<void> {
    return requestJson<void>('/auth/me', { ...options, method: 'DELETE' });
  },

  // -------- 用户偏好画像（P6-01 / P6-03） --------
  /**
   * P6-01：GET /preferences
   * 当前用户的偏好画像快照列表（append-only，created_at DESC 分页）。需要登录。
   *
   * - 分页模式优先：传 before 走 cursor，传 offset 走传统 offset；都不传默认取第一页（offset=0）。
   * - cursor 模式适合 Timeline 点击"加载更多"，避免 offset 在高并发写入场景下漏/重。
   */
  preferenceList(
    params: { limit?: number; offset?: number; before?: string } = {},
    options?: Omit<RequestOptions, 'method' | 'body'>,
  ): Promise<PreferenceListResponse> {
    const q = new URLSearchParams();
    if (params.limit != null) q.set('limit', String(params.limit));
    if (params.before != null) {
      q.set('before', params.before);
    } else if (params.offset != null) {
      q.set('offset', String(params.offset));
    }
    const query = q.toString();
    return api.get<PreferenceListResponse>(`/preferences${query ? `?${query}` : ''}`, options);
  },

  /**
   * P6-01：GET /preferences/latest
   * 最近一条画像（用户首页个性化卡片使用）。无记录返回 HTTP 404。
   */
  preferenceLatest(options?: Omit<RequestOptions, 'method' | 'body'>): Promise<PreferenceSnapshot> {
    return api.get<PreferenceSnapshot>('/preferences/latest', options);
  },

  /**
   * P6-01：POST /preferences
   * 前端一般不用手动调，后端 /recommendations 登录态会自动写。
   */
  preferenceCreate(
    request: PreferenceWriteRequest,
    options?: Omit<RequestOptions, 'method' | 'body'>,
  ): Promise<PreferenceSnapshot> {
    return api.post<PreferenceSnapshot>('/preferences', request, options);
  },

  /**
   * P6-01：DELETE /preferences/{id}
   * 删除单条快照（不归你的会 404）。
   */
  preferenceDelete(id: string, options?: Omit<RequestOptions, 'method' | 'body'>): Promise<void> {
    return requestJson<void>(`/preferences/${encodeURIComponent(id)}`, { ...options, method: 'DELETE' });
  },

  /**
   * P6-01：DELETE /preferences
   * 清空当前用户的全部偏好画像（用于"重置我的画像"）。返回删除条数。
   */
  preferenceDeleteAll(options?: Omit<RequestOptions, 'method' | 'body'>): Promise<PreferenceDeleteAllResponse> {
    return requestJson<PreferenceDeleteAllResponse>('/preferences', { ...options, method: 'DELETE' });
  },

  // -------- 观测仪表盘（P7-03 / P7-09） --------
  /**
   * P7-09：GET /system/ai-stats
   * 最近 N 条 ai_call_meta 的整体观测（sample_size、画像使用率、prompt 长度分布、outcome 分布）。
   * 后端对 user_id/session_id 做 sha1_10 脱敏，样本记录只用于内部观测。
   */
  systemAiStats(
    params: { limit?: number; stage?: 'follow_up' | 'final' } = {},
    options?: Omit<RequestOptions, 'method' | 'body'>,
  ): Promise<SystemAiStatsResponse> {
    const q = new URLSearchParams();
    if (params.limit != null) q.set('limit', String(params.limit));
    if (params.stage != null) q.set('stage', params.stage);
    const query = q.toString();
    return api.get<SystemAiStatsResponse>(`/system/ai-stats${query ? `?${query}` : ''}`, options);
  },

  // -------- 社区（B 阶段 MVP） --------
  /**
   * GET /community/feed
   * 匿名可读；登录态后端会按 user_id 填 liked_by_me。
   */
  communityFeed(
    params: { sort?: CommunityFeedSort } = {},
    options?: Omit<RequestOptions, 'method' | 'body'>,
  ): Promise<CommunityFeedListResponse> {
    const q = new URLSearchParams();
    if (params.sort) q.set('sort', params.sort);
    const query = q.toString();
    return api.get<CommunityFeedListResponse>(`/community/feed${query ? `?${query}` : ''}`, options);
  },

  /** GET /community/trending — 今日推荐 Top 榜（匿名可读）。 */
  communityTrending(
    options?: Omit<RequestOptions, 'method' | 'body'>,
  ): Promise<CommunityTrendingResponse> {
    return api.get<CommunityTrendingResponse>('/community/trending', options);
  },

  /**
   * GET /community/theme — 本周主题 + 投票进度。
   * 匿名可读；登录态返回 voted_key（已投选项 key / 未投 = null）。
   */
  communityTheme(
    options?: Omit<RequestOptions, 'method' | 'body'>,
  ): Promise<CommunityThemeResponse> {
    return api.get<CommunityThemeResponse>('/community/theme', options);
  },

  /**
   * POST /community/theme/vote — 主题投票（需要登录）。
   * 幂等；同用户投过别的选项 → 后端 409 ALREADY_VOTED_OTHER。
   */
  communityThemeVote(
    request: CommunityThemeVoteRequest,
    options?: Omit<RequestOptions, 'method' | 'body'>,
  ): Promise<CommunityThemeVoteResponse> {
    return api.post<CommunityThemeVoteResponse>('/community/theme/vote', request, options);
  },

  /**
   * POST /community/feed/{id}/like — 点赞（需要登录）。
   * 幂等；重复点 duplicated=true，点赞数不叠加。
   */
  communityFeedLike(
    feedId: string,
    options?: Omit<RequestOptions, 'method' | 'body'>,
  ): Promise<CommunityLikeResponse> {
    return api.post<CommunityLikeResponse>(
      `/community/feed/${encodeURIComponent(feedId)}/like`,
      undefined,
      options,
    );
  },

  // -------- 反馈（P6-04） --------
  /** GET /feedback/types — 获取反馈类型列表。 */
  feedbackTypes(
    options?: Omit<RequestOptions, 'method' | 'body'>,
  ): Promise<FeedbackTypeOption[]> {
    return api.get<FeedbackTypeOption[]>('/feedback/types', options);
  },

  /** POST /feedback/submit — 提交反馈（登录可选）。 */
  feedbackSubmit(
    request: FeedbackSubmitRequest,
    options?: Omit<RequestOptions, 'method' | 'body'>,
  ): Promise<FeedbackSubmitResponse> {
    return api.post<FeedbackSubmitResponse>('/feedback/submit', request, options);
  },

  /** POST /feedback/report — 举报内容（需要登录）。 */
  feedbackReport(
    request: ReportRequest,
    options?: Omit<RequestOptions, 'method' | 'body'>,
  ): Promise<ReportResponse> {
    return api.post<ReportResponse>('/feedback/report', request, options);
  },
};
