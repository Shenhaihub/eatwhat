/**
 * B 阶段社区接口类型。
 *
 * 与 backend/app/api/v1/community.py 的 Pydantic Schema 1:1 对齐：
 *   GET  /community/feed        → FeedListResponse
 *   GET  /community/trending    → TrendingResponse
 *   GET  /community/theme       → ThemeResponse
 *   POST /community/theme/vote  → ThemeVoteRequest / ThemeVoteResponse
 *   POST /community/feed/{id}/like → LikeResponse
 */

// ==================== /feed ====================

export interface CommunityFeedAuthor {
  readonly user_id: string;
  readonly nickname: string;
  /** MVP 阶段用 emoji 占位头像，后续换 avatar_url */
  readonly avatar_emoji: string;
}

export interface CommunityFeedItem {
  readonly id: string;
  readonly author: CommunityFeedAuthor;
  readonly food_code: string;
  readonly food_name_zh?: string | null;
  readonly cuisine_tag: string;
  readonly content: string;
  readonly likes: number;
  readonly comments: number;
  /** ISO 8601 UTC 时间串 */
  readonly created_at: string;
  /** 登录才有用；匿名时恒 false */
  readonly liked_by_me: boolean;
}

export type CommunityFeedSort = 'hot' | 'latest';

export interface CommunityFeedListResponse {
  readonly sort: CommunityFeedSort;
  readonly items: readonly CommunityFeedItem[];
}

// ==================== /trending ====================

export interface CommunityTrendingItem {
  readonly rank: number;
  readonly food_code: string;
  readonly food_name_zh?: string | null;
  readonly cuisine_tag: string;
  readonly recommended_today: number;
}

export interface CommunityTrendingResponse {
  /** 榜单生成时间（ISO 8601 UTC） */
  readonly as_of: string;
  readonly top_n: number;
  readonly items: readonly CommunityTrendingItem[];
  /** 数据来源：real=纯真实 / mixed=混合 / seed=纯示例 */
  readonly data_source: 'real' | 'mixed' | 'seed';
  /** 是否为示例数据（前端展示提示用） */
  readonly is_example: boolean;
}

// ==================== /theme + /theme/vote ====================

export interface CommunityThemeOption {
  readonly key: string;
  readonly label: string;
  readonly votes: number;
  /** 百分比 0~100（保留 1 位小数） */
  readonly percent: number;
}

export interface CommunityThemeResponse {
  readonly theme_id: string;
  readonly title: string;
  readonly subtitle: string;
  /** 活动截止时间（ISO 8601 UTC） */
  readonly ends_at: string;
  /** 已投选项 key；未登录 / 未投 → null */
  readonly voted_key: string | null;
  readonly options: readonly CommunityThemeOption[];
}

export interface CommunityThemeVoteRequest {
  readonly option_key: string;
}

export interface CommunityThemeVoteResponse {
  readonly ok: boolean;
  readonly voted_key: string;
  /** true = 重复点，不累加 */
  readonly duplicated: boolean;
  readonly options: readonly CommunityThemeOption[];
}

// ==================== /feed/{id}/like ====================

export interface CommunityLikeResponse {
  readonly ok: boolean;
  /** MVP 阶段固定 true（不支持取消点赞） */
  readonly liked: boolean;
  /** true = 重复点，不累加 */
  readonly duplicated: boolean;
  /** 点赞后的最新点赞数 */
  readonly likes: number;
}
