/**
 * C：统一埋点 Helper（最小可用实现）。
 *
 * 目前项目还没有后端埋点接口（无 useAnalytics / POST /events/track）。
 * 为了立刻给所有新 CTA（社区🎯去推荐 / 本周主题🎯 / History归零 / seed预填等）
 * 打通曝光→点击→转化到推荐结果的漏斗，这里先实现：
 *   1) 所有事件 push 到 `localStorage[track.events.v1]`（环形队列，上限 RING_MAX=500）；
 *   2) 同时 `console.debug('[track]', event)`，联调时在 DevTools Console 直接可见；
 *   3) 导出 `flushTrackQueue()`，后端上了 `/events/track` 后，在登录成功/路由切换时
 *      调用即可把队列批量上报（只需改这一个文件，业务层 0 改动）。
 */

const QUEUE_KEY = 'track.events.v1';
const RING_MAX = 500;

export type TrackEventBase = {
  /** 事件名：模块 + 动宾，如 community.feed.goto_recommend_click */
  readonly name: string;
  /** 事件发生时的 client 相对时间戳（ms） */
  readonly ts: number;
  /** 事件属性（统一任意对象，各事件自有 schema） */
  readonly props?: Readonly<Record<string, unknown>>;
};

// 先写 TS 约定，让调用点有强约束，避免自由字符串拼写错误。
// 新增事件只要在这里加一条类型映射，下面 createEvent 就能用。
export type TrackEventName =
  | 'community.feed.impression'
  | 'community.feed.goto_recommend_click'
  | 'community.feed.goto_nearby_click'
  | 'community.theme_card.impression'
  | 'community.theme_card.goto_recommend_click'
  | 'history.wipe_all_click'
  | 'history.wipe_all_confirm'
  | 'history.wipe_all_cancel'
  | 'preference.wipe_all_click'
  | 'preference.wipe_all_confirm'
  | 'preference.wipe_all_cancel'
  | 'recommend.seed_prefill_applied'
  | 'recommend.follow_up_auto_skipped'
  | 'nearby.url_foodcode_applied';

function safeGetQueue(): TrackEventBase[] {
  try {
    if (typeof window === 'undefined') return [];
    const raw = window.localStorage.getItem(QUEUE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as unknown;
    return Array.isArray(parsed) ? (parsed as TrackEventBase[]) : [];
  } catch {
    // localStorage 异常（例如隐私模式、配额满、损坏 JSON）→ 当空队列处理
    return [];
  }
}

function safeSetQueue(q: readonly TrackEventBase[]): void {
  try {
    if (typeof window === 'undefined') return;
    const toWrite = q.length > RING_MAX ? q.slice(q.length - RING_MAX) : q;
    window.localStorage.setItem(QUEUE_KEY, JSON.stringify(toWrite));
  } catch {
    // 忽略写入失败：埋点不能阻塞业务
  }
}

export function createEvent(name: TrackEventName, props?: Readonly<Record<string, unknown>>): TrackEventBase {
  return {
    name,
    ts: Date.now(),
    props: props && Object.keys(props).length > 0 ? props : undefined,
  };
}

export function track(name: TrackEventName, props?: Readonly<Record<string, unknown>>): void {
  const ev = createEvent(name, props);
  // eslint-disable-next-line no-console
  console.debug('[track]', ev.name, ev.props ?? '');
  const q = safeGetQueue();
  q.push(ev);
  safeSetQueue(q);
}

/**
 * 读取并清空队列（等后端有 /events/track 后调用）。
 * 返回的是取出的事件列表；调用方上报失败时，可通过 unshiftTrackQueue 塞回去。
 */
export function drainTrackQueue(): TrackEventBase[] {
  const q = safeGetQueue();
  if (q.length === 0) return [];
  safeSetQueue([]);
  return q;
}

/** 上报失败时把事件塞回队头（保持时间序）。 */
export function unshiftTrackQueue(items: readonly TrackEventBase[]): void {
  if (!items?.length) return;
  const q = safeGetQueue();
  const merged = [...items, ...q];
  safeSetQueue(merged);
}
