/**
 * API 客户端骨架。
 * - 统一基础路径（来自 VITE_API_BASE_URL，默认 /api/v1）；
 * - 统一 JSON 请求与错误解析（对齐后端统一错误结构）；
 * - P2 起在此之上补充认证头、幂等键与业务请求。
 */

import type {
  QuestionnaireNextRequestV1,
  QuestionnaireRecomputeResult,
  RecommendationsGenerateRequestV1,
  RecommendationsGenerateResponseV1,
} from './types';

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? '/api/v1';

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
  const response = await fetch(`${API_BASE_URL}${path}`, {
    method,
    headers: {
      'Content-Type': 'application/json',
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
};
