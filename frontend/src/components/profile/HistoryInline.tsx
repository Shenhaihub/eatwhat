/**P7-03：Settings 内嵌入式推荐历史卡片（精简版，复用 history-list/card 样式）。
 *
 * 完整版去 /history；这里只取最新 10 条用于"我的" Tab 内。
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import { History as HistoryIcon, PlusCircle, RotateCcw, Search, Trash2 } from 'lucide-react';
import { Link } from 'react-router';
import type { HistoryRecord } from '../../services/api/types';
import type { RecommendationItem } from '../../services/api/types/food';
import { api } from '../../services/api/client';
import { describeFinalReason } from '../../lib/sourceBadge';

const EMBED_LIMIT = 10;

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
    return r.food_code.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
  }
  return '未命名菜品';
}

export default function HistoryInline() {
  const [records, setRecords] = useState<HistoryRecord[]>([]);
  const [total, setTotal] = useState(0);
  const [, setLoading] = useState(false);
  const [initialLoading, setInitialLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actionId, setActionId] = useState<string | null>(null);
  const [clearing, setClearing] = useState(false);
  const [showClear, setShowClear] = useState(false);
  const [confirmId, setConfirmId] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await api.historyList({ limit: EMBED_LIMIT, offset: 0 });
      setRecords(res.items);
      setTotal(res.total);
    } catch (e) {
      setError(e instanceof Error ? e.message : '加载失败');
    } finally {
      setLoading(false);
      setInitialLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const onDeleteOne = async (id: string) => {
    setActionId(id);
    try {
      await api.historyDelete(id);
      setRecords((prev) => prev.filter((r) => r.id !== id));
      setTotal((t) => Math.max(0, t - 1));
      setConfirmId(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : '删除失败');
    } finally {
      setActionId(null);
    }
  };

  const onClear = async () => {
    setClearing(true);
    try {
      const res = await api.historyDeleteAll();
      setRecords([]);
      setTotal(0);
      setShowClear(false);
      setError(`已清空 ${res.deleted} 条记录`);
      setTimeout(() => setError(null), 2000);
    } catch (e) {
      setError(e instanceof Error ? e.message : '清空失败');
    } finally {
      setClearing(false);
    }
  };

  const tagsText = (r: HistoryRecord): string => {
    const parts: string[] = [];
    if (r.food_code) parts.push(r.food_code.replace(/_/g, ' '));
    if (r.tags?.length) parts.push(...r.tags.slice(0, 3));
    return parts.join(' · ');
  };

  const listItems = useMemo(() => records, [records]);

  return (
    <div className="pref-section">
      <div className="pref-card-head">
        <div>
          <h2 className="pref-card-title">
            <HistoryIcon size={16} aria-hidden />
            推荐历史
          </h2>
          <p className="pref-card-sub">
            {total > 0 ? `共 ${total} 次推荐（此处仅显示最近 ${EMBED_LIMIT} 条）` : '登录后做推荐会自动保存'}
          </p>
        </div>
        <div className="pref-card-actions">
          <Link to="/recommend" className="btn btn-ghost btn-sm">
            <PlusCircle size={14} aria-hidden />
            <span>新推荐</span>
          </Link>
          <Link to="/history" className="btn btn-outline btn-sm">
            <HistoryIcon size={14} aria-hidden />
            <span>完整历史</span>
          </Link>
          {total > 0 ? (
            <button
              type="button"
              className="btn btn-danger-outline btn-sm"
              onClick={() => setShowClear(true)}
              disabled={clearing}
            >
              <Trash2 size={14} aria-hidden />
              <span>清空</span>
            </button>
          ) : null}
        </div>
      </div>

      {error ? <div className="toast-error" role="alert">{error}</div> : null}

      {initialLoading ? (
        <div className="history-empty" aria-busy="true">
          <p>加载中…</p>
        </div>
      ) : listItems.length === 0 ? (
        <div className="history-empty">
          <Search size={40} className="history-empty-icon" aria-hidden />
          <h2>还没有推荐记录</h2>
          <p>登录后生成的每一次推荐都会保存在这里，下次可以直接回顾 5 道菜。</p>
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
              const open = confirmId === r.id;
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
                      {r.final_reason != null ? (
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
                      onClick={() => setConfirmId(open ? null : r.id)}
                      disabled={actionId === r.id}
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
                          <span className="history-card-item-priority">
                            {(it as { priority?: number }).priority ?? i + 1}
                          </span>
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
                  {open ? (
                    <div className="history-card-confirm" role="alertdialog" aria-label="确认删除">
                      <p>删除后不可恢复，确定移除这条记录？</p>
                      <div className="history-card-confirm-actions">
                        <button
                          type="button"
                          className="btn btn-ghost btn-sm"
                          onClick={() => setConfirmId(null)}
                        >
                          取消
                        </button>
                        <button
                          type="button"
                          className="btn btn-danger btn-sm"
                          onClick={() => onDeleteOne(r.id)}
                          disabled={actionId === r.id}
                        >
                          {actionId === r.id ? '删除中…' : '确认删除'}
                        </button>
                      </div>
                    </div>
                  ) : null}
                </li>
              );
            })}
          </ul>
          <div className="history-footer">
            {total > EMBED_LIMIT ? (
              <Link to="/history" className="btn btn-ghost btn-block">
                <RotateCcw size={16} aria-hidden />
                <span>查看全部 {total} 条</span>
              </Link>
            ) : (
              <p className="history-footer-text">已经到底啦，一共 {total} 条</p>
            )}
          </div>
        </>
      )}

      {showClear ? (
        <div className="modal-backdrop" role="dialog" aria-modal="true" aria-label="清空确认">
          <div className="modal-card">
            <h3>清空全部历史？</h3>
            <p>
              所有推荐记录将被永久移除，且无法恢复。偏好画像数据不会受影响（需在
              <Link to="/settings" onClick={() => setShowClear(false)}>饮食偏好</Link>里单独清空）。
            </p>
            <div className="modal-actions">
              <button
                type="button"
                className="btn btn-ghost"
                onClick={() => setShowClear(false)}
                disabled={clearing}
              >
                取消
              </button>
              <button
                type="button"
                className="btn btn-danger"
                onClick={() => onClear()}
                disabled={clearing}
              >
                <Trash2 size={16} aria-hidden />
                <span>{clearing ? '清空中…' : '确认清空'}</span>
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
