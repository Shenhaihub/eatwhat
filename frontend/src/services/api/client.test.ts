// @vitest-environment node
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiError, requestJson } from './client';

function jsonResponse(body: unknown, status: number): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

describe('requestJson', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('拼接基础路径并解析 JSON', async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL) => jsonResponse({ ok: true }, 200));
    vi.stubGlobal('fetch', fetchMock);

    const data = await requestJson<{ ok: boolean }>('/health/live');
    expect(data).toEqual({ ok: true });
    const url = fetchMock.mock.calls[0]?.[0];
    expect(String(url)).toMatch(/\/api\/v1\/health\/live$/);
  });

  it('非 2xx 抛出 ApiError 并携带后端错误信息', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        jsonResponse(
          {
            error: {
              code: 'AI_DAILY_QUOTA_EXHAUSTED',
              message: '今天的次数已用完',
              request_id: 'req-1',
            },
          },
          429,
        ),
      ),
    );

    const caught = await requestJson('/recommendations').catch((e: unknown) => e);
    expect(caught).toBeInstanceOf(ApiError);
    if (caught instanceof ApiError) {
      expect(caught.status).toBe(429);
      expect(caught.code).toBe('AI_DAILY_QUOTA_EXHAUSTED');
      expect(caught.message).toBe('今天的次数已用完');
      expect(caught.requestId).toBe('req-1');
    }
  });

  it('响应体非 JSON 时使用默认错误信息', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response('gateway error', { status: 502 })),
    );

    const caught = await requestJson('/x').catch((e: unknown) => e);
    expect(caught).toBeInstanceOf(ApiError);
    if (caught instanceof ApiError) {
      expect(caught.status).toBe(502);
      expect(caught.code).toBeNull();
      expect(caught.message).toContain('请求失败');
    }
  });
});
