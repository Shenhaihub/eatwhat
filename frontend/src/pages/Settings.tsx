import { useEffect, useState } from 'react';
import { AlertTriangle, ArrowLeft, History as HistoryIcon, LogOut, Shield, Trash2, UserRound } from 'lucide-react';
import { Link, useNavigate } from 'react-router';
import { api } from '../services/api/client';
import { useAuth } from '../context/AuthContext';

export default function Settings() {
  const { user, logout } = useAuth();
  const nav = useNavigate();

  const [loggingOut, setLoggingOut] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [confirmEmail, setConfirmEmail] = useState('');
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    if (notice) {
      const t = setTimeout(() => setNotice(null), 2500);
      return () => clearTimeout(t);
    }
    return undefined;
  }, [notice]);

  const expectedEmail = user?.email ?? '';
  const canDelete =
    !!expectedEmail && confirmEmail.trim().toLowerCase() === expectedEmail.trim().toLowerCase();

  const onLogout = async () => {
    setLoggingOut(true);
    try {
      await logout();
      nav('/', { replace: true });
    } catch (e) {
      const msg = e instanceof Error ? e.message : '退出登录失败';
      setError(msg);
    } finally {
      setLoggingOut(false);
    }
  };

  const onDelete = async () => {
    setDeleting(true);
    setError(null);
    try {
      await api.accountDelete();
      // GDPR 成功：清 session（前端自己清），跳首页
      try {
        await logout({ skipServer: true });
      } catch {
        // ignore – token 可能已失效
      }
      nav('/', { replace: true, state: { deleted: true } satisfies Record<string, boolean> });
    } catch (e) {
      const msg = e instanceof Error ? e.message : '删除账号失败';
      setError(msg);
    } finally {
      setDeleting(false);
    }
  };

  return (
    <div className="page-shell settings-page">
      <div className="settings-top">
        <Link to="/history" className="btn btn-ghost btn-sm settings-back" aria-label="返回历史">
          <ArrowLeft size={16} aria-hidden />
          <span>返回</span>
        </Link>
        <div className="settings-top-title">
          <h1>账户</h1>
          <p className="settings-sub">管理你的登录方式、推荐历史与数据。</p>
        </div>
      </div>

      {error ? (
        <div className="toast-error" role="alert">
          {error}
        </div>
      ) : null}
      {notice ? (
        <div className="toast-info" role="status">
          {notice}
        </div>
      ) : null}

      <section className="settings-card">
        <div className="settings-row">
          <div className="settings-row-icon settings-row-icon--primary">
            <UserRound size={18} aria-hidden />
          </div>
          <div className="settings-row-body">
            <p className="settings-row-title">已登录账户</p>
            <p className="settings-row-value">{expectedEmail || '未知'}</p>
          </div>
        </div>
        <div className="settings-row">
          <div className="settings-row-icon settings-row-icon--success">
            <Shield size={18} aria-hidden />
          </div>
          <div className="settings-row-body">
            <p className="settings-row-title">登录方式</p>
            <p className="settings-row-value">邮箱 Magic Link · 无需密码</p>
          </div>
        </div>
        <div className="settings-row settings-row--with-action">
          <div className="settings-row-icon">
            <HistoryIcon size={18} aria-hidden />
          </div>
          <div className="settings-row-body">
            <p className="settings-row-title">推荐历史</p>
            <p className="settings-row-value">查看、删除、清空过往推荐记录</p>
          </div>
          <Link to="/history" className="btn btn-ghost btn-sm">
            去查看
          </Link>
        </div>

        <div className="settings-divider" />

        <button
          type="button"
          className="btn btn-ghost btn-block settings-logout"
          onClick={onLogout}
          disabled={loggingOut}
        >
          <LogOut size={16} aria-hidden />
          <span>{loggingOut ? '退出中…' : '退出登录'}</span>
        </button>
      </section>

      <section className="settings-card danger-zone" aria-label="危险操作区">
        <div className="danger-title">
          <AlertTriangle size={18} aria-hidden />
          <h2>危险操作</h2>
        </div>
        <p className="danger-desc">
          删除账号后，你的身份信息、全部推荐历史及关联数据将被永久移除，
          且<strong>无法恢复</strong>。若有需要请在删除前先通过
          <Link to="/history">历史记录</Link>自行备份。
        </p>

        {confirmOpen ? (
          <div className="danger-confirm">
            <p>
              请输入你的邮箱 <strong>{expectedEmail}</strong> 以确认删除：
            </p>
            <label className="sr-only" htmlFor="confirm-email">
              确认邮箱
            </label>
            <input
              id="confirm-email"
              type="email"
              className="input"
              placeholder={expectedEmail}
              autoCapitalize="none"
              autoCorrect="off"
              spellCheck={false}
              value={confirmEmail}
              onChange={(e) => setConfirmEmail(e.target.value)}
              disabled={deleting}
            />
            <div className="danger-confirm-actions">
              <button
                type="button"
                className="btn btn-ghost"
                onClick={() => {
                  setConfirmOpen(false);
                  setConfirmEmail('');
                }}
                disabled={deleting}
              >
                取消
              </button>
              <button
                type="button"
                className="btn btn-danger"
                onClick={onDelete}
                disabled={!canDelete || deleting}
              >
                <Trash2 size={16} aria-hidden />
                <span>{deleting ? '删除中…' : '确认永久删除账号'}</span>
              </button>
            </div>
          </div>
        ) : (
          <button
            type="button"
            className="btn btn-danger-outline btn-block"
            onClick={() => setConfirmOpen(true)}
          >
            <Trash2 size={16} aria-hidden />
            <span>删除我的 EatWhat 账号</span>
          </button>
        )}
      </section>

      <section className="settings-links">
        <h3>说明</h3>
        <ul>
          <li>
            <Link to="/about">关于 EatWhat</Link>
          </li>
          <li>
            <Link to="/privacy">隐私说明</Link>
          </li>
          <li>
            <Link to="/disclaimer">免责声明</Link>
          </li>
        </ul>
      </section>
    </div>
  );
}
