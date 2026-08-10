/** EatWhat 登录态上下文。
 *
 *  数据流：
 *   1. Supabase SDK 在初始化时读取 localStorage（detectSessionInUrl=true 会自动解析 magic link 回调）。
 *   2. AuthContext 监听 onAuthStateChange，把 session.user 同步到 React state。
 *   3. 业务组件通过 useAuth() 拿到 user / accessToken / login/logout。
 *   4. API client 通过 getAccessTokenFn() 自动注入 Authorization: Bearer <token>。
 *
 *  🔒 注意：前端绝不接触 service_role key；所有写用户数据走后端 /api/v1/auth/*。
 */
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react';
import { Navigate, useLocation } from 'react-router';
import { getSupabase } from '../lib/supabase';
import type { Session, User } from '@supabase/supabase-js';

interface AuthUser {
  user_id: string;
  email: string;
  role?: string | null;
  created_at?: string | null;
}

interface AuthContextValue {
  /** 当前登录用户（未登录为 null） */
  user: AuthUser | null;
  /** 原始 Supabase User（便于扩展） */
  rawUser: User | null;
  /** Supabase access_token（JWT），传给后端 Authorization 头 */
  accessToken: string | null;
  /** 是否正在初始化/加载 session */
  loading: boolean;
  /** 是否已登录 */
  isAuthenticated: boolean;
  /** 发送 magic link 邮件 */
  sendMagicLink: (email: string, redirectTo?: string) => Promise<void>;
  /** 注销（清除本地 session + 通知后端）。
   *
   * @param options.skipServer - 默认 false。传 true 时只清本地不调 Supabase SDK
   * （用于"删账号成功后本地立即失效"这种场景，此时 token 可能已被远端吊销）。
   */
  logout: (options?: { skipServer?: boolean }) => Promise<void>;
  /** 手动刷新 session（例如 magic link 回调后） */
  refresh: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

function _toAuthUser(user: User | null): AuthUser | null {
  if (!user || !user.email) return null;
  return {
    user_id: user.id,
    email: user.email,
    role: user.role ?? null,
    created_at: user.created_at ?? null,
  };
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session | null>(null);
  const [loading, setLoading] = useState(true);
  const sb = useMemo(() => {
    // 若启动时配置缺失（例如 CI / 某些测试环境），降级成 null 避免 throw。
    try {
      return getSupabase();
    } catch {
      return null;
    }
  }, []);

  // 首次挂载：读取当前 session
  useEffect(() => {
    let cancelled = false;
    (async () => {
      if (!sb) {
        if (!cancelled) setLoading(false);
        return;
      }
      // E2E 注入钩子：仅在开发环境，从 URL hash 读取一次性 session（不进服务器请求）。
      // 用完后立即 replaceState 清掉，避免污染 history。
      // 注意：生产构建时 import.meta.env.DEV 为 false，这段会被 tree-shake 掉。
      if (import.meta.env.DEV) {
        try {
          const m = /#e2e-session=([^&]+)/.exec(window.location.hash);
          if (m) {
            const payload = JSON.parse(decodeURIComponent(m[1]));
            if (
              payload &&
              typeof payload === 'object' &&
              typeof (payload as { access_token?: unknown }).access_token === 'string' &&
              typeof (payload as { refresh_token?: unknown }).refresh_token === 'string'
            ) {
              const { data } = await sb.auth.setSession({
                access_token: (payload as { access_token: string }).access_token,
                refresh_token: (payload as { refresh_token: string }).refresh_token,
              });
              // 清 hash（不触发新的路由）
              const cleanUrl = window.location.pathname + window.location.search;
              window.history.replaceState(null, '', cleanUrl);
              if (!cancelled) {
                setSession(data.session);
                setLoading(false);
                return;
              }
            }
          }
          // 兼容老的 window 钩子（仅当没有 hash 时才看）
          const anyWin = window as unknown as { __E2E_INJECT_SESSION__?: unknown };
          const payload = anyWin.__E2E_INJECT_SESSION__;
          if (payload && typeof payload === 'object') {
            const sess = payload as { access_token?: string; refresh_token?: string };
            if (sess.access_token && sess.refresh_token) {
              const { data } = await sb.auth.setSession({
                access_token: sess.access_token,
                refresh_token: sess.refresh_token,
              });
              if (!cancelled) {
                setSession(data.session);
                setLoading(false);
                delete anyWin.__E2E_INJECT_SESSION__;
                return;
              }
            }
          }
        } catch {
          // ignore – 注入失败就走默认 getSession
        }
      }
      const { data } = await sb.auth.getSession();
      if (!cancelled) {
        setSession(data.session);
        setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [sb]);

  // 订阅会话变化（magic link 回调 / 刷新 token / 登出 都会触发）
  useEffect(() => {
    if (!sb) return;
    const {
      data: { subscription },
    } = sb.auth.onAuthStateChange((_event, next) => {
      setSession(next);
    });
    return () => subscription.unsubscribe();
  }, [sb]);

  const sendMagicLink = useCallback(
    async (email: string, redirectTo?: string) => {
      if (!sb) throw new Error('Supabase 客户端未初始化');
      // 走后端统一入口（便于审计 + 统一 redirect_to 默认值）
      // 若后端不可用，直接用前端 SDK 兜底，保证 MVP 可用
      try {
        const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? '/api/v1';
        const resp = await fetch(`${API_BASE_URL}/auth/magic-link`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            email,
            redirect_to: redirectTo ?? `${window.location.origin}/auth/callback`,
          }),
        });
        if (!resp.ok) throw new Error(`后端发送失败: ${resp.status}`);
        try {
          const body = (await resp.json()) as { sent?: boolean; error_code?: string | null };
          if (body.sent === false) {
            // 后端明确失败（如 NETWORK_SUPABASE）：fallback 到前端 SDK 直连
            throw new Error(`后端 sent=false: ${body.error_code ?? 'unknown'}`);
          }
        } catch {
          // 非 JSON 响应时按原样忽略（兼容旧实现）
        }
      } catch (err) {
        // 前端 SDK 兜底：直接调 Supabase signInWithOtp
        await sb.auth.signInWithOtp({
          email,
          options: {
            emailRedirectTo: redirectTo ?? `${window.location.origin}/auth/callback`,
            shouldCreateUser: true,
          },
        });
      }
    },
    [sb],
  );

  const logout = useCallback(async (options?: { skipServer?: boolean }) => {
    if (!sb) return;
    // 先清本地，再通知后端吊销（顺序无所谓，后端 MVP 也不强制吊销）
    if (!options?.skipServer) {
      const { error } = await sb.auth.signOut();
      if (error) {
        // 兜底：即使 SDK 报错也清本地，保证 UI 同步
        localStorage.removeItem('ew-sb-auth');
      }
    } else {
      localStorage.removeItem('ew-sb-auth');
    }
    setSession(null);
  }, [sb]);

  const refresh = useCallback(async () => {
    if (!sb) return;
    const { data } = await sb.auth.getSession();
    setSession(data.session);
  }, [sb]);

  const value: AuthContextValue = {
    user: _toAuthUser(session?.user ?? null),
    rawUser: session?.user ?? null,
    accessToken: session?.access_token ?? null,
    loading,
    isAuthenticated: Boolean(session?.user?.email),
    sendMagicLink,
    logout,
    refresh,
  };

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth 必须在 <AuthProvider> 内使用');
  return ctx;
}

/** 给 API client 注入 access token 的钩子（非 React Hook，纯函数 getter）。
 *  首次调用时需要已经 mount 过 AuthProvider，否则返回 null。 */
export function createAccessTokenGetter(): () => string | null {
  let latest: string | null = null;
  // 通过订阅 onAuthStateChange 同步最新 token
  let sb: ReturnType<typeof getSupabase> | null = null;
  try {
    sb = getSupabase();
    // 初始值
    sb.auth
      .getSession()
      .then(({ data }) => {
        latest = data.session?.access_token ?? null;
      })
      .catch(() => {
        /* 忽略 */
      });
    sb.auth.onAuthStateChange((_e, s) => {
      latest = s?.access_token ?? null;
    });
  } catch {
    /* 配置缺失场景 */
  }
  return () => latest;
}

/** 需要登录才能访问的路由守卫：未登录跳 /login 并带上 return_to。 */
export function ProtectedRoute({ children }: { children: ReactNode }) {
  const { isAuthenticated, loading } = useAuth();
  const location = useLocation();
  if (loading) return null;
  if (!isAuthenticated) {
    const qs = new URLSearchParams({ return_to: location.pathname + location.search });
    return <Navigate to={`/login?${qs.toString()}`} replace />;
  }
  return <>{children}</>;
}
