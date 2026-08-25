/**
 * 活动横幅（首页 Hero 顶部 / 其它入口可复用）。
 *
 * MVP 内置 2 条活动：
 *   A. 🎉 每日打卡挑战：连续 7 天分享餐食 → 送 20 次 AI 推荐额度
 *   B. 🍱 本周主题 PK：日料 vs 韩餐，点进去社区页投票
 *
 * 关闭记忆（UX 改造：2 步确认，只隐藏 24h）：
 *   1. 用户点 × 按钮 → 原横幅位置替换为一条确认条（不写入 localStorage）。
 *   2. 用户在确认条里点「今天不再显示」→ localStorage 记该横幅的「截止戳」= 现在 + 24h，
 *      到期自动再次出现（避免「关一次 7 天不见」太长）。
 *   3. 用户在确认条里点「取消」→ 恢复显示原横幅（什么都不写）。
 */

import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router';

const DAY_MS = 24 * 60 * 60 * 1000;
// 关闭窗口 = 24 小时（不是之前的 7 天；7 天太长，按用户反馈改短）
const HIDE_WINDOW_MS = DAY_MS;

const KEY_PREFIX = 'eatwhat:campaign:hidden-until-ms:';

export interface CampaignItem {
  readonly id: string;
  readonly icon: string;
  readonly title: string;
  readonly description: string;
  readonly ctaLabel: string;
  readonly onClick: () => void;
  /** CSS 样式：给不同活动不同渐变，避免视觉单调 */
  readonly style: React.CSSProperties;
}

interface CampaignBannerProps {
  /** 可选：外部传入活动列表；默认使用内置 2 条 */
  readonly campaigns?: readonly CampaignItem[];
  /** 容器 className，便于在不同页面做微调 */
  readonly className?: string;
}

export default function CampaignBanner({ campaigns, className }: CampaignBannerProps) {
  const nav = useNavigate();

  const defaultCampaigns = useMemo<CampaignItem[]>(
    () => [
      {
        id: 'daily_checkin_ai_quota',
        icon: '🎉',
        title: '每日打卡挑战',
        description: '连续 7 天分享餐食，送 20 次 AI 推荐额度。',
        ctaLabel: '去社区参与 →',
        onClick: () => nav('/community'),
        style: {
          background:
            'linear-gradient(135deg, color-mix(in oklab, #7c5cff 14%, var(--color-surface)), color-mix(in oklab, #ff7aa2 12%, var(--color-surface)))',
          borderColor: 'color-mix(in oklab, #7c5cff 40%, var(--color-border))',
        },
      },
      {
        id: 'weekly_theme_pk_w33',
        icon: '🍱',
        title: '本周主题：日料 vs 韩餐 PK',
        description: '投票你的心头好，周末看榜单结果。',
        ctaLabel: '去投票 →',
        onClick: () => nav('/community#theme'),
        style: {
          background:
            'linear-gradient(135deg, color-mix(in oklab, #3d6fe0 14%, var(--color-surface)), color-mix(in oklab, #13c2a7 12%, var(--color-surface)))',
          borderColor: 'color-mix(in oklab, #3d6fe0 40%, var(--color-border))',
        },
      },
    ],
    [nav],
  );

  const list = campaigns ?? defaultCampaigns;

  // 先按 localStorage 的「24h 隐藏截止戳」过滤掉仍在隐藏窗口内的横幅
  const hiddenUntilByCampaign = useMemo(() => {
    const map = new Map<string, number>();
    if (typeof window === 'undefined') return map;
    for (const c of list) {
      const raw = window.localStorage.getItem(`${KEY_PREFIX}${c.id}`);
      const ms = raw ? Number(raw) : NaN;
      if (Number.isFinite(ms)) map.set(c.id, ms);
    }
    return map;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [list]);

  const now = Date.now();
  const defaultVisible = useMemo(() => {
    const s = new Set<string>();
    for (const c of list) {
      const until = hiddenUntilByCampaign.get(c.id);
      if (until === undefined || until <= now) s.add(c.id);
    }
    return s;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [list]);

  // visible: 当前渲染显示的横幅 id（受 2 步确认的「临时关闭」也会从这里临时剔除）
  const [visible, setVisible] = useState<Set<string>>(() => defaultVisible);
  // confirm: 按了 × 后，当前正在"显示确认条"的横幅 id
  const [confirmingId, setConfirmingId] = useState<string | null>(null);

  // 如果 campaigns 引用变了，同步刷新展示状态（一般不会变，但保持 idempotent）
  useEffect(() => {
    setVisible(defaultVisible);
    setConfirmingId((cur) => (list.some((c) => c.id === cur) ? cur : null));
  }, [defaultVisible, list]);

  // Step 1：用户按了 × → 先显示确认条（不写 localStorage；横幅临时隐藏）
  const handleRequestDismiss = useCallback((id: string) => {
    setConfirmingId(id);
    setVisible((prev) => {
      const next = new Set(prev);
      next.delete(id);
      return next;
    });
  }, []);

  // Step 2a：用户点「今天不再显示」→ 写 24h 截止戳
  const handleConfirmDismissToday = useCallback((id: string) => {
    try {
      window.localStorage.setItem(
        `${KEY_PREFIX}${id}`,
        String(Date.now() + HIDE_WINDOW_MS),
      );
    } catch {
      /* localStorage 被禁用时，只做会话级隐藏（刷新后再出现） */
    }
    setConfirmingId((cur) => (cur === id ? null : cur));
    // 注意：不再恢复到 visible（相当于今天都隐藏了）
  }, []);

  // Step 2b：用户点「取消」→ 什么都不写，恢复显示原横幅
  const handleCancelDismiss = useCallback((id: string) => {
    setConfirmingId((cur) => (cur === id ? null : cur));
    setVisible((prev) => {
      const next = new Set(prev);
      next.add(id);
      return next;
    });
  }, []);

  const visibleItems = list.filter((c) => visible.has(c.id));
  const confirmingItem = confirmingId ? list.find((c) => c.id === confirmingId) ?? null : null;

  if (visibleItems.length === 0 && !confirmingItem) return null;

  const wrapperClass = `campaign-banner-stack${className ? ` ${className}` : ''}`;

  return (
    <div className={wrapperClass} aria-label="活动横幅">
      {visibleItems.map((c) => (
        <div
          key={c.id}
          className="campaign-banner"
          role="region"
          style={{
            marginTop: 'var(--space-2)',
            marginBottom: 'var(--space-2)',
            padding: 'var(--space-3) var(--space-4)',
            borderRadius: 'var(--radius-md)',
            border: '1px solid',
            display: 'flex',
            gap: 'var(--space-3)',
            alignItems: 'center',
            ...c.style,
          }}
        >
          <span aria-hidden style={{ fontSize: '1.6rem', lineHeight: 1, flexShrink: 0 }}>
            {c.icon}
          </span>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontWeight: 600, marginBottom: '2px' }}>{c.title}</div>
            <div style={{ fontSize: '0.9rem', opacity: 0.9 }}>{c.description}</div>
          </div>
          <button
            type="button"
            className="button button-primary"
            onClick={c.onClick}
            style={{ margin: 0, flexShrink: 0 }}
          >
            {c.ctaLabel}
          </button>
          <button
            type="button"
            className="campaign-banner__close"
            onClick={() => handleRequestDismiss(c.id)}
            aria-label={`询问是否今日不再显示「${c.title}」活动横幅`}
            title="关闭（会再确认一次）"
            style={{
              border: 'none',
              background: 'transparent',
              cursor: 'pointer',
              opacity: 0.65,
              padding: 'var(--space-1)',
              borderRadius: '50%',
              lineHeight: 1,
              fontSize: '1.1rem',
              flexShrink: 0,
            }}
          >
            ×
          </button>
        </div>
      ))}

      {confirmingItem ? (
        <div
          role="status"
          aria-live="polite"
          style={{
            marginTop: 'var(--space-2)',
            marginBottom: 'var(--space-2)',
            padding: 'var(--space-3) var(--space-4)',
            borderRadius: 'var(--radius-md)',
            border: '1px solid color-mix(in oklab, var(--color-primary) 30%, var(--color-border))',
            background: 'color-mix(in oklab, var(--color-primary) 8%, var(--color-surface))',
            display: 'flex',
            gap: 'var(--space-3)',
            alignItems: 'center',
            flexWrap: 'wrap',
          }}
        >
          <span style={{ fontSize: '1.15rem' }} aria-hidden>❓</span>
          <div style={{ flex: 1, minWidth: 220 }}>
            <div style={{ fontWeight: 600 }}>
              今天内不再显示「{confirmingItem.title}」这条活动？
            </div>
            <div style={{ fontSize: '0.85rem', opacity: 0.8, marginTop: '2px' }}>
              选"是"的话 24 小时内不会再次弹出；到期后第二天会自动重新出现。
            </div>
          </div>
          <div style={{ display: 'flex', gap: 'var(--space-2)' }}>
            <button
              type="button"
              className="button button-secondary"
              onClick={() => handleCancelDismiss(confirmingItem.id)}
              style={{ margin: 0 }}
            >
              否，继续显示
            </button>
            <button
              type="button"
              className="button button-primary"
              onClick={() => handleConfirmDismissToday(confirmingItem.id)}
              style={{ margin: 0 }}
            >
              是，今天不再显示
            </button>
          </div>
        </div>
      ) : null}
    </div>
  );
}
