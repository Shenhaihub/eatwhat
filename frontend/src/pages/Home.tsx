import { useEffect, useMemo, useState } from 'react';
import { Link, useLocation, useNavigate } from 'react-router';

import CampaignBanner from '../components/CampaignBanner';
import { api } from '../services/api/client';
import type {
  CommunityTrendingItem,
  CommunityTrendingResponse,
} from '../services/api/types';
import { displayFoodName } from '../lib/foodNames';

type HomeLocationState = { readonly deleted?: boolean };

// 菜系徽章配色（与社区页一致）
const CUISINE_BADGE: Record<string, { label: string; color: string }> = {
  japanese: { label: '日料', color: '#e76f51' },
  korean: { label: '韩餐', color: '#f4a261' },
  western: { label: '西餐', color: '#2a9d8f' },
  fast_food: { label: '快餐', color: '#e9c46a' },
  chinese: { label: '中餐家常', color: '#e9c46a' },
  sichuan: { label: '川味', color: '#d62828' },
  cantonese: { label: '粤菜', color: '#457b9d' },
  spicy_hotpot: { label: '火锅', color: '#b5179e' },
  barbecue: { label: '烧烤', color: '#6d597a' },
  southeast_asian: { label: '东南亚', color: '#2d6a4f' },
  vegetarian: { label: '素食', color: '#95d5b2' },
  noodle: { label: '面食', color: '#fca311' },
  hotpot: { label: '麻辣烫', color: '#ef476f' },
  chinese_staple: { label: '家常菜', color: '#e9c46a' },
};
function cuisineBadge(tag: string) {
  const hit = CUISINE_BADGE[tag];
  return hit ?? { label: tag, color: '#adb5bd' };
}

/**
 * 今日活动 · 名场面日历（按星期几匹配耳熟能详的饮食活动）。
 * food_code 必须来自食物字典 v1.0（nearby 页依赖它搜索附近商家）。
 * new Date().getDay()：0=周日, 1=周一, ... 6=周六。
 */
interface TodayActivity {
  readonly weekday: number; // 0=Sun .. 6=Sat
  readonly name: string;
  readonly brand: string;
  readonly emoji: string;
  readonly food_code: string;
  readonly cuisine_tag: string;
  readonly desc: string;
}
const WEEKDAY_ACTIVITIES: readonly TodayActivity[] = [
  {
    weekday: 1,
    name: '疯狂星期一',
    brand: '麦当劳',
    emoji: '🍔',
    food_code: 'burger',
    cuisine_tag: 'fast_food',
    desc: '周一会员日，汉堡套餐价格砍半，打工人的快乐源泉。',
  },
  {
    weekday: 2,
    name: '周二会员日',
    brand: '汉堡王',
    emoji: '🍔',
    food_code: 'burger',
    cuisine_tag: 'fast_food',
    desc: '周二汉堡买一送一，大口满足不用犹豫。',
  },
  {
    weekday: 3,
    name: '周三半价披萨',
    brand: '必胜客',
    emoji: '🍕',
    food_code: 'pizza',
    cuisine_tag: 'western',
    desc: '周三披萨半价，一人食也能过个丰盛周三。',
  },
  {
    weekday: 4,
    name: '肯德基疯狂星期四',
    brand: '肯德基',
    emoji: '🍗',
    food_code: 'fried_chicken',
    cuisine_tag: 'fast_food',
    desc: 'v我50！周四炸鸡狂欢，经典不解释。',
  },
  {
    weekday: 5,
    name: '周五快乐炸鸡',
    brand: '各快餐',
    emoji: '🍗',
    food_code: 'fried_chicken',
    cuisine_tag: 'fast_food',
    desc: '周五炸鸡配啤酒，犒劳一周的自己。',
  },
  {
    weekday: 6,
    name: '周末烧烤局',
    brand: '烤肉店',
    emoji: '🍖',
    food_code: 'bbq',
    cuisine_tag: 'barbecue',
    desc: '周六烤串走起，烟火气和朋友都在。',
  },
  {
    weekday: 0,
    name: '周日日料放题',
    brand: '寿司店',
    emoji: '🍣',
    food_code: 'sushi',
    cuisine_tag: 'japanese',
    desc: '周日慢下来，吃一顿认真的日料。',
  },
];

const WEEKDAY_CN = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'];

export default function Home() {
  const location = useLocation();
  const nav = useNavigate();
  const [showDeletedToast, setShowDeletedToast] = useState(false);

  // P4-05：删除账号成功后的 toast
  useEffect(() => {
    const state = location.state as HomeLocationState | null | undefined;
    if (state?.deleted) {
      setShowDeletedToast(true);
      const t = window.setTimeout(() => setShowDeletedToast(false), 2500);
      return () => window.clearTimeout(t);
    }
    return undefined;
  }, [location.state]);

  // ===== 大家都在吃什么：trending（Top 榜） =====
  const [trending, setTrending] = useState<CommunityTrendingResponse | null>(null);
  const [trendingLoading, setTrendingLoading] = useState(true);
  const [trendingErr, setTrendingErr] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setTrendingLoading(true);
    void api
      .communityTrending()
      .then((t) => {
        if (!cancelled) setTrending(t);
      })
      .catch((e) => {
        if (!cancelled) {
          setTrendingErr(e instanceof Error ? e.message : '加载失败');
        }
      })
      .finally(() => {
        if (!cancelled) setTrendingLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const topTrending: readonly CommunityTrendingItem[] = useMemo(
    () => (trending?.items ?? []).slice(0, 5),
    [trending],
  );

  // ===== 今日活动：按当天星期几匹配名场面 =====
  const todayActivity: TodayActivity | undefined = useMemo(() => {
    const week = new Date().getDay();
    return WEEKDAY_ACTIVITIES.find((a) => a.weekday === week);
  }, []);

  const gotoActivityNearby = (foodCode: string) => {
    nav(`/nearby?food_code=${encodeURIComponent(foodCode)}`);
  };

  return (
    <div className="page-shell home-page-shell">
      {showDeletedToast ? (
        <div className="toast-success" role="status" aria-live="polite">
          账号及全部推荐历史已成功删除，期待下次再见 👋
        </div>
      ) : null}

      {/* B 阶段：活动横幅（首页 Hero 正上方） */}
      <CampaignBanner />

      <p className="eyebrow">先定方向，再找附近</p>
      <h1>别再纠结，先决定吃什么</h1>
      <p>回答几组会根据你选择变化的问题。需要 AI 时再登录，选定食物后帮你查附近商家。</p>
      <div className="hero-actions">
        <Link to="/recommend" className="button button-primary button-large">
          开始推荐
        </Link>
        <Link to="/community" className="button button-secondary">
          看看大家在吃什么
        </Link>
      </div>
      <p className="microcopy">预设问卷无需登录 · AI 只推荐食物，不编造商家</p>

      <h2>附近商家</h2>
      <p>选定食物后，帮你查附近商家。精确坐标只用于当前搜索，不写入历史。</p>
      <div className="hero-actions">
        <Link to="/nearby" className="button button-secondary">
          选地点找商家
        </Link>
      </div>

      {/* ========== 区块 1：大家都在吃什么 · 今日推荐 Top 榜 ========== */}
      <section className="home-section" aria-labelledby="trending-title">
        <div className="home-section-head">
          <div>
            <h2 id="trending-title">🔥 大家都在吃什么</h2>
            <p className="microcopy" style={{ margin: 0 }}>
              今天被推荐最多的食物 Top 榜 · 数据来自全站真实推荐结果
            </p>
          </div>
          <Link to="/community" className="button button-secondary" style={{ margin: 0 }}>
            去社区看详情 →
          </Link>
        </div>

        <div
          className="trending-list"
          style={{
            display: 'grid',
            gap: 'var(--space-2)',
            marginTop: 'var(--space-3)',
          }}
        >
          {trendingLoading ? (
            [...Array(5)].map((_, i) => (
              <div key={i} className="trending-skeleton" aria-hidden>
                <span className="tr-skel-rank" />
                <span className="tr-skel-name" />
                <span className="tr-skel-chip" />
                <span className="tr-skel-count" />
              </div>
            ))
          ) : trendingErr ? (
            <div
              className="notice"
              role="alert"
              style={{ background: 'color-mix(in oklab, #e74c3c 10%, var(--color-surface))' }}
            >
              <strong>榜单加载失败：</strong>
              <span>{trendingErr}</span>
              <button
                type="button"
                className="button button-secondary"
                style={{ marginLeft: 'var(--space-3)' }}
                onClick={() => {
                  setTrendingErr(null);
                  setTrendingLoading(true);
                  api
                    .communityTrending()
                    .then((t) => setTrending(t))
                    .catch((e) => setTrendingErr(e instanceof Error ? e.message : '加载失败'))
                    .finally(() => setTrendingLoading(false));
                }}
              >
                重试
              </button>
            </div>
          ) : topTrending.length === 0 ? (
            <div className="notice">今天暂时还没有推荐记录，去当第一个推荐的人吧！</div>
          ) : (
            topTrending.map((item) => {
              const badge = cuisineBadge(item.cuisine_tag);
              const gotoRecommend = () =>
                nav(
                  `/recommend?prefill_cuisine=${encodeURIComponent(item.cuisine_tag)}&prefill_food=${encodeURIComponent(item.food_code)}`,
                );
              const gotoNearby = () =>
                nav(`/nearby?food_code=${encodeURIComponent(item.food_code)}`);
              return (
                <article
                  key={`${item.rank}-${item.food_code}`}
                  className="trending-card"
                  data-rank={item.rank}
                >
                  <div className="tr-rank" aria-label={`第 ${item.rank} 名`}>
                    #{item.rank}
                  </div>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div
                      style={{
                        display: 'flex',
                        alignItems: 'center',
                        flexWrap: 'wrap',
                        gap: 'var(--space-2)',
                        marginBottom: 4,
                      }}
                    >
                      <h3 className="tr-name">{displayFoodName(item)}</h3>
                      <span
                        className="cuisine-chip"
                        style={{
                          background: `color-mix(in oklab, ${badge.color} 15%, var(--color-surface))`,
                          color: badge.color,
                          border: `1px solid color-mix(in oklab, ${badge.color} 50%, var(--color-border))`,
                        }}
                      >
                        {badge.label}
                      </span>
                    </div>
                    <div className="tr-count">今日被推荐 {item.recommended_today} 次</div>
                  </div>
                  <div className="tr-actions">
                    <button
                      type="button"
                      className="button button-secondary"
                      onClick={gotoNearby}
                      style={{ margin: 0 }}
                    >
                      📍 附近商家
                    </button>
                    <button
                      type="button"
                      className="button button-primary"
                      onClick={gotoRecommend}
                      style={{ margin: 0 }}
                    >
                      就吃这个 →
                    </button>
                  </div>
                </article>
              );
            })
          )}
        </div>
      </section>

      {/* ========== 区块 2：今日活动 · 名场面日历 ========== */}
      <section className="home-section" aria-labelledby="activity-title">
        <div className="home-section-head">
          <div>
            <h2 id="activity-title">🎉 今日活动 · 名场面</h2>
            <p className="microcopy" style={{ margin: 0 }}>
              {todayActivity
                ? `今天是${WEEKDAY_CN[todayActivity.weekday]}，${todayActivity.brand}${todayActivity.name}来啦！`
                : '每天都有不一样的美食活动，今天也不例外～'}
            </p>
          </div>
        </div>

        {todayActivity ? (
          <div className="activity-today-card">
            <div className="activity-today-emoji" aria-hidden>
              {todayActivity.emoji}
            </div>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  flexWrap: 'wrap',
                  gap: 'var(--space-2)',
                  marginBottom: 6,
                }}
              >
                <h3 className="tr-name">{todayActivity.name}</h3>
                <span className="cuisine-chip" style={{ background: 'color-mix(in oklab, #e9c46a 18%, var(--color-surface))', color: '#8a6d00' }}>
                  {todayActivity.brand}
                </span>
              </div>
              <p className="activity-desc">{todayActivity.desc}</p>
              <div className="tr-count">
                推荐去吃：{displayFoodName(todayActivity)} · 直接定位附近商家一步到位
              </div>
            </div>
            <div className="tr-actions">
              <button
                type="button"
                className="button button-primary button-large"
                onClick={() => gotoActivityNearby(todayActivity.food_code)}
                style={{ margin: 0 }}
              >
                {todayActivity.emoji} 就吃这个 → 找附近商家
              </button>
            </div>
          </div>
        ) : (
          <div className="notice">今天没有匹配到名场面活动，随便吃点啥都好～</div>
        )}

        {/* 本周其它活动一览（次要，折叠式提示） */}
        <details className="activity-other" style={{ marginTop: 'var(--space-3)' }}>
          <summary className="microcopy">本周还有这些名场面（按星期看）</summary>
          <div
            style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))',
              gap: 'var(--space-2)',
              marginTop: 'var(--space-2)',
            }}
          >
            {WEEKDAY_ACTIVITIES.filter((a) => a.weekday !== todayActivity?.weekday).map((a) => (
              <button
                key={a.weekday}
                type="button"
                className="activity-mini"
                onClick={() => gotoActivityNearby(a.food_code)}
              >
                <span className="activity-mini-emoji" aria-hidden>
                  {a.emoji}
                </span>
                <span style={{ fontWeight: 600 }}>{WEEKDAY_CN[a.weekday]} · {a.name}</span>
                <span className="microcopy">{displayFoodName(a)}</span>
              </button>
            ))}
          </div>
        </details>
      </section>
    </div>
  );
}
