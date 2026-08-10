/** Magic Link 回调页。
 *
 *  两种路径：
 *   1) Supabase SDK detectSessionInUrl=true 自动解析 URL 里的 token，并在本页 mount 前完成
 *      → AuthContext 已经有 session，直接跳回首页（或 ?next= 指定地址）。
 *   2) 若 SDK 未检测到（例如用户手动复制链接进来时 token_hash 已失效），
 *      → 展示错误提示 + 返回登录按钮。
 *
 *  本页也支持 ?mode=verify 模式：通过后端 /auth/verify 手工兑换 token（MVP 暂用方案 1）。
 */
import { useEffect } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router';
import { useAuth } from '../context/AuthContext';

export default function AuthCallback() {
  const { isAuthenticated, loading, accessToken } = useAuth();
  const [params] = useSearchParams();
  const nav = useNavigate();

  const next = params.get('next') ?? '/';
  const mode = params.get('mode') ?? 'auto';

  useEffect(() => {
    if (loading) return;
    // 一旦登录成功，立即跳转
    if (isAuthenticated && accessToken) {
      nav(next, { replace: true });
      return;
    }
    // 模式：verify —— 用 URL 里的 token_hash 调后端换 session（MVP 暂不实现，留给手动模式）
    if (mode === 'verify') {
      const email = params.get('email');
      const token = params.get('token') ?? params.get('token_hash');
      if (email && token) {
        (async () => {
          try {
            const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? '/api/v1';
            const resp = await fetch(`${API_BASE_URL}/auth/verify`, {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ email, token, type: 'magiclink' }),
            });
            if (resp.ok) {
              const body = (await resp.json()) as { access_token?: string };
              if (body.access_token) {
                // setSession 已由 Supabase SDK 的 detectSessionInUrl 完成；直接跳
                nav(next, { replace: true });
              }
            }
          } catch {
            /* 静默：下方错误 UI 兜底 */
          }
        })();
      }
    }
  }, [loading, isAuthenticated, accessToken, mode, next, params, nav]);

  if (loading || isAuthenticated) {
    return (
      <div className="auth-callback">
        <p className="status-ok">正在登录…</p>
      </div>
    );
  }

  return (
    <div className="auth-callback">
      <div className="callback-card" role="alert">
        <h1>登录链接已过期或无效</h1>
        <p>可能的原因：</p>
        <ul>
          <li>链接已被使用过（Magic Link 只能用一次）</li>
          <li>链接超过 1 小时有效期</li>
          <li>复制粘贴时缺少部分字符</li>
        </ul>
        <div className="callback-actions">
          <Link to="/login" className="btn-primary">
            重新获取登录链接
          </Link>
          <Link to="/" className="btn-secondary">
            返回首页
          </Link>
        </div>
      </div>
    </div>
  );
}
