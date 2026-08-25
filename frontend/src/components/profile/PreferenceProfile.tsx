/**P6-03 我的饮食偏好画像组件。
 *
 * 结构：
 *   ┌─────────────────────────────────────────────┐
 *   │  最新画像 · 七维雷达（或空状态引导）            │
 *   │    · 时段 / 胃口 / 口味 / 忌口 / 预算         │
 *   │    · 问卷版本 / 字典版本 / 来源溯源           │
 *   │  清空全部画像（Danger 操作）                   │
 *   ├─────────────────────────────────────────────┤
 *   │  画像时间轴（最近 N 条 append-only 快照）       │
 *   │    · 每条单条删除（"移除一条画像记录"）        │
 *   └─────────────────────────────────────────────┘
 */

import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  AlertTriangle,
  ArchiveRestore,
  Clock3,
  DatabaseZap,
  History as HistoryIcon,
  RotateCcw,
  Sparkles,
  Trash2,
} from 'lucide-react';
import { ApiError, api } from '../../services/api/client';
import type { PreferenceSnapshot } from '../../services/api/types';
import { describeFinalReason } from '../../lib/sourceBadge';
import {
  formatWipeSuccessNotice,
  wipeAllPreferencesAndHistory,
} from '../../lib/wipeProfile';
import { track } from '../../lib/track';

type Tab = 'overview' | 'timeline';

const _enumLabel: Record<string, string> = {
  // MealPeriod
  breakfast: '早餐',
  lunch: '午餐',
  afternoon_tea: '下午茶',
  dinner: '晚餐',
  midnight_snack: '夜宵',
  // Appetite
  'appetite::light': '没胃口',
  'appetite::normal': '正常食欲',
  'appetite::hungry': '很饿',
  // Taste
  any: '不限口味',
  'taste::light': '清淡',
  spicy: '麻辣',
  sour: '酸爽',
  sweet: '甜口',
  salty: '咸鲜',
  // BudgetTier
  t_15: '15元内',
  t_30: '15-30元',
  t_50: '30-50元',
  t_80: '50-80元',
  t_unlimited: '80元以上',
  // ExplicitPreference
  undecided: '需要推荐',
  specific: '有明确想吃',
};

function labelize(value: unknown, context?: string): string {
  if (value == null) return '—';
  if (typeof value === 'boolean') return value ? '是' : '否';
  if (typeof value === 'number') return String(value);
  if (typeof value === 'string') {
    const ctxKey = context ? `${context}::${value}` : null;
    if (ctxKey && ctxKey in _enumLabel) return _enumLabel[ctxKey];
    if (value in _enumLabel) return _enumLabel[value];
    return value;
  }
  if (Array.isArray(value)) {
    if (value.length === 0) return '—';
    return value.map((v) => labelize(v, context)).join('、');
  }
  if (typeof value === 'object') return '对象（详情省略）';
  return String(value);
}

function formatDate(iso: string): string {
  try {
    const d = new Date(iso);
    const ymd = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
    const hm = `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
    return `${ymd} ${hm}`;
  } catch {
    return iso;
  }
}

interface DimenRow {
  key: string;
  label: string;
  value: unknown;
  icon: string;
}

function snapshotToDimens(snapshot: Record<string, unknown>): DimenRow[] {
  return [
    { key: 'meal_period', label: '用餐时段', value: snapshot.meal_period, icon: '🕒' },
    { key: 'appetite', label: '胃口状态', value: snapshot.appetite, icon: '🍽️' },
    { key: 'tastes', label: '口味偏好', value: snapshot.tastes, icon: '🌶️' },
    { key: 'avoidances', label: '日常忌口', value: snapshot.avoidances, icon: '🚫' },
    { key: 'budget', label: '预算档位', value: snapshot.budget, icon: '💰' },
    { key: 'max_distance_m', label: '出行距离', value: snapshot.max_distance_m, icon: '📍' },
    { key: 'explicit_food_preference', label: '明确意愿', value: snapshot.explicit_food_preference, icon: '🎯' },
  ];
}

// -------- 子组件：七维条形可视化（纯 SVG，不引入第三方图表库） --------
function DimensChart({ dims }: { dims: DimenRow[] }) {
  // 对七维画"信息量分"：
  //   - null/空/[] → 0
  //   - boolean true → 100
  //   - appetite: light(60) normal(80) hungry(100)
  //   - budget: 越贵分越高（4 档线性）
  //   - tastes/avoidances: 元素数 / 6 档上限 × 100（上限 100）
  //   - meal_period: 单值枚举 → 80
  //   - explicit_food_preference: 非空 → 100，undecided → 30，specific → 100
  //   - max_distance_m: 米数 / 3000 × 100，上限 100
  const bars: Array<{ key: string; label: string; icon: string; value: string; pct: number }> = dims.map(
    (d) => {
      let pct = 0;
      const v = d.value;
      if (v == null) pct = 0;
      else if (typeof v === 'boolean') pct = v ? 100 : 10;
      else if (d.key === 'appetite') {
        pct = { light: 60, normal: 80, hungry: 100 }[String(v)] ?? 0;
      } else if (d.key === 'budget') {
        const tiers = ['t_15', 't_30', 't_50', 't_80', 't_unlimited'];
        const idx = tiers.indexOf(String(v));
        pct = idx < 0 ? 0 : Math.round(((idx + 1) / tiers.length) * 100);
      } else if (typeof v === 'number') {
        if (d.key === 'max_distance_m') pct = Math.min(100, Math.round((v / 3000) * 100));
        else pct = Math.min(100, Math.abs(v));
      } else if (Array.isArray(v)) {
        const cap = d.key === 'tastes' || d.key === 'avoidances' ? 6 : 4;
        pct = Math.min(100, Math.round((v.length / cap) * 100));
      } else if (typeof v === 'string') {
        if (d.key === 'explicit_food_preference') {
          pct = v === 'undecided' ? 30 : v === 'specific' ? 100 : 80;
        } else {
          pct = v === 'any' ? 40 : 80;
        }
      } else {
        pct = 50;
      }
      return {
        key: d.key,
        label: d.label,
        icon: d.icon,
        value: labelize(d.value, d.key),
        pct,
      };
    },
  );

  const rowH = 28;
  const pad = 8;
  const labelW = 110;
  const trackX = labelW + 8;
  const trackW = 320;
  const valueW = 150;
  const W = trackX + trackW + 8 + valueW + pad;
  const H = rowH * bars.length + pad * 2;

  return (
    <figure className="pref-chart" aria-label="七维画像概览">
      <svg viewBox={`0 0 ${W} ${H}`} role="img" width="100%" preserveAspectRatio="xMidYMid meet">
        {bars.map((b, i) => {
          const y = pad + i * rowH;
          const cy = y + rowH / 2;
          const trackY = y + 8;
          const fillW = Math.max(2, Math.round((trackW * b.pct) / 100));
          return (
            <g key={b.key}>
              {/* 左侧：label */}
              <text
                x={pad}
                y={cy + 4}
                className="pref-chart-label"
                fontSize="12"
                fill="var(--color-text-muted)"
              >
                <tspan fontSize="14">{b.icon} </tspan>
                {b.label}
              </text>
              {/* 背景 track */}
              <rect
                x={trackX}
                y={trackY}
                width={trackW}
                height={10}
                rx={5}
                ry={5}
                fill="var(--color-surface-2)"
              />
              {/* 前景 bar */}
              <rect
                x={trackX}
                y={trackY}
                width={fillW}
                height={10}
                rx={5}
                ry={5}
                fill="var(--color-primary)"
              />
              {/* 右侧：pct + value */}
              <text
                x={trackX + trackW + 8}
                y={cy + 4}
                fontSize="11"
                fill="var(--color-text-muted)"
              >
                <tspan fill="var(--color-primary)" fontWeight="600">{b.pct}</tspan>
                <tspan> 分 · </tspan>
                <tspan fill="var(--color-text)" fontSize="12">
                  {b.value.length > 12 ? `${b.value.slice(0, 11)}…` : b.value}
                </tspan>
              </text>
            </g>
          );
        })}
      </svg>
      <figcaption className="pref-chart-caption">
        分数表示该维度画像的"信息量"——越高代表偏好越明确，推荐引擎越容易给出贴合的结果。
      </figcaption>
    </figure>
  );
}

// -------- 子组件：空状态 --------
function EmptyState({ onGo }: { onGo: () => void }) {
  return (
    <div className="pref-empty" role="status" aria-live="polite">
      <div className="pref-empty-illust" aria-hidden>
        <Sparkles size={28} />
      </div>
      <h3>还没有你的饮食偏好画像</h3>
      <p>
        每完成一次推荐，系统就会在 <strong>本地加密</strong> 后保存一份七维偏好快照；
        积累得越多，推荐就越了解你。
      </p>
      <button type="button" className="btn btn-primary" onClick={onGo}>
        <DatabaseZap size={16} aria-hidden />
        <span>去做一次推荐，生成我的画像</span>
      </button>
    </div>
  );
}

// -------- 子组件：最新画像 --------
function LatestCard({ snap, onDismissAlert }: { snap: PreferenceSnapshot; onDismissAlert: () => void }) {
  const dims = useMemo(() => snapshotToDimens(snap.snapshot), [snap.snapshot]);
  const meta = describeFinalReason(
    (snap.snapshot?._meta as { final_reason?: string } | null)?.final_reason ?? null,
  );

  return (
    <section className="pref-card" aria-labelledby="pref-latest-title">
      <header className="pref-card-header">
        <div className="pref-card-title">
          <h2 id="pref-latest-title">最新画像</h2>
          <span className="pref-card-sub">
            <Clock3 size={12} aria-hidden />
            {formatDate(snap.created_at)}
            {snap.source_session_id ? (
              <span className="pref-chip pref-chip--muted" title="可溯源到 P5 会话">
                #{snap.source_session_id.slice(-6)}
              </span>
            ) : null}
          </span>
        </div>
        <span
          className={`source-badge source-badge--${meta.variant}`}
          aria-label={meta.accessibleLabel}
        >
          {meta.label}
        </span>
      </header>

      <div className="pref-meta-row">
        <span className="pref-chip">问卷 v{snap.questionnaire_version}</span>
        <span className="pref-chip">字典 v{snap.dictionary_version}</span>
      </div>

      {meta.summaryText ? (
        <div className={`pref-tip pref-tip--${meta.variant}`} onAnimationEnd={onDismissAlert} role="note">
          {meta.summaryText}
        </div>
      ) : null}

      <DimensChart dims={dims} />

      <dl className="pref-dimen-list">
        {dims.map((d) => (
          <div className="pref-dimen-row" key={d.key}>
            <dt className="pref-dimen-label">
              <span aria-hidden>{d.icon}</span>
              {d.label}
            </dt>
            <dd className={`pref-dimen-value ${d.value == null || (Array.isArray(d.value) && d.value.length === 0) ? 'is-empty' : ''}`}>
              {labelize(d.value, d.key)}
            </dd>
          </div>
        ))}
      </dl>

      {typeof snap.snapshot.ai_follow_up_answers === 'object' &&
      snap.snapshot.ai_follow_up_answers != null &&
      Object.keys(snap.snapshot.ai_follow_up_answers as Record<string, unknown>).length > 0 ? (
        <div className="pref-followup">
          <h3>AI 追问的追加偏好</h3>
          <ul>
            {Object.entries(snap.snapshot.ai_follow_up_answers as Record<string, unknown>).map(
              ([k, v]) => (
                <li key={k}>
                  <span className="pref-followup-key">{k}</span>
                  <span className="pref-followup-value">{labelize(v)}</span>
                </li>
              ),
            )}
          </ul>
        </div>
      ) : null}
    </section>
  );
}

// -------- 子组件：时间轴 --------
function Timeline({
  items,
  onDeleteOne,
  deletingId,
  loadingMore,
  hasMore,
  onLoadMore,
  total,
}: {
  items: PreferenceSnapshot[];
  onDeleteOne: (id: string) => Promise<void>;
  deletingId: string | null;
  loadingMore: boolean;
  hasMore: boolean;
  onLoadMore: () => Promise<void>;
  total: number;
}) {
  return (
    <section className="pref-card" aria-labelledby="pref-timeline-title">
      <header className="pref-card-header">
        <div className="pref-card-title">
          <h2 id="pref-timeline-title">画像时间轴</h2>
          <span className="pref-card-sub">
            <HistoryIcon size={12} aria-hidden />
            共 {total} 条 append-only 记录（从新到旧）
          </span>
        </div>
      </header>

      {items.length === 0 ? (
        <div className="pref-timeline-empty">
          <ArchiveRestore size={20} aria-hidden />
          <p>还没有历史快照，先去做一次推荐吧。</p>
        </div>
      ) : (
        <>
          <ol className="pref-timeline">
            {items.map((snap, idx) => {
              const dims = snapshotToDimens(snap.snapshot).filter(
                (d) =>
                  d.value != null &&
                  !(Array.isArray(d.value) && d.value.length === 0),
              );
              const headDimens = dims.slice(0, 4);
              return (
                <li className="pref-timeline-item" key={snap.id}>
                  <div className={`pref-timeline-dot ${idx === 0 ? 'is-latest' : ''}`} aria-hidden />
                  <div className="pref-timeline-body">
                    <div className="pref-timeline-head">
                      <time dateTime={snap.created_at}>{formatDate(snap.created_at)}</time>
                      {snap.snapshot_version ? (
                        <span className="pref-chip pref-chip--muted" title="画像快照 Schema 版本号（P7-06）">
                          v{snap.snapshot_version}
                        </span>
                      ) : null}
                      <button
                        type="button"
                        className="btn btn-ghost btn-xs pref-timeline-delete"
                        onClick={() => onDeleteOne(snap.id)}
                        disabled={deletingId === snap.id}
                        aria-label={`删除 ${formatDate(snap.created_at)} 的画像记录`}
                      >
                        <Trash2 size={12} aria-hidden />
                        <span>{deletingId === snap.id ? '删除中…' : '删除'}</span>
                      </button>
                    </div>
                    <ul className="pref-timeline-tags">
                      {headDimens.length === 0 ? (
                        <li className="pref-timeline-tag pref-timeline-tag--empty">（空快照）</li>
                      ) : (
                        headDimens.map((d) => (
                          <li className="pref-timeline-tag" key={d.key}>
                            <span aria-hidden>{d.icon}</span>
                            <strong>{d.label}</strong> {labelize(d.value, d.key)}
                          </li>
                        ))
                      )}
                    </ul>
                  </div>
                </li>
              );
            })}
          </ol>

          {/* P7-02：加载更多 */}
          <div className="pref-timeline-loadmore-wrap">
            {hasMore ? (
              <button
                type="button"
                className="btn btn-ghost btn-block"
                disabled={loadingMore}
                onClick={() => void onLoadMore()}
              >
                {loadingMore ? '加载中…' : '加载更多历史快照'}
                <span className="pref-loadmore-hint">
                  （已加载 {items.length} / {total}）
                </span>
              </button>
            ) : total > 0 ? (
              <p className="pref-loadmore-end">已到达末尾（共 {total} 条）</p>
            ) : null}
          </div>
        </>
      )}
    </section>
  );
}

// -------- 主组件 --------
export default function PreferenceProfile({ goRecommend }: { goRecommend: () => void }) {
  const [tab, setTab] = useState<Tab>('overview');
  const [latest, setLatest] = useState<PreferenceSnapshot | null>(null);
  const [timeline, setTimeline] = useState<PreferenceSnapshot[]>([]);
  const [total, setTotal] = useState<number>(0);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [clearing, setClearing] = useState(false);
  const [clearConfirmOpen, setClearConfirmOpen] = useState(false);
  const [wiping, setWiping] = useState(false);
  const [wipeConfirmOpen, setWipeConfirmOpen] = useState(false);

  const PAGE_SIZE = 5;

  const showNotice = useCallback((txt: string) => {
    setNotice(txt);
    window.setTimeout(() => setNotice((cur) => (cur === txt ? null : cur)), 2500);
  }, []);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [latestResp, listResp] = await Promise.allSettled([
        api.preferenceLatest(),
        // P7-02：首屏用 offset=0；拿到第一页同时产出首次 next_cursor
        api.preferenceList({ limit: PAGE_SIZE, offset: 0 }),
      ]);
      if (latestResp.status === 'fulfilled') setLatest(latestResp.value);
      else if (latestResp.reason instanceof ApiError && latestResp.reason.status === 404) setLatest(null);
      else throw latestResp.reason;
      if (listResp.status === 'fulfilled') {
        setTimeline(listResp.value.items);
        setTotal(listResp.value.total);
        setNextCursor(listResp.value.next_cursor);
      } else {
        throw listResp.reason;
      }
    } catch (e) {
      const msg = e instanceof Error ? e.message : '加载偏好画像失败';
      setError(msg);
    } finally {
      setLoading(false);
    }
  }, []);

  const loadMore = useCallback(async () => {
    if (loadingMore || !nextCursor) return;
    setLoadingMore(true);
    setError(null);
    try {
      const resp = await api.preferenceList({ limit: PAGE_SIZE, before: nextCursor });
      // 拼接 + 去重（删除过的可能残留，按 id dedupe）
      setTimeline((prev) => {
        const seen = new Set(prev.map((it) => it.id));
        const extras = resp.items.filter((it) => !seen.has(it.id));
        return [...prev, ...extras];
      });
      setTotal(resp.total);
      setNextCursor(resp.next_cursor);
    } catch (e) {
      const msg = e instanceof Error ? e.message : '加载更多失败';
      setError(msg);
    } finally {
      setLoadingMore(false);
    }
  }, [loadingMore, nextCursor]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const onDeleteOne = useCallback(
    async (id: string) => {
      setDeletingId(id);
      setError(null);
      try {
        await api.preferenceDelete(id);
        showNotice('已删除该条画像记录');
        setTimeline((prev) => prev.filter((it) => it.id !== id));
        if (latest?.id === id) setLatest(null);
        setTotal((prev) => Math.max(0, prev - 1));
      } catch (e) {
        const msg = e instanceof Error ? e.message : '删除失败';
        setError(msg);
      } finally {
        setDeletingId(null);
      }
    },
    [latest?.id, showNotice],
  );

  const onClearAll = useCallback(async () => {
    setClearing(true);
    setError(null);
    try {
      const resp = await api.preferenceDeleteAll();
      showNotice(`已清空全部 ${resp.deleted} 条画像`);
      setLatest(null);
      setTimeline([]);
      setTotal(0);
      setNextCursor(null);
      setClearConfirmOpen(false);
    } catch (e) {
      const msg = e instanceof Error ? e.message : '清空失败';
      setError(msg);
    } finally {
      setClearing(false);
    }
  }, [showNotice]);

  // ---- 一键「重新从零开始」：清空画像 + 推荐历史 + 问卷草稿 + 横幅隐藏记忆
  // 设计语义：把用户在「饮食偏好」维度的一切"记忆"都抹掉，
  // 下一次进推荐页就像首访用户一样，从头答完整的自适应问卷（不会因为草稿/画像被跳过 3–4 道题）。
  const onWipeAll = useCallback(async () => {
    setWiping(true);
    setError(null);
    try {
      track('preference.wipe_all_confirm', { total_snapshots: total });
      const result = await wipeAllPreferencesAndHistory();
      // 状态复位（重新渲染成空画像页）
      setLatest(null);
      setTimeline([]);
      setTotal(0);
      setNextCursor(null);
      setClearConfirmOpen(false);
      setWipeConfirmOpen(false);
      showNotice(formatWipeSuccessNotice(result));
      // 直接跳到推荐页：首屏会从草稿空态开始 → 完整自适应问卷
      window.setTimeout(() => goRecommend(), 400);
    } catch (e) {
      const msg = e instanceof Error ? e.message : '重置失败';
      setError(msg);
    } finally {
      setWiping(false);
    }
  }, [goRecommend, showNotice, total]);

  return (
    <div className="pref-profile">
      <div className="pref-header" style={{ alignItems: 'flex-start' }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <h1>我的饮食偏好画像</h1>
          <p className="pref-sub">
            基于你每次推荐时的问卷与 AI 追问，形成七维饮食画像。数据仅你可见，可随时一键清空。
          </p>
        </div>
        <div style={{ display: 'flex', gap: 'var(--space-2)', flexShrink: 0, marginTop: '4px' }}>
          <button
            type="button"
            className="btn btn-primary btn-sm"
            onClick={() => goRecommend()}
            title="跳转到推荐页生成新的画像"
          >
            <DatabaseZap size={14} aria-hidden />
            <span>做一次推荐</span>
          </button>
          <button
            type="button"
            className="btn btn-secondary btn-sm"
            onClick={() => {
              track('preference.wipe_all_click', { total_snapshots: total });
              setWipeConfirmOpen(true);
            }}
            title="清空画像 + 推荐历史 + 问卷草稿 + 横幅隐藏记忆，让下次推荐从头开始"
            style={{
              borderColor: 'color-mix(in oklab, #e67e22 55%, var(--color-border))',
              background: 'color-mix(in oklab, #e67e22 12%, var(--color-surface))',
              color: 'color-mix(in oklab, #c0392b 70%, var(--color-text))',
            }}
          >
            <RotateCcw size={14} aria-hidden />
            <span>🔄 重新从零开始</span>
          </button>
        </div>
      </div>

      {/* 🔄 重新从零开始：二次确认弹窗（放 header 下最显眼，避免误触） */}
      {wipeConfirmOpen ? (
        <div
          className="pref-wipe-confirm"
          role="alertdialog"
          aria-modal="true"
          aria-labelledby="pref-wipe-title"
          aria-describedby="pref-wipe-desc"
          style={{
            marginBottom: 'var(--space-3)',
            padding: 'var(--space-3) var(--space-4)',
            borderRadius: 'var(--radius-md)',
            border: '1px solid color-mix(in oklab, #e67e22 50%, var(--color-border))',
            background:
              'linear-gradient(135deg, color-mix(in oklab, #e67e22 14%, var(--color-surface)), color-mix(in oklab, #c0392b 10%, var(--color-surface)))',
          }}
        >
          <div style={{ display: 'flex', alignItems: 'flex-start', gap: 'var(--space-2)', marginBottom: 'var(--space-2)' }}>
            <AlertTriangle size={20} aria-hidden style={{ color: '#e67e22', flexShrink: 0, marginTop: '2px' }} />
            <div style={{ flex: 1, minWidth: 0 }}>
              <h3 id="pref-wipe-title" style={{ margin: 0, fontSize: '1.05rem' }}>
                确认要把你的饮食偏好"一键归零"？
              </h3>
              <p id="pref-wipe-desc" style={{ margin: '6px 0 0', fontSize: '0.9rem', opacity: 0.92 }}>
                这会同时做 <strong>4</strong> 件事，<strong>全部删除后不可恢复</strong>：
              </p>
              <ol style={{ margin: '8px 0 0', paddingLeft: '20px', fontSize: '0.88rem', lineHeight: 1.7 }}>
                <li>删除 <strong>全部画像快照</strong>（当前共 {total} 条）</li>
                <li>删除 <strong>全部推荐历史</strong>（"推荐历史"页的记录）</li>
                <li>清空 <strong>问卷草稿</strong>（下次进推荐会从头答完整问卷）</li>
                <li>清空 <strong>活动横幅今日隐藏记忆</strong>（首页活动会重新出现）</li>
              </ol>
            </div>
          </div>
          <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 'var(--space-2)', marginTop: 'var(--space-2)' }}>
            <button
              type="button"
              className="btn btn-ghost btn-sm"
              disabled={wiping}
              onClick={() => {
                track('preference.wipe_all_cancel', { total_snapshots: total });
                setWipeConfirmOpen(false);
              }}
            >
              取消
            </button>
            <button
              type="button"
              className="btn btn-danger btn-sm"
              disabled={wiping}
              onClick={() => void onWipeAll()}
            >
              <RotateCcw size={14} aria-hidden />
              {wiping ? '正在归零…' : '是的，重新从零开始'}
            </button>
          </div>
        </div>
      ) : null}

      <nav className="pref-tabs" role="tablist" aria-label="偏好画像页签">
        <button
          role="tab"
          aria-selected={tab === 'overview'}
          className={`pref-tab ${tab === 'overview' ? 'is-active' : ''}`}
          onClick={() => setTab('overview')}
          type="button"
        >
          <Sparkles size={14} aria-hidden />
          最新画像
        </button>
        <button
          role="tab"
          aria-selected={tab === 'timeline'}
          className={`pref-tab ${tab === 'timeline' ? 'is-active' : ''}`}
          onClick={() => setTab('timeline')}
          type="button"
        >
          <Clock3 size={14} aria-hidden />
          时间轴
          {total > 0 ? <span className="pref-tab-count">{total}</span> : null}
        </button>
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

      {loading ? (
        <div className="pref-card pref-skeleton" aria-busy="true">
          <div className="pref-skeleton-line" style={{ width: '55%' }} />
          <div className="pref-skeleton-line" style={{ width: '90%' }} />
          <div className="pref-skeleton-line" style={{ width: '75%' }} />
          <div className="pref-skeleton-line" style={{ width: '88%' }} />
        </div>
      ) : (
        <>
          {!latest && tab === 'overview' ? (
            <EmptyState onGo={goRecommend} />
          ) : (
            <>
              {tab === 'overview' && latest ? (
                <LatestCard snap={latest} onDismissAlert={() => undefined} />
              ) : null}
              {tab === 'timeline' ? (
                <Timeline
                  items={timeline}
                  onDeleteOne={onDeleteOne}
                  deletingId={deletingId}
                  loadingMore={loadingMore}
                  hasMore={nextCursor != null && timeline.length < total}
                  onLoadMore={loadMore}
                  total={total}
                />
              ) : null}
            </>
          )}
        </>
      )}

      {total > 0 ? (
        <section className="pref-card danger-zone-sm" aria-label="画像危险操作">
          <div className="danger-title-sm">
            <AlertTriangle size={16} aria-hidden />
            <h3>清空全部画像</h3>
          </div>
          <p>
            将删除你当前 <strong>{total}</strong> 条偏好快照记录。
            推荐历史不受影响（可在"推荐历史"中另行删除）。
          </p>
          {clearConfirmOpen ? (
            <div className="danger-confirm-sm">
              <button
                type="button"
                className="btn btn-ghost btn-sm"
                disabled={clearing}
                onClick={() => setClearConfirmOpen(false)}
              >
                取消
              </button>
              <button
                type="button"
                className="btn btn-danger btn-sm"
                disabled={clearing}
                onClick={() => void onClearAll()}
              >
                <Trash2 size={14} aria-hidden />
                {clearing ? '清空中…' : '确认清空全部画像'}
              </button>
            </div>
          ) : (
            <button
              type="button"
              className="btn btn-danger-outline btn-sm"
              onClick={() => setClearConfirmOpen(true)}
            >
              <Trash2 size={14} aria-hidden />
              清空全部画像
            </button>
          )}
        </section>
      ) : null}
    </div>
  );
}
