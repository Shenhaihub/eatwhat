/**
 * API 客户端骨架。
 * - 统一基础路径（来自 VITE_API_BASE_URL，默认 /api/v1）；
 * - 统一 JSON 请求与错误解析（对齐后端统一错误结构）；
 * - 自动携带 Supabase access_token（Authorization: Bearer），由 AuthContext 注入；
 * - P2 起在此之上补充幂等键与业务请求。
 */

import type {
  DemoLocationListResponse,
  DemoLocationSelectResponse,
  HistoryDeleteAllResponse,
  HistoryListResponse,
  HistoryRecord,
  HistoryWriteRequest,
  LocationReverseRequestV1,
  LocationReverseResponseV1,
  LocationSearchRequestV1,
  LocationSearchResponseV1,
  QuestionnaireNextRequestV1,
  QuestionnaireRecomputeResult,
  RecommendationsGenerateRequestV1,
  RecommendationsGenerateResponseV1,
  RestaurantSearchRequestV1,
  RestaurantSearchResponseV1,
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
};
