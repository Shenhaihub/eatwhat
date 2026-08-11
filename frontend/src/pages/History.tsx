import { useCallback, useEffect, useMemo, useState } from 'react';
import { History as HistoryIcon, PlusCircle, RotateCcw, Search, Settings, Trash2 } from 'lucide-react';
import { Link } from 'react-router';
import { api } from '../services/api/client';
import type { HistoryRecord } from '../services/api/types';
import type { RecommendationItem } from '../services/api/types/food';
import { describeFinalReason } from '../lib/sourceBadge';

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

function displayFoodName(item: unknown): string {
  const r = item as Partial<RecommendationItem> & { food_name_zh?: string | null; food_display?: string | null };
  if (r.food_name_zh) return r.food_name_zh;
  if (r.food_display) return r.food_display;
  if (r.food_code) {
    const pretty = r.food_code.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
    return pretty;
  }
  return '未命名菜品';
}

export default function History() {
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
      setError(`已清空 ${res.deleted} 条记录`);
      setTimeout(() => setError(null), 2000);
    } catch (e) {
      const msg = e instanceof Error ? e.message : '清空失败';
      setError(msg);
    } finally {
      setClearing(false);
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
    if (r.food_code) parts.push(r.food_code.replace(/_/g, ' '));
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
          {records.length > 0 ? (
            <button
              type="button"
              className="btn btn-danger-outline btn-sm"
              onClick={() => setShowConfirmClear(true)}
              disabled={clearing}
              aria-label="清空全部历史"
            >
              <Trash2 size={16} aria-hidden />
              <span>清空</span>
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
                          <span className="history-card-item-name">{displayFoodName(it)}</span>
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
            <h3>清空全部历史？</h3>
            <p>
              将删除 <strong>{total}</strong> 条推荐记录。此操作不可撤销，你确定要继续吗？
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
                {clearing ? '清空中…' : '确认清空全部'}
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
