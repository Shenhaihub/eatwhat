import { useCallback, useEffect, useMemo, useState } from 'react';
import { AlertTriangle, History as HistoryIcon, PlusCircle, RotateCcw, Search, Settings, Trash2 } from 'lucide-react';
import { Link, useNavigate } from 'react-router';
import { api } from '../services/api/client';
import type { HistoryRecord } from '../services/api/types';
import { describeFinalReason } from '../lib/sourceBadge';
import { displayFoodName } from '../lib/foodNames';
import {
  formatWipeSuccessNotice,
  wipeAllPreferencesAndHistory,
} from '../lib/wipeProfile';
import { track } from '../lib/track';

const PAGE_SIZE = 20;

function formatTime(iso: string): string {
  try {
    const d = new Date(iso);
    const now = new Date();
    const sameDay = d.toDateString() === now.toDateString();
    const pad = (n: number) => n.toString().padStart(2, '0');
    const hm = `${pad(d.getHours())}:${pad(d.getMinutes())}`;
    if (sameDay) return `今天 ${hm}`;
    const yesterday = new Date(now);
    yesterday.setDate(now.getDate() - 1);
    if (d.toDateString() === yesterday.toDateString()) return `昨天 ${hm}`;
    return `${d.getFullYear()}/${pad(d.getMonth() + 1)}/${pad(d.getDate())} ${hm}`;
  } catch {
    return iso;
  }
}

export default function History() {
  const nav = useNavigate();
  const [records, setRecords] = useState<HistoryRecord[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [initialLoading, setInitialLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [offset, setOffset] = useState(0);
  const [hasMore, setHasMore] = useState(true);
  const [actionInFlightId, setActionInFlightId] = useState<string | null>(null);
  const [clearing, setClearing] = useState(false);
  const [showConfirmClear, setShowConfirmClear] = useState(false);
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);
  const [wiping, setWiping] = useState(false);
  const [showConfirmWipe, setShowConfirmWipe] = useState(false);

  const showToast = useCallback((txt: string, isError = false) => {
    setError(isError ? txt : null);
    if (!isError) {
      // History 之前用 error 兼做清空成功提示；保持语义：非错误场景也塞到这里（UI toast-error 颜色偏红，
      // 但实际没有单独的 toast-success，用 setError 复用同一个 banner 区域；临时再用 setTimeout 抹掉）。
      setError(txt);
      setTimeout(() => setError((cur) => (cur === txt ? null : cur)), 2800);
    }
  }, []);

  const loadList = useCallback(
    async (nextOffset: number, append: boolean) => {
      setLoading(true);
      setError(null);
      try {
        const res = await api.historyList({ limit: PAGE_SIZE, offset: nextOffset });
        setTotal(res.total);
        setHasMore(nextOffset + res.items.length < res.total);
        setRecords((prev) => (append ? [...prev, ...res.items] : res.items));
      } catch (e) {
        const msg = e instanceof Error ? e.message : '加载失败';
        setError(msg);
      } finally {
        setLoading(false);
        setInitialLoading(false);
      }
    },
    [],
  );

  useEffect(() => {
    setOffset(0);
    setHasMore(true);
    loadList(0, false).catch(() => undefined);
  }, [loadList]);

  const onDeleteOne = async (id: string) => {
    setActionInFlightId(id);
    try {
      await api.historyDelete(id);
      setRecords((prev) => prev.filter((r) => r.id !== id));
      setTotal((t) => Math.max(0, t - 1));
      setConfirmDeleteId(null);
    } catch (e) {
      const msg = e instanceof Error ? e.message : '删除失败';
      setError(msg);
    } finally {
      setActionInFlightId(null);
    }
  };

  const onClearAll = async () => {
    setClearing(true);
    try {
      const res = await api.historyDeleteAll();
      setRecords([]);
      setTotal(0);
      setShowConfirmClear(false);
      showToast(`已清空 ${res.deleted} 条历史记录`);
    } catch (e) {
      const msg = e instanceof Error ? e.message : '清空失败';
      setError(msg);
    } finally {
      setClearing(false);
    }
  };

  // B：历史页新增「🔄 重新从零开始」，语义与画像页完全一致（4 类数据一起清）
  const onWipeAll = async () => {
    setWiping(true);
    try {
      track('history.wipe_all_confirm', { total_records: total });
      const result = await wipeAllPreferencesAndHistory();
      setRecords([]);
      setTotal(0);
      setHasMore(false);
      setOffset(0);
      setShowConfirmWipe(false);
      setShowConfirmClear(false);
      showToast(formatWipeSuccessNotice(result));
      // 归零后直接跳推荐页从头开始完整问卷
      window.setTimeout(() => nav('/recommend', { replace: true }), 450);
    } catch (e) {
      const msg = e instanceof Error ? e.message : '重置失败';
      setError(msg);
    } finally {
      setWiping(false);
    }
  };

  const loadMore = () => {
    if (loading || !hasMore) return;
    const next = offset + PAGE_SIZE;
    setOffset(next);
    loadList(next, true).catch(() => undefined);
  };

  const tagsText = useCallback((r: HistoryRecord): string => {
    const parts: string[] = [];
    if (r.food_code) parts.push(displayFoodName({ food_code: r.food_code }));
    if (r.tags && r.tags.length > 0) {
      parts.push(...r.tags.slice(0, 3));
    }
    return parts.join(' · ');
  }, []);

  const listItems = useMemo(() => records, [records]);

  return (
    <div className="page-shell history-page">
      <div className="history-top">
        <div className="history-top-title">
          <div className="history-title-row">
            <HistoryIcon size={20} aria-hidden />
            <h1>推荐历史</h1>
          </div>
          <p className="history-sub">
            {total > 0 ? `共 ${total} 次推荐 · 登录态下生成的推荐会自动记录` : '登录后做推荐会自动保存'}
          </p>
        </div>
        <div className="history-top-actions">
          <Link to="/recommend" className="btn btn-ghost btn-sm" aria-label="去做新推荐">
            <PlusCircle size={16} aria-hidden />
            <span>新推荐</span>
          </Link>
          <Link to="/settings" className="btn btn-ghost btn-sm" aria-label="账户设置">
            <Settings size={16} aria-hidden />
          </Link>
          <button
            type="button"
            className="btn btn-secondary btn-sm"
            onClick={() => {
              track('history.wipe_all_click', { total_records: total });
              setShowConfirmWipe(true);
            }}
            title="一键归零：清空画像 + 推荐历史 + 问卷草稿 + 横幅隐藏记忆"
            style={{
              borderColor: 'color-mix(in oklab, #e67e22 55%, var(--color-border))',
              background: 'color-mix(in oklab, #e67e22 12%, var(--color-surface))',
              color: 'color-mix(in oklab, #c0392b 70%, var(--color-text))',
            }}
          >
            <RotateCcw size={16} aria-hidden />
            <span>🔄 重新从零开始</span>
          </button>
          {records.length > 0 ? (
            <button
              type="button"
              className="btn btn-danger-outline btn-sm"
              onClick={() => setShowConfirmClear(true)}
              disabled={clearing}
              aria-label="清空全部历史"
            >
              <Trash2 size={16} aria-hidden />
              <span>仅清空历史</span>
            </button>
          ) : null}
        </div>
      </div>

      {error ? (
        <div className="toast-error" role="alert">
          {error}
        </div>
      ) : null}

      {initialLoading ? (
        <div className="history-empty" aria-busy="true">
          <p>加载中…</p>
        </div>
      ) : listItems.length === 0 ? (
        <div className="history-empty">
          <Search size={48} className="history-empty-icon" aria-hidden />
          <h2>还没有推荐记录</h2>
          <p>
            登录后生成的每一次推荐都会保存在这里，下次可以直接点卡片回顾 5 道菜。
          </p>
          <Link to="/recommend" className="btn btn-primary">
            开始做推荐
          </Link>
        </div>
      ) : (
        <>
          <ul className="history-list" role="list">
            {listItems.map((r) => {
              const snapItems = Array.isArray(r.recommendation_snapshot?.items)
                ? r.recommendation_snapshot.items
                : [];
              const top3 = snapItems.slice(0, 3);
              const confirmOpen = confirmDeleteId === r.id;
              return (
                <li key={r.id} className="history-card">
                  <div className="history-card-head">
                    <div className="history-card-meta">
                      <time className="history-card-time" dateTime={r.created_at}>
                        {formatTime(r.created_at)}
                      </time>
                      {r.result_count > 0 ? (
                        <span className="history-card-count">{r.result_count} 道菜</span>
                      ) : null}
                      {(r.final_reason ?? undefined) !== undefined ? (
                        (() => {
                          const meta = describeFinalReason(r.final_reason);
                          return (
                            <span
                              className={`source-badge source-badge--${meta.variant}`}
                              role="note"
                              aria-label={meta.accessibleLabel}
                            >
                              {meta.label}
                            </span>
                          );
                        })()
                      ) : null}
                    </div>
                    <button
                      type="button"
                      className="btn btn-ghost btn-icon btn-sm"
                      onClick={() => setConfirmDeleteId(confirmOpen ? null : r.id)}
                      disabled={actionInFlightId === r.id}
                      aria-label={`删除 ${formatTime(r.created_at)} 的推荐记录`}
                    >
                      <Trash2 size={16} aria-hidden />
                    </button>
                  </div>

                  {tagsText(r) ? <p className="history-card-tags">{tagsText(r)}</p> : null}

                  {top3.length > 0 ? (
                    <ol className="history-card-items" role="list">
                      {top3.map((it, i) => (
                        <li key={i} className="history-card-item">
                          <span className="history-card-item-priority">{(it as { priority?: number }).priority ?? i + 1}</span>
                          <span className="history-card-item-name">{displayFoodName(it as { food_code: string; food_name_zh?: string | null })}</span>
                        </li>
                      ))}
                      {snapItems.length > 3 ? (
                        <li className="history-card-item history-card-more">
                          还有 {snapItems.length - 3} 道…
                        </li>
                      ) : null}
                    </ol>
                  ) : (
                    <p className="history-card-empty">本次快照未保存菜单项</p>
                  )}

                  {confirmOpen ? (
                    <div className="history-card-confirm" role="alertdialog" aria-label="确认删除">
                      <p>删除后不可恢复，确定移除这条记录？</p>
                      <div className="history-card-confirm-actions">
                        <button
                          type="button"
                          className="btn btn-ghost btn-sm"
                          onClick={() => setConfirmDeleteId(null)}
                        >
                          取消
                        </button>
                        <button
                          type="button"
                          className="btn btn-danger btn-sm"
                          onClick={() => onDeleteOne(r.id)}
                          disabled={actionInFlightId === r.id}
                        >
                          {actionInFlightId === r.id ? '删除中…' : '确认删除'}
                        </button>
                      </div>
                    </div>
                  ) : null}
                </li>
              );
            })}
          </ul>

          <div className="history-footer">
            {hasMore ? (
              <button
                type="button"
                className="btn btn-ghost btn-block"
                onClick={loadMore}
                disabled={loading}
              >
                <RotateCcw size={16} className={loading ? 'spin' : ''} aria-hidden />
                {loading ? '加载中…' : '加载更多'}
              </button>
            ) : (
              <p className="history-footer-text">已经到底啦，一共 {total} 条</p>
            )}
          </div>
        </>
      )}

      {showConfirmClear ? (
        <div className="modal-backdrop" role="dialog" aria-modal="true" aria-label="清空确认">
          <div className="modal-card">
            <h3>仅清空推荐历史？</h3>
            <p>
              只删除 <strong>{total}</strong> 条推荐记录，<strong>画像快照、问卷草稿、横幅隐藏记忆保留</strong>。
              如果你希望"下一次推荐从头答完整问卷"，请用 <strong>🔄 重新从零开始</strong>。
            </p>
            <div className="modal-actions">
              <button
                type="button"
                className="btn btn-ghost"
                onClick={() => setShowConfirmClear(false)}
                disabled={clearing}
              >
                取消
              </button>
              <button
                type="button"
                className="btn btn-danger"
                onClick={onClearAll}
                disabled={clearing}
              >
                {clearing ? '清空中…' : '确认只清空历史'}
              </button>
            </div>
          </div>
        </div>
      ) : null}

      {showConfirmWipe ? (
        <div className="modal-backdrop" role="dialog" aria-modal="true" aria-label="一键归零确认">
          <div className="modal-card">
            <div className="modal-head" style={{ display: 'flex', alignItems: 'flex-start', gap: 'var(--space-2)', marginBottom: 'var(--space-2)' }}>
              <AlertTriangle size={20} style={{ color: '#e67e22', flexShrink: 0, marginTop: '4px' }} aria-hidden />
              <div style={{ flex: 1, minWidth: 0 }}>
                <h3 style={{ margin: 0 }}>确认要把你的饮食偏好"一键归零"？</h3>
              </div>
            </div>
            <p style={{ marginTop: 0 }}>
              这会同时做 <strong>4</strong> 件事，<strong>全部删除后不可恢复</strong>：
            </p>
            <ol style={{ paddingLeft: '20px', lineHeight: 1.8, margin: '6px 0 var(--space-2)' }}>
              <li>删除 <strong>全部画像快照</strong>（七维偏好画像全部清除）</li>
              <li>删除 <strong>全部推荐历史</strong>（当前共 {total} 条）</li>
              <li>清空 <strong>问卷草稿</strong>（下次进推荐会从头答完整问卷）</li>
              <li>清空 <strong>活动横幅今日隐藏记忆</strong>（首页活动会重新出现）</li>
            </ol>
            <div className="modal-actions">
              <button
                type="button"
                className="btn btn-ghost"
                onClick={() => {
                  track('history.wipe_all_cancel', { total_records: total });
                  setShowConfirmWipe(false);
                }}
                disabled={wiping}
              >
                取消
              </button>
              <button
                type="button"
                className="btn btn-danger"
                onClick={onWipeAll}
                disabled={wiping}
              >
                {wiping ? '正在归零…' : '是的，重新从零开始'}
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
