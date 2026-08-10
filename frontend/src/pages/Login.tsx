/** Magic Link 登录页。
 *
 *  流程：用户输入邮箱 → 点提交 → 后端 /auth/magic-link 发邮件 → 提示"请查收邮箱"。
 *  无论邮箱是否已注册，都显示同一提示（防止邮箱枚举）。
 */
import { useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router';
import { useAuth } from '../context/AuthContext';

export default function Login() {
  const { sendMagicLink, isAuthenticated, loading: authLoading } = useAuth();
  const [params] = useSearchParams();
  const nav = useNavigate();

  const [email, setEmail] = useState(params.get('email') ?? '');
  const [submitting, setSubmitting] = useState(false);
  const [sent, setSent] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  // 保存跳转来源，便于登录后回到原页面
  const returnTo = params.get('return_to') ?? '/';

  // 已登录用户直接跳 returnTo
  if (!authLoading && isAuthenticated) {
    queueMicrotask(() => nav(returnTo, { replace: true }));
  }

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setErr(null);
    if (!email.trim()) {
      setErr('请输入邮箱');
      return;
    }
    setSubmitting(true);
    try {
      const redirectTo = `${window.location.origin}/auth/callback?next=${encodeURIComponent(returnTo)}`;
      await sendMagicLink(email.trim(), redirectTo);
      setSent(true);
    } catch (err2) {
      setErr(err2 instanceof Error ? err2.message : '发送失败，请稍后重试');
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="login-page">
      <div className="login-card" role="main" aria-labelledby="login-title">
        <h1 id="login-title" className="login-title">
          登录 EatWhat
        </h1>
        <p className="login-subtitle">
          输入邮箱，我们会给你发送一个<strong>一次性登录链接</strong>，无需密码。
        </p>

        {sent ? (
          <div className="login-sent" role="status">
            <h2>请查收邮箱</h2>
            <p>
              如果 <code>{email}</code> 与我们系统匹配，登录链接邮件已发送。
            </p>
            <p className="login-tip">
              小贴士：没收到？检查垃圾邮件箱，或 30 秒后重新发送。
            </p>
            <button type="button" className="btn-secondary" onClick={() => setSent(false)}>
              使用其他邮箱
            </button>
          </div>
        ) : (
          <form className="login-form" onSubmit={onSubmit} noValidate>
            <label htmlFor="email-input">邮箱地址</label>
            <input
              id="email-input"
              type="email"
              autoComplete="email"
              placeholder="you@example.com"
              value={email}
              onChange={(e2) => setEmail(e2.target.value)}
              disabled={submitting || authLoading}
              required
            />
            {err && (
              <p className="form-error" role="alert">
                {err}
              </p>
            )}
            <button type="submit" className="btn-primary" disabled={submitting || authLoading}>
              {submitting ? '发送中…' : '发送登录链接'}
            </button>
          </form>
        )}

        <div className="login-footer">
          <Link to="/">← 返回首页</Link>
          <span className="dot">·</span>
          <Link to="/privacy">隐私政策</Link>
        </div>
      </div>
    </div>
  );
}
