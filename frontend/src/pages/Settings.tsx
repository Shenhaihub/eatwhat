import { useEffect, useState } from 'react';
import {
  Activity,
  AlertTriangle,
  ArrowLeft,
  BarChart3,
  History as HistoryIcon,
  LogOut,
  Shield,
  Sparkles,
  Trash2,
  UserRound,
  Users,
} from 'lucide-react';
import { Link, useNavigate, useSearchParams } from 'react-router';
import PreferenceProfile from '../components/profile/PreferenceProfile';
import HistoryInline from '../components/profile/HistoryInline';
import { api } from '../services/api/client';
import { useAuth } from '../context/AuthContext';
import type { SystemAiStatsResponse } from '../services/api/types';

type Tab = 'account' | 'history' | 'preference';

const TAB_ITEMS: Array<{ id: Tab; label: string; icon: typeof Users }> = [
  { id: 'account', label: '账户设置', icon: UserRound },
  { id: 'history', label: '推荐历史', icon: HistoryIcon },
  { id: 'preference', label: '饮食偏好', icon: Sparkles },
];

export default function Settings() {
  const { user, logout } = useAuth();
  const nav = useNavigate();
  const [params] = useSearchParams();
  // 初始化：优先读取 URL 上的 ?tab=（account | history | preference）；
  // 不合法或缺省时默认 'account'，保证从 Recommend 跳转能直接落在"饮食偏好"tab。
  const validTabs = new Set<string>(TAB_ITEMS.map((t) => t.id));
  const initialTab: Tab =
    (() => {
      const raw = params.get('tab');
      if (raw && validTabs.has(raw)) return raw as Tab;
      return 'account';
    })();
  const [tab, setTab] = useState<Tab>(initialTab);

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
        <Link to="/" className="btn btn-ghost btn-sm settings-back" aria-label="返回首页">
          <ArrowLeft size={16} aria-hidden />
          <span>返回</span>
        </Link>
        <div className="settings-top-title">
          <h1>我的</h1>
          <p className="settings-sub">账户、推荐历史与饮食偏好一站式管理。</p>
        </div>
      </div>

      <nav className="pref-tabs" role="tablist" aria-label="我的页签">
        {TAB_ITEMS.map((it) => {
          const Icon = it.icon;
          const active = tab === it.id;
          return (
            <button
              role="tab"
              aria-selected={active}
              key={it.id}
              onClick={() => setTab(it.id)}
              className={`pref-tab ${active ? 'is-active' : ''}`}
              type="button"
            >
              <Icon size={14} aria-hidden />
              {it.label}
            </button>
          );
        })}
      </nav>

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

      {tab === 'preference' ? (
        <PreferenceProfile goRecommend={() => nav('/recommend')} />
      ) : tab === 'history' ? (
        <HistoryInline />
      ) : (
        <AccountSection
          expectedEmail={expectedEmail}
          onLogout={onLogout}
          loggingOut={loggingOut}
          confirmOpen={confirmOpen}
          setConfirmOpen={setConfirmOpen}
          confirmEmail={confirmEmail}
          setConfirmEmail={setConfirmEmail}
          canDelete={canDelete}
          onDelete={onDelete}
          deleting={deleting}
        />
      )}
    </div>
  );
}

function AccountSection(props: {
  expectedEmail: string;
  onLogout: () => Promise<void>;
  loggingOut: boolean;
  confirmOpen: boolean;
  setConfirmOpen: (v: boolean) => void;
  confirmEmail: string;
  setConfirmEmail: (v: string) => void;
  canDelete: boolean;
  onDelete: () => Promise<void>;
  deleting: boolean;
}) {
  const {
    expectedEmail,
    onLogout,
    loggingOut,
    confirmOpen,
    setConfirmOpen,
    confirmEmail,
    setConfirmEmail,
    canDelete,
    onDelete,
    deleting,
  } = props;
  return (
    <>
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

      {/* P7-03 / P7-09：观测仪表盘最小 UI */}
      <ObservabilitySection />

      <section className="settings-card danger-zone" aria-label="危险操作区">
        <div className="danger-title">
          <AlertTriangle size={18} aria-hidden />
          <h2>危险操作</h2>
        </div>
        <p className="danger-desc">
          删除账号后，你的身份信息、全部推荐历史及偏好画像将被永久移除，
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
    </>
  );
}

// -----------------------------
// P7-03 / P7-09：观测仪表盘
// -----------------------------
function ObservabilitySection() {
  const [stats, setStats] = useState<SystemAiStatsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [stage, setStage] = useState<'all' | 'follow_up' | 'final'>('all');
  const [sampleOpen, setSampleOpen] = useState(false);
  const [refreshing, setRefreshing] = useState(false);

  const fetchStats = async (s: typeof stage, signal?: AbortSignal) => {
    const base = loading ? false : true;
    if (base) setRefreshing(true);
    setError(null);
    try {
      const data = await api.systemAiStats(
        { limit: 200, stage: s === 'all' ? undefined : s },
        { signal },
      );
      setStats(data);
    } catch (e) {
      // 组件卸载 / 切换 tab 时的主动 abort——所有包装形式的 abort 都静默忽略，不显示红字
      const isAbort =
        (signal?.aborted === true) ||
        (e instanceof DOMException && e.name === 'AbortError') ||
        (typeof e === 'object' && e !== null && 'name' in e && (e as { name: string }).name === 'AbortError') ||
        (e instanceof Error && /Abort|aborted|signal/i.test(e.message));
      if (isAbort) return;
      const msg = e instanceof Error ? e.message : '获取观测数据失败';
      setError(msg);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  };

  useEffect(() => {
    const ctrl = new AbortController();
    void fetchStats(stage, ctrl.signal);
    return () => ctrl.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stage]);

  return (
    <section className="settings-card observability-card" aria-label="AI 调用观测">
      <div className="obs-header">
        <div className="obs-title-wrap">
          <div className="settings-row-icon settings-row-icon--primary">
            <BarChart3 size={18} aria-hidden />
          </div>
          <div>
            <p className="obs-title">
              AI 调用观测 <span className="obs-subtitle">· Dashboard v0.1</span>
            </p>
            <p className="obs-desc">
              最近 {stats ? stats.queried_records : '--'} 次 AI 调用的整体画像 · 用户/会话 ID 已脱敏
              {stats?.window?.newest_ts
                ? ` · 统计窗口 ${new Date(stats.window.oldest_ts ?? stats.window.newest_ts).toLocaleString()} ~ ${new Date(
                    stats.window.newest_ts,
                  ).toLocaleString()}`
                : ''}
            </p>
          </div>
        </div>
        <div className="obs-actions">
          <div className="obs-seg" role="tablist" aria-label="Stage 过滤">
            {(['all', 'follow_up', 'final'] as const).map((s) => (
              <button
                key={s}
                role="tab"
                type="button"
                aria-selected={stage === s}
                className={`obs-seg-btn ${stage === s ? 'is-active' : ''}`}
                onClick={() => setStage(s)}
                disabled={loading}
              >
                {s === 'all' ? '全部' : s === 'follow_up' ? '追问' : '最终'}
              </button>
            ))}
          </div>
          <button
            type="button"
            className="btn btn-ghost btn-sm"
            disabled={loading || refreshing}
            onClick={() => void fetchStats(stage)}
          >
            <Activity size={14} aria-hidden />
            {refreshing ? '刷新中…' : '刷新'}
          </button>
        </div>
      </div>

      {error ? (
        <div className="toast-error" role="alert">
          {error}
        </div>
      ) : null}

      {loading && !stats ? (
        <div className="obs-grid">
          {Array.from({ length: 3 }).map((_, i) => (
            <div key={i} className="obs-card obs-card--skeleton">
              <div className="obs-skeleton-line obs-skeleton-line--title" />
              <div className="obs-skeleton-line obs-skeleton-line--big" />
              <div className="obs-skeleton-line obs-skeleton-line--small" />
            </div>
          ))}
        </div>
      ) : stats ? (
        <div className="obs-grid">
          {/* Card 1：画像使用率 */}
          <div className="obs-card">
            <p className="obs-card-title">
              <Sparkles size={14} aria-hidden />
              画像上下文利用率
            </p>
            <p className="obs-card-value">
              {Math.round(stats.pref_context_used_rate * 10000) / 100}
              <span className="obs-card-unit">%</span>
            </p>
            <p className="obs-card-foot">
              总调用 {stats.queried_records} · 画像命中平均快照 {round(stats.avg_snapshot_count_used)}
            </p>
            <div className="obs-meter" aria-hidden>
              <div
                className="obs-meter-fill obs-meter-fill--primary"
                style={{ width: `${clampPct(stats.pref_context_used_rate * 100)}%` }}
              />
            </div>
          </div>

          {/* Card 2：平均 prompt 长度 */}
          <div className="obs-card">
            <p className="obs-card-title">
              <Activity size={14} aria-hidden />
              平均 Prompt 长度
            </p>
            <p className="obs-card-value">
              {stats.avg_total_prompt_chars != null ? stats.avg_total_prompt_chars : 0}
              <span className="obs-card-unit">字</span>
            </p>
            {(() => {
              const stages = Object.entries(stats.breakdown_by_stage);
              if (!stages.length) {
                return (
                  <p className="obs-card-foot">
                    阶段细分：暂无
                  </p>
                );
              }
              return (
                <ul className="obs-stage-chips" aria-label="各阶段 avg prompt 长度">
                  {stages.map(([k, v]) => (
                    <li key={k} title={`${k} 平均 prompt ${v.avg_total_prompt_chars} 字，共 ${v.calls} 次`}>
                      <span className="obs-chip-label">
                        {k === 'final' ? '最终' : k === 'follow_up' ? '追问' : k}
                      </span>
                      <span className="obs-chip-num">{v.avg_total_prompt_chars}字</span>
                    </li>
                  ))}
                </ul>
              );
            })()}
            {stats.avg_total_prompt_chars ? (
              <div className="obs-meter" aria-hidden>
                <div
                  className="obs-meter-fill obs-meter-fill--success"
                  style={{
                    width: `${clampPct(
                      // 线性映射 0..4000 → 0..100%
                      (stats.avg_total_prompt_chars / 4000) * 100,
                    )}%`,
                  }}
                />
              </div>
            ) : null}
          </div>

          {/* Card 3：Outcome 分布 */}
          <div className="obs-card">
            <p className="obs-card-title">
              <BarChart3 size={14} aria-hidden />
              Outcome 分布
            </p>
            <div className="obs-outcome-list">
              {(() => {
                const entries = Object.entries(stats.outcome_breakdown)
                  .filter(([, v]) => typeof v === 'number')
                  .sort(([, a], [, b]) => (b as number) - (a as number));
                const total = entries.reduce<number>((s, [, v]) => s + ((v as number) || 0), 0);
                if (!entries.length) {
                  return <p className="obs-empty">暂无</p>;
                }
                const labelizeOutcome = (k: string) => {
                  switch (k) {
                    case 'ok':
                      return 'AI 正常';
                    case 'fallback_rules_engine':
                      return '规则引擎降级';
                    case 'fail':
                      return 'AI 失败';
                    default:
                      return k;
                  }
                };
                return entries.map(([k, v]) => {
                  const count = v as number;
                  const pct = total ? (count / total) * 100 : 0;
                  const danger = k === 'fail';
                  const primary = k === 'ok';
                  return (
                    <div key={k} className="obs-outcome-row">
                      <div className="obs-outcome-head">
                        <span className="obs-outcome-key">{labelizeOutcome(k)}</span>
                        <span className="obs-outcome-count">
                          {count} · {Math.round(pct * 10) / 10}%
                        </span>
                      </div>
                      <div className="obs-bar" aria-hidden>
                        <div
                          className={`obs-bar-fill ${
                            danger
                              ? 'obs-bar-fill--danger'
                              : primary
                                ? 'obs-bar-fill--primary'
                                : 'obs-bar-fill--success'
                          }`}
                          style={{ width: `${pct}%` }}
                        />
                      </div>
                    </div>
                  );
                });
              })()}
            </div>
          </div>
        </div>
      ) : null}

      {/* 样本详情 */}
      {stats && stats.sample_records?.length ? (
        <div className="obs-sample">
          <button
            type="button"
            className="obs-sample-toggle"
            aria-expanded={sampleOpen}
            onClick={() => setSampleOpen((v) => !v)}
          >
            <span>{sampleOpen ? '收起' : '展开'}样本记录（最近 {stats.sample_records.length}）</span>
            <ChevronFlipped open={sampleOpen} />
          </button>
          {sampleOpen ? (
            <div className="obs-sample-list" role="list">
              {stats.sample_records.slice(0, 12).map((r, idx) => (
                <div key={idx} className="obs-sample-item" role="listitem">
                  <div className="obs-sample-head">
                    <span className={`obs-pill ${r.ai_stage === 'final' ? 'is-final' : r.ai_stage === 'follow_up' ? 'is-follow' : ''}`}>
                      {r.ai_stage ?? '?'}
                    </span>
                    <span
                      className={`obs-pill ${
                        r.ai_outcome === 'ok'
                          ? 'is-used'
                          : r.ai_outcome === 'fail'
                            ? 'is-nouse'
                            : ''
                      }`}
                      title={`ai_outcome=${r.ai_outcome}`}
                    >
                      {r.ai_outcome === 'ok'
                        ? '结果 OK'
                        : r.ai_outcome === 'fallback_rules_engine'
                          ? '规则引擎降级'
                          : r.ai_outcome === 'fail'
                            ? '失败'
                            : r.ai_outcome}
                    </span>
                    <span className={`obs-pill ${r.preference_context_used ? 'is-used' : 'is-nouse'}`}>
                      {r.preference_context_used ? '画像命中' : '画像未用'}
                    </span>
                    <span className="obs-sample-meta">
                      快照 {r.preference_context_snapshot_count ?? 0} · prompt{' '}
                      {r.total_prompt_chars ?? '-'}字
                    </span>
                  </div>
                  <div className="obs-sample-meta2">
                    user:{r.user_id_sha1_10 ?? r.user_id ?? '-'} · session:
                    {r.session_id_sha1_10 ?? r.session_id ?? '-'} ·{' '}
                    {r.ts_iso ? new Date(r.ts_iso).toLocaleString() : '-'}
                    {r.ai_fail_code ? ` · fail_code=${r.ai_fail_code}` : ''}
                  </div>
                </div>
              ))}
            </div>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}

function ChevronFlipped(props: { open: boolean }) {
  return (
    <svg
      width="16"
      height="16"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={`obs-chevron ${props.open ? 'is-open' : ''}`}
      aria-hidden
    >
      <polyline points="6 9 12 15 18 9" />
    </svg>
  );
}

function clampPct(v: number) {
  if (!isFinite(v)) return 0;
  return Math.max(0, Math.min(100, v));
}
function round(v: number | null | undefined, digits = 2) {
  if (v == null || !isFinite(v)) return '--';
  const d = Math.pow(10, digits);
  return Math.round(v * d) / d;
}
