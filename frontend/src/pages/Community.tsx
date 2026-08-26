/**
 * B 阶段 MVP：社区页 /community
 *
 * 区块从上到下（PC 两栏布局，窄屏单栏 + Top 榜插到主题下方）：
 *   1. 本周主题横幅（id=theme，承接首页 CTA 跳锚点）：标题/倒计时/jp/kr 两栏投票
 *   2. 主内容：2 个 tab「🔥 最热」 /  ⏰ 最新 + 动态 Feed 卡片列表（点赞/菜系 tag/头像 emoji）
 *   3. 右侧栏：今日推荐 Top 榜（Top 5，点击「去吃 →」跳到 /nearby）
 *   4. 右下悬浮：「分享今天吃了啥」按钮 → 半屏弹层占位（P3 再做）
 *
 * 登录态处理：
 *   - 主题投票 / 点赞 / 分享按钮：未登录 → 点了跳 /login?return_to=/community
 *   - Feed liked_by_me：匿名直接全 false；登录后端填充
 */

import { useCallback, useEffect, useMemo, useState } from 'react';
import { useLocation, useNavigate } from 'react-router';

import { api, ApiError } from '../services/api/client';
import { displayFoodName } from '../lib/foodNames';
import type {
  CommunityFeedItem,
  CommunityFeedSort,
  CommunityThemeResponse,
  CommunityThemeOption,
  CommunityTrendingItem,
} from '../services/api/types';
import { useAuth } from '../context/AuthContext';
import { track } from '../lib/track';

const FEED_TABS: ReadonlyArray<{ id: CommunityFeedSort; label: string }> = [
  { id: 'hot', label: '🔥 最热' },
  { id: 'latest', label: '⏰ 最新' },
];

// Feed 的 cuisine_tag 是中文（如"日料"/"韩餐"），但推荐页 q07_cuisine_preference 只能识别
// 后端 CuisineGroup 枚举值（japanese/korean 等）。这里做中文 tag → q07 key 的映射，
// 让 Feed「用这个去做推荐」也能把菜系预填成后端可解析的值，避免 follow_up 重复问菜系。
const CUISINE_TAG_TO_Q07_KEY: Readonly<Record<string, string>> = {
  日料: 'japanese',
  日式: 'japanese',
  韩餐: 'korean',
  韩式: 'korean',
  西餐: 'western',
  西式: 'western',
  中餐: 'chinese_staple',
  家常菜: 'chinese_staple',
  面食: 'noodle',
  粉面: 'noodle',
  火锅: 'hotpot',
  麻辣烫: 'hotpot',
  砂锅: 'hotpot',
};

function relativeTime(iso: string): string {
  const t = new Date(iso).getTime();
  if (!Number.isFinite(t)) return '';
  const diffMs = Date.now() - t;
  const m = Math.floor(diffMs / 60000);
  if (m < 1) return '刚刚';
  if (m < 60) return `${m} 分钟前`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h} 小时前`;
  const d = Math.floor(h / 24);
  return `${d} 天前`;
}

export default function Community() {
  const nav = useNavigate();
  const location = useLocation();
  const { isAuthenticated } = useAuth();
  const isLoggedIn = isAuthenticated;

  // ---------- Theme ----------
  const [theme, setTheme] = useState<CommunityThemeResponse | null>(null);
  const [themeError, setThemeError] = useState<string | null>(null);
  const [voting, setVoting] = useState<boolean>(false);
  const [voteHint, setVoteHint] = useState<{ kind: 'ok' | 'err' | 'info'; text: string } | null>(null);

  // 锚点滚动：/community#theme 跳到主题横幅（因为首屏就展示，一般不用滚；兼容）
  useEffect(() => {
    if (location.hash !== '#theme') return;
    const el = document.getElementById('community-theme');
    if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }, [location.hash]);

  // C：本周主题卡曝光（theme 拉到之后，对每张 option 打一次 impression）。
  // useEffect 必须放在组件顶层，不能放到 map 回调里。
  useEffect(() => {
    if (!themeOptions.length) return;
    for (const o of themeOptions) {
      track('community.theme_card.impression', { theme_key: o.key, label: o.label });
    }
    // 只在 theme 第一次落地、或换主题（theme_id 变更）时打
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [theme?.theme_id]);

  const requireLoginThen = useCallback(
    (andThen: () => void) => {
      if (!isLoggedIn) {
        nav('/login', { state: { return_to: '/community' } });
        return;
      }
      andThen();
    },
    [isLoggedIn, nav],
  );

  const fetchTheme = useCallback(async () => {
    try {
      const data = await api.communityTheme();
      setTheme(data);
      setThemeError(null);
    } catch (e) {
      const msg = e instanceof ApiError ? e.message : '加载主题失败';
      setThemeError(msg);
    }
  }, []);

  useEffect(() => {
    void fetchTheme();
  }, [fetchTheme]);

  // C：Feed 卡片曝光埋点 - 错误位置：放到这里（feed / tab 还没声明）。
  // 已在 fetchFeed 之后的 200 行附近重新声明正确版本。先占位删掉代码，避免重复打 + TDZ 报错。

  const endsAtText = useMemo(() => {
    if (!theme) return '';
    const d = new Date(theme.ends_at);
    // 转北京时间（UTC+8）做展示
    const bj = new Date(d.getTime() + 8 * 3600 * 1000);
    const pad = (n: number) => String(n).padStart(2, '0');
    return `${bj.getUTCFullYear()}-${pad(bj.getUTCMonth() + 1)}-${pad(bj.getUTCDate())} ${pad(bj.getUTCHours())}:${pad(bj.getUTCMinutes())} (北京时间)`;
  }, [theme]);

  const handleVote = useCallback(
    async (key: string) => {
      if (!isLoggedIn) {
        nav('/login', { state: { return_to: '/community#theme' } });
        return;
      }
      if (voting) return;
      setVoting(true);
      setVoteHint(null);
      try {
        const res = await api.communityThemeVote({ option_key: key });
        // 写回最新 options / voted_key
        setTheme((prev) =>
          prev ? { ...prev, voted_key: res.voted_key, options: res.options } : prev,
        );
        setVoteHint(
          res.duplicated
            ? { kind: 'info', text: '你已经投过这个选项啦～' }
            : { kind: 'ok', text: '投票成功，感谢参与 🎉' },
        );
      } catch (e) {
        const code = e instanceof ApiError ? e.code : null;
        let text = '投票失败，请稍后重试';
        if (code === 'ALREADY_VOTED_OTHER') text = '你本周已投过其他选项啦，不能改票哦';
        else if (e instanceof ApiError && e.message) text = e.message;
        setVoteHint({ kind: 'err', text });
      } finally {
        setVoting(false);
        // 提示 4 秒自动清
        window.setTimeout(() => setVoteHint((cur) => (cur ? null : cur)), 4000);
      }
    },
    [isLoggedIn, nav, voting],
  );

  // ---------- Feed ----------
  const [tab, setTab] = useState<CommunityFeedSort>('latest');
  const [feedItems, setFeedItems] = useState<CommunityFeedItem[] | null>(null);
  const [feedError, setFeedError] = useState<string | null>(null);
  const [feedLoading, setFeedLoading] = useState<boolean>(false);
  const [likedBusy, setLikedBusy] = useState<Set<string>>(new Set());

  const fetchFeed = useCallback(async (sort: CommunityFeedSort) => {
    setFeedLoading(true);
    setFeedError(null);
    try {
      const res = await api.communityFeed({ sort });
      setFeedItems(res.items as CommunityFeedItem[]);
    } catch (e) {
      const msg = e instanceof ApiError ? e.message : '加载动态失败';
      setFeedError(msg);
    } finally {
      setFeedLoading(false);
    }
  }, []);

  useEffect(() => {
    void fetchFeed(tab);
  }, [tab, fetchFeed]);

  // C：Feed 卡片曝光埋点。每次 feed 列表落地（或 tab 切换）时对所有卡片各打一次 impression。
  // 注意：useEffect 必须在 feedItems / tab 都声明之后，这里才是正确位置；
  //       使用 feedItems.id 数组做去重键：点赞数更新不重打，切 tab / feed 内容改变才重新打。
  useEffect(() => {
    if (!feedItems?.length) return;
    for (const it of feedItems) {
      track('community.feed.impression', {
        feed_id: it.id,
        food_code: it.food_code,
        cuisine_tag: it.cuisine_tag,
        sort: tab,
      });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [feedItems?.map((f) => f.id).join(','), tab]);

  const handleLike = useCallback(
    async (item: CommunityFeedItem) => {
      if (!isLoggedIn) {
        nav('/login', { state: { return_to: '/community' } });
        return;
      }
      if (likedBusy.has(item.id)) return;
      setLikedBusy((prev) => {
        const next = new Set(prev);
        next.add(item.id);
        return next;
      });
      try {
        const res = await api.communityFeedLike(item.id);
        // 前端乐观更新
        setFeedItems((prev) =>
          prev
            ? prev.map((x) =>
                x.id === item.id ? { ...x, liked_by_me: res.liked, likes: res.likes } : x,
              )
            : prev,
        );
        if (res.duplicated) {
          // 不特别提示；避免刷屏
        }
      } catch (e) {
        const msg = e instanceof ApiError ? e.message : '点赞失败';
        setFeedError(msg);
      } finally {
        setLikedBusy((prev) => {
          const next = new Set(prev);
          next.delete(item.id);
          return next;
        });
      }
    },
    [isLoggedIn, likedBusy, nav],
  );

  // ---------- Trending ----------
  const [trending, setTrending] = useState<CommunityTrendingItem[] | null>(null);
  const [trendingMeta, setTrendingMeta] = useState<{ is_example: boolean; data_source: string } | null>(null);
  const [trendingError, setTrendingError] = useState<string | null>(null);

  const fetchTrending = useCallback(async () => {
    try {
      const res = await api.communityTrending();
      setTrending(res.items as CommunityTrendingItem[]);
      setTrendingMeta({ is_example: res.is_example, data_source: res.data_source });
    } catch (e) {
      const msg = e instanceof ApiError ? e.message : '加载榜单失败';
      setTrendingError(msg);
    }
  }, []);

  useEffect(() => {
    void fetchTrending();
  }, [fetchTrending]);

  // ---------- Share (P3 placeholder) ----------
  const [showShare, setShowShare] = useState<boolean>(false);

  // ---------- Render helpers ----------
  const themeOptions: readonly CommunityThemeOption[] = theme?.options ?? [];
  const totalVotes = themeOptions.reduce((s, o) => s + o.votes, 0);

  return (
    <div className="page-shell community-page">
      {/* ======== 区块 1：本周主题 ======== */}
      {/* UX 修复：主题不占满首屏，让 Feed 在首屏就能看到；
           同时支持 CSS 宽屏两栏（grid-template-columns 断点在全局样式）。 */}
      <section id="community-theme" className="community-theme" aria-label="本周主题">
        <div
          style={{
            borderRadius: 'var(--radius-md)',
            border: '1px solid color-mix(in oklab, var(--color-primary) 35%, var(--color-border))',
            background:
              'linear-gradient(135deg, color-mix(in oklab, #3d6fe0 12%, var(--color-surface)), color-mix(in oklab, #13c2a7 10%, var(--color-surface)))',
            padding: 'var(--space-3) var(--space-3)',
            marginBottom: 'var(--space-3)',
            // 限制主题区最高高度，避免首屏被占满（导致用户觉得"进了主题页"看不到 Feed）
            maxHeight: 'min(46vh, 420px)',
            overflow: 'auto',
          }}
        >
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 'var(--space-3)', flexWrap: 'wrap' }}>
            <div>
              <div style={{ fontSize: '0.85rem', opacity: 0.85, marginBottom: '4px' }}>
                🗓 本周主题
              </div>
              <h2 style={{ margin: 0, fontSize: '1.25rem' }}>{theme?.title ?? '加载中…'}</h2>
              {theme?.subtitle ? (
                <p style={{ margin: '6px 0 0', opacity: 0.9, fontSize: '0.9rem' }}>{theme.subtitle}</p>
              ) : null}
              {endsAtText ? (
                <p style={{ margin: '6px 0 0', fontSize: '0.8rem', opacity: 0.8 }}>
                  截止：{endsAtText} · 已参与 {totalVotes.toLocaleString()} 人
                </p>
              ) : null}
            </div>
          </div>

          {themeError ? (
            <div role="alert" className="autowrite-banner" style={{
              marginTop: 'var(--space-2)',
              border: '1px solid color-mix(in oklab, #c0392b 35%, var(--color-border))',
              background: 'color-mix(in oklab, #e74c3c 12%, var(--color-surface))',
              borderRadius: 'var(--radius-md)', padding: 'var(--space-2)'
            }}>
              {themeError}
            </div>
          ) : null}

          <div
            style={{
              display: 'grid',
              gridTemplateColumns: '1fr 1fr',
              gap: 'var(--space-2)',
              marginTop: 'var(--space-2)',
            }}
            className="community-theme-vote-grid"
          >
            {themeOptions.map((o) => {
              const voted = theme?.voted_key === o.key;
              const disabled = voting || (!!theme?.voted_key && !voted);
              // 主题选项 key（如 "jp"/"kr"）是展示层/投票用的，
              // 但推荐页 q07_cuisine_preference 只能识别后端 CuisineGroup 枚举值（japanese/korean 等）。
              // 这里把主题 key 归一化成后端菜系 key，否则 prefill_cuisine 传 "jp" 会被后端解析失败 → 菜系被判定未收集 → follow_up 又追问菜系。
              const THEME_KEY_TO_CUISINE: Record<string, string> = {
                jp: 'japanese',
                kr: 'korean',
              };
              const cuisineKey = THEME_KEY_TO_CUISINE[o.key] ?? o.key;
              // 从归一化后的菜系 key 反推一道代表性菜（作为 URL seed_food 传到推荐页预填「明确想吃」）；
              // 没命中映射时用"选项 key"兜底（服务端兼容自由文本）。
              const themeSeedMap: Record<string, string> = {
                japanese: 'sushi',
                korean: 'korean_stew',
                chinese: 'braised_pork_rice',
                western: 'pasta',
                southeast_asian: 'thai_curry',
                cantonese: 'wonton_noodle',
                sichuan: 'mapo_tofu',
                vegetarian: 'buddhist_vegetarian',
                spicy_hotpot: 'spicy_hotpot',
                barbecue: 'korean_bbq',
              };
              const seedFood = themeSeedMap[cuisineKey] ?? cuisineKey;
              return (
                <div
                  key={o.key}
                  role="button"
                  tabIndex={disabled ? -1 : 0}
                  aria-pressed={voted}
                  onKeyDown={(e) => {
                    if (disabled) return;
                    if (e.key === 'Enter' || e.key === ' ') {
                      e.preventDefault();
                      void handleVote(o.key);
                    }
                  }}
                  className="theme-vote-card"
                  onClick={() => void handleVote(o.key)}
                  style={{
                    textAlign: 'left',
                    padding: 'var(--space-2)',
                    borderRadius: 'var(--radius-md)',
                    border: `1px solid ${voted ? 'var(--color-primary)' : 'color-mix(in oklab, var(--color-primary) 25%, var(--color-border))'}`,
                    background: voted
                      ? 'color-mix(in oklab, var(--color-primary) 14%, var(--color-surface))'
                      : 'color-mix(in oklab, var(--color-surface) 92%, white)',
                    cursor: disabled ? 'not-allowed' : 'pointer',
                    position: 'relative',
                    opacity: disabled && !voted ? 0.75 : 1,
                    outline: 'none',
                  }}
                >
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: '6px' }}>
                    <span style={{ fontWeight: 600, fontSize: '0.95rem' }}>{o.label}</span>
                    <span style={{ fontSize: '0.8rem', opacity: 0.8 }}>{o.percent.toFixed(1)}%</span>
                  </div>
                  <div
                    role="progressbar"
                    aria-valuenow={Math.round(o.percent)}
                    aria-valuemin={0}
                    aria-valuemax={100}
                    style={{
                      height: 6,
                      background: 'color-mix(in oklab, var(--color-border) 70%, var(--color-surface))',
                      borderRadius: 999,
                      overflow: 'hidden',
                      marginBottom: '6px',
                    }}
                  >
                    <div
                      style={{
                        width: `${o.percent}%`,
                        height: '100%',
                        background: voted ? 'var(--color-primary)' : 'color-mix(in oklab, var(--color-primary) 75%, #13c2a7)',
                        borderRadius: 999,
                      }}
                    />
                  </div>
                  <div style={{ fontSize: '0.8rem', opacity: 0.85, marginBottom: '8px' }}>
                    {o.votes.toLocaleString()} 票 {voted ? ' · 你已投 ✅' : ''}
                  </div>
                  <button
                    type="button"
                    className="button button-secondary"
                    onClick={(e) => {
                      // 注意：外层 div 绑定了"投票"点击；这里阻止冒泡，避免同时又把票投了
                      e.stopPropagation();
                      // B：同时传 seed_food（代表菜→q02）+ prefill_cuisine（菜系主题→q07）
                      const url = `/recommend?seed_food=${encodeURIComponent(seedFood)}&prefill_cuisine=${encodeURIComponent(cuisineKey)}`;
                      track('community.theme_card.goto_recommend_click', {
                        theme_key: o.key,
                        label: o.label,
                        seed_food: seedFood,
                      });
                      nav(url);
                    }}
                    title={`跳推荐页，按「${o.label}」风格生成 Top5（会自动预填明确想吃为代表菜）`}
                    style={{ margin: 0, width: '100%', padding: '6px 8px', fontSize: '0.85rem' }}
                  >
                    🎯 就按「{o.label}」给我生成推荐
                  </button>
                </div>
              );
            })}
          </div>

          {voteHint ? (
            <div
              role="status"
              aria-live="polite"
              style={{
                marginTop: 'var(--space-2)',
                padding: '8px 12px',
                borderRadius: 'var(--radius-md)',
                fontSize: '0.85rem',
                background:
                  voteHint.kind === 'ok'
                    ? 'color-mix(in oklab, #13c2a7 14%, var(--color-surface))'
                    : voteHint.kind === 'err'
                      ? 'color-mix(in oklab, #e67e22 14%, var(--color-surface))'
                      : 'color-mix(in oklab, var(--color-primary) 12%, var(--color-surface))',
                border: `1px solid color-mix(in oklab, ${
                  voteHint.kind === 'ok' ? '#13c2a7' : voteHint.kind === 'err' ? '#e67e22' : 'var(--color-primary)'
                } 35%, var(--color-border))`,
              }}
            >
              {voteHint.text}
              {!isLoggedIn ? (
                <>
                  {' '}
                  <button
                    type="button"
                    className="button button-secondary"
                    onClick={() => nav('/login', { state: { return_to: '/community#theme' } })}
                    style={{ margin: 0, padding: '4px 10px', fontSize: '0.85rem' }}
                  >
                    去登录投票
                  </button>
                </>
              ) : null}
            </div>
          ) : null}
        </div>
      </section>

      {/* ======== 双栏布局：主 Feed + Top 榜 ========
           宽屏（>= 900px）两栏，窄屏单栏。保证"大家在吃"进页首屏必能看到 Feed。 */}
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'minmax(0, 1fr)',
          gap: 'var(--space-4)',
        }}
        className="community-grid"
      >
        {/* ======== 区块 2：Feed ======== */}
        <section aria-label="大家今天吃什么">
          <div
            role="tablist"
            aria-label="动态排序"
            style={{
              display: 'inline-flex',
              gap: 'var(--space-1)',
              padding: '4px',
              borderRadius: 999,
              background: 'color-mix(in oklab, var(--color-border) 45%, var(--color-surface))',
              marginBottom: 'var(--space-3)',
            }}
          >
            {FEED_TABS.map((t) => {
              const active = tab === t.id;
              return (
                <button
                  key={t.id}
                  role="tab"
                  aria-selected={active}
                  type="button"
                  onClick={() => setTab(t.id)}
                  disabled={feedLoading}
                  style={{
                    border: 'none',
                    background: active ? 'var(--color-surface)' : 'transparent',
                    borderRadius: 999,
                    padding: '6px 14px',
                    fontSize: '0.9rem',
                    fontWeight: active ? 600 : 500,
                    cursor: feedLoading ? 'not-allowed' : 'pointer',
                    boxShadow: active ? '0 1px 2px rgba(0,0,0,0.06)' : 'none',
                  }}
                >
                  {t.label}
                </button>
              );
            })}
          </div>

          {feedLoading ? (
            <div aria-busy="true" className="loading-placeholder" style={{ padding: 'var(--space-4)', textAlign: 'center', opacity: 0.7 }}>
              正在加载大家今天吃什么…
            </div>
          ) : feedError ? (
            <div role="alert" style={{ padding: 'var(--space-3)', borderRadius: 'var(--radius-md)', background: 'color-mix(in oklab, #e74c3c 12%, var(--color-surface))', border: '1px solid color-mix(in oklab, #c0392b 35%, var(--color-border))' }}>
              {feedError}
              <button
                type="button"
                className="button button-secondary"
                style={{ marginLeft: 'var(--space-2)' }}
                onClick={() => void fetchFeed(tab)}
              >
                重试
              </button>
            </div>
          ) : (
            <ul className="community-feed" style={{ listStyle: 'none', padding: 0, margin: 0, display: 'flex', flexDirection: 'column', gap: 'var(--space-3)' }}>
              {(feedItems ?? []).map((it) => (
                <li
                  key={it.id}
                  className="community-feed-card"
                  style={{
                    padding: 'var(--space-3)',
                    borderRadius: 'var(--radius-md)',
                    border: '1px solid var(--color-border)',
                    background: 'var(--color-surface)',
                  }}
                >
                  <header style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-2)', marginBottom: 'var(--space-2)' }}>
                    <span
                      aria-hidden
                      style={{
                        width: 36,
                        height: 36,
                        borderRadius: '50%',
                        background: 'color-mix(in oklab, var(--color-primary) 14%, var(--color-surface))',
                        display: 'inline-flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                        fontSize: '1.2rem',
                        flexShrink: 0,
                      }}
                    >
                      {it.author.avatar_emoji}
                    </span>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ fontWeight: 600, fontSize: '0.95rem' }}>
                        {it.author.nickname}
                      </div>
                      <div style={{ fontSize: '0.8rem', opacity: 0.7 }}>
                        {relativeTime(it.created_at)}
                      </div>
                    </div>
                    <span
                      className="pill-tag"
                      style={{
                        padding: '2px 10px',
                        borderRadius: 999,
                        fontSize: '0.8rem',
                        background: 'color-mix(in oklab, var(--color-accent) 16%, var(--color-surface))',
                        border: '1px solid color-mix(in oklab, var(--color-accent) 35%, var(--color-border))',
                        color: 'var(--color-accent)',
                      }}
                    >
                      {it.cuisine_tag}
                    </span>
                  </header>
                  <div style={{ marginBottom: 'var(--space-2)' }}>{it.content}</div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-3)', fontSize: '0.9rem', opacity: 0.85, flexWrap: 'wrap' }}>
                    <button
                      type="button"
                      onClick={() => void handleLike(it)}
                      disabled={likedBusy.has(it.id)}
                      aria-pressed={it.liked_by_me}
                      style={{
                        display: 'inline-flex',
                        alignItems: 'center',
                        gap: '6px',
                        border: 'none',
                        background: 'transparent',
                        padding: '4px 6px',
                        borderRadius: 8,
                        cursor: likedBusy.has(it.id) ? 'not-allowed' : 'pointer',
                        color: it.liked_by_me ? 'var(--color-primary)' : 'inherit',
                        fontWeight: it.liked_by_me ? 600 : 500,
                      }}
                    >
                      <span aria-hidden>{it.liked_by_me ? '❤️' : '🤍'}</span>
                      <span>{it.likes.toLocaleString()}</span>
                    </button>
                    <span style={{ display: 'inline-flex', alignItems: 'center', gap: '6px' }}>
                      <span aria-hidden>💬</span>
                      <span>{it.comments.toLocaleString()}</span>
                    </span>
                    <button
                      type="button"
                      className="button button-secondary"
                      onClick={() => {
                        track('community.feed.goto_recommend_click', {
                          feed_id: it.id,
                          food_code: it.food_code,
                          cuisine_tag: it.cuisine_tag,
                          sort: tab,
                        });
                        const feedCuisineKey = CUISINE_TAG_TO_Q07_KEY[it.cuisine_tag.trim()];
                        const url = feedCuisineKey
                          ? `/recommend?seed_food=${encodeURIComponent(it.food_code)}&prefill_cuisine=${encodeURIComponent(feedCuisineKey)}`
                          : `/recommend?seed_food=${encodeURIComponent(it.food_code)}`;
                        nav(url);
                      }}
                      title="跳到推荐页，预填'明确想吃'为这道菜，再给我生成 Top5"
                      style={{ margin: 0, padding: '4px 10px', fontSize: '0.85rem', marginLeft: 'auto' }}
                    >
                      🎯 用这个去做推荐
                    </button>
                    <button
                      type="button"
                      className="button button-secondary"
                      onClick={() => {
                        track('community.feed.goto_nearby_click', {
                          feed_id: it.id,
                          food_code: it.food_code,
                          cuisine_tag: it.cuisine_tag,
                          sort: tab,
                        });
                        nav(`/nearby?food_code=${encodeURIComponent(it.food_code)}`);
                      }}
                      title="跳到附近商家页，搜这道菜的周边店铺"
                      style={{ margin: 0, padding: '4px 10px', fontSize: '0.85rem' }}
                    >
                      📍 吃附近「{displayFoodName(it)}」
                    </button>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </section>

        {/* ======== 区块 3：Top 榜（窄屏用 CSS 在 community-page--aside 样式位置放到主题下方） ======== */}
        <aside
          aria-label="今日推荐 Top 榜"
          className="community-page__aside"
          style={{
            display: 'flex',
            flexDirection: 'column',
            gap: 'var(--space-3)',
            height: 'fit-content',
            position: 'sticky',
            top: 'var(--space-4)',
          }}
        >
          <div
            style={{
              padding: 'var(--space-3) var(--space-4)',
              borderRadius: 'var(--radius-md)',
              border: '1px solid var(--color-border)',
              background: 'var(--color-surface)',
            }}
          >
            <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', marginBottom: 'var(--space-2)' }}>
              <h3 style={{ margin: 0, fontSize: '1rem' }}>
                🏆 今日推荐 Top 榜
                {trendingMeta?.is_example && (
                  <span style={{
                    marginLeft: '8px',
                    fontSize: '0.7rem',
                    fontWeight: 'normal',
                    padding: '2px 6px',
                    borderRadius: '4px',
                    background: 'color-mix(in oklab, #f39c12 15%, var(--color-surface))',
                    color: '#e67e22',
                    border: '1px solid color-mix(in oklab, #f39c12 30%, var(--color-border))',
                    verticalAlign: 'middle',
                  }}>
                    示例数据
                  </span>
                )}
              </h3>
              <span style={{ fontSize: '0.8rem', opacity: 0.7 }}>基于全站推荐聚合</span>
            </div>

            {trendingError ? (
              <div role="alert" style={{ fontSize: '0.9rem', color: '#c0392b' }}>
                {trendingError}
              </div>
            ) : trending ? (
              <ol style={{ padding: 0, margin: 0, listStyle: 'none', display: 'flex', flexDirection: 'column', gap: '8px' }}>
                {trending.map((t) => (
                  <li
                    key={t.food_code}
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      gap: 'var(--space-2)',
                      padding: '8px 0',
                      borderTop: '1px dashed color-mix(in oklab, var(--color-border) 60%, transparent)',
                    }}
                  >
                    <span
                      aria-hidden
                      style={{
                        width: 24,
                        height: 24,
                        borderRadius: t.rank <= 3 ? '50%' : 4,
                        display: 'inline-flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                        fontSize: '0.8rem',
                        fontWeight: 700,
                        background:
                          t.rank === 1
                            ? 'color-mix(in oklab, #f1c40f 30%, var(--color-surface))'
                            : t.rank === 2
                              ? 'color-mix(in oklab, #95a5a6 25%, var(--color-surface))'
                              : t.rank === 3
                                ? 'color-mix(in oklab, #cd7f32 25%, var(--color-surface))'
                                : 'color-mix(in oklab, var(--color-border) 45%, var(--color-surface))',
                        color: t.rank <= 3 ? '#333' : 'inherit',
                        flexShrink: 0,
                      }}
                    >
                      {t.rank}
                    </span>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ fontWeight: 600, fontSize: '0.95rem' }}>{displayFoodName(t)}</div>
                      <div style={{ fontSize: '0.8rem', opacity: 0.7 }}>
                        {t.cuisine_tag} · 今日被推荐 <strong>{t.recommended_today.toLocaleString()}</strong> 次
                      </div>
                    </div>
                    <button
                      type="button"
                      className="button button-secondary"
                      onClick={() => nav(`/nearby?food_code=${encodeURIComponent(t.food_code)}`)}
                      style={{ margin: 0, padding: '4px 10px', fontSize: '0.85rem' }}
                    >
                      去吃 →
                    </button>
                  </li>
                ))}
              </ol>
            ) : (
              <div style={{ opacity: 0.7 }}>加载榜单中…</div>
            )}
          </div>
        </aside>
      </div>

      {/* ======== 区块 4：右下悬浮分享按钮 ======== */}
      <button
        type="button"
        className="community-fab"
        onClick={() => requireLoginThen(() => setShowShare(true))}
        aria-label="分享今天吃了啥"
        style={{
          position: 'fixed',
          right: 'var(--space-4)',
          bottom: 'calc(var(--space-5) + 64px)',
          width: 56,
          height: 56,
          borderRadius: '50%',
          border: 'none',
          cursor: 'pointer',
          background:
            'radial-gradient(circle at 30% 30%, #7c5cff, #3d6fe0)',
          color: '#fff',
          fontSize: '1.5rem',
          boxShadow: '0 8px 24px rgba(61,111,224,0.35)',
          display: 'inline-flex',
          alignItems: 'center',
          justifyContent: 'center',
          zIndex: 20,
        }}
        title="分享今天吃了啥"
      >
        ✏️
      </button>

      {showShare ? (
        <div
          role="dialog"
          aria-modal="true"
          aria-labelledby="share-sheet-title"
          style={{
            position: 'fixed',
            inset: 0,
            background: 'rgba(18, 20, 24, 0.45)',
            zIndex: 30,
            display: 'flex',
            alignItems: 'flex-end',
            justifyContent: 'center',
          }}
          onClick={() => setShowShare(false)}
        >
          <div
            onClick={(e) => e.stopPropagation()}
            style={{
              width: 'min(720px, 100%)',
              borderRadius: 'var(--radius-lg) var(--radius-lg) 0 0',
              padding: 'var(--space-4)',
              background: 'var(--color-surface)',
              boxShadow: '0 -12px 36px rgba(0,0,0,0.2)',
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 'var(--space-3)' }}>
              <h3 id="share-sheet-title" style={{ margin: 0 }}>✏️ 分享今天吃了啥</h3>
              <button
                type="button"
                aria-label="关闭弹层"
                onClick={() => setShowShare(false)}
                style={{ border: 'none', background: 'transparent', fontSize: '1.4rem', cursor: 'pointer' }}
              >
                ×
              </button>
            </div>
            <p style={{ opacity: 0.85 }}>
              发布动态（配图 + 菜系 + 短评）功能正在路上，P3 上线后你就能在这里一键分享啦～
              现在可以先去 <button
                type="button"
                className="button button-link"
                onClick={() => { setShowShare(false); nav('/recommend'); }}
                style={{ padding: 0, margin: 0 }}
              >
                做一次问卷推荐
              </button> 找灵感。
            </p>
            <div style={{ marginTop: 'var(--space-3)', textAlign: 'right' }}>
              <button type="button" className="button button-primary" onClick={() => setShowShare(false)}>
                好的，我等你
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
