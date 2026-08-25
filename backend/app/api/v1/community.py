"""B 阶段：社区聚合接口 MVP（v1）。

5 条接口（全部走 /api/v1/community 前缀）：
  GET  /feed?sort=hot|latest         → 「大家今天吃什么」流（先 mock 数据，schema 预留接 DB）
  GET  /trending                     → 今日推荐 Top 榜（基于 mock 聚合）
  GET  /theme                        → 本周主题（含投票进度 / 用户是否已投）
  POST /theme/vote                   → 主题投票（🔒登录；幂等，同一用户重复投不算数）
  POST /feed/{id}/like               → 点赞（🔒登录；幂等）

注意事项（MVP 阶段）：
  - 数据层：所有 GET 返回 mock，写操作落内存（进程内 dict），进程重启归零。
            后续接 Supabase 时只改本文件内部实现，对外 schema 不变。
  - 鉴权：写操作才需要登录；读操作匿名可读（避免社区首页白屏）。
  - Schema：全部 BaseModel 用 extra="forbid"，与 history/preferences 模块保持一致。
"""

from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel, ConfigDict, Field

from app.api.v1.auth import CurrentUser, get_current_user, get_current_user_optional


log = logging.getLogger("app.api.v1.community")

router = APIRouter(prefix="/api/v1/community", tags=["community"])


# ============================================================
# Schemas
# ============================================================

FeedSort = Literal["hot", "latest"]


class FeedAuthor(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_id: str = Field(..., max_length=64, description="匿名化后的展示用 ID（非真实 auth.id）")
    nickname: str = Field(..., max_length=32)
    avatar_emoji: str = Field(..., max_length=4, description="头像 emoji 占位，后续换 URL")


class FeedItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(..., max_length=32, description="社区动态 ID，供点赞/评论使用")
    author: FeedAuthor
    food_code: str = Field(..., max_length=64)
    cuisine_tag: str = Field(..., max_length=32, description="菜系 tag，例如 '日料' / '川菜'")
    content: str = Field(..., max_length=280, description="≤140 字短评，mock 阶段允许 280")
    likes: int = Field(..., ge=0)
    comments: int = Field(..., ge=0)
    created_at: datetime = Field(..., description="动态发布时间（UTC，ISO 8601）")
    liked_by_me: bool = Field(False, description="当前登录用户是否点过赞（匿名永远 false）")


class FeedListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sort: FeedSort
    items: list[FeedItem]


class TrendingItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rank: int = Field(..., ge=1, le=50)
    food_code: str = Field(..., max_length=64)
    cuisine_tag: str = Field(..., max_length=32)
    recommended_today: int = Field(..., ge=0, description="今日被推荐次数（mock 聚合）")


DataSource = Literal["real", "mixed", "seed"]


class TrendingResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    as_of: datetime = Field(..., description="榜单生成时间（UTC）")
    top_n: int = Field(..., ge=1)
    items: list[TrendingItem]
    data_source: DataSource = Field("seed", description="数据来源：real=纯真实 / mixed=混合 / seed=纯示例")
    is_example: bool = Field(False, description="是否为示例数据（前端展示用）")


class ThemeOption(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str = Field(..., max_length=32)
    label: str = Field(..., max_length=32)
    votes: int = Field(..., ge=0)
    percent: float = Field(..., ge=0.0, le=100.0)


class ThemeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    theme_id: str = Field(..., max_length=64)
    title: str = Field(..., max_length=120)
    subtitle: str = Field("", max_length=240)
    ends_at: datetime
    voted_key: str | None = Field(None, max_length=32, description="当前登录用户投过的选项；匿名时为 null")
    options: list[ThemeOption]


class ThemeVoteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    option_key: str = Field(..., max_length=32, description="主题选项 key，对应 ThemeOption.key")


class ThemeVoteResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ok: bool
    voted_key: str
    duplicated: bool = Field(False, description="是否重复投票（true=未累加，幂等）")
    options: list[ThemeOption]


class LikeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ok: bool
    liked: bool = Field(..., description="点赞后当前状态（true=已赞 / false=已取消，MVP 固定 true）")
    duplicated: bool = Field(False, description="是否重复点赞（true=未累加，幂等）")
    likes: int = Field(..., ge=0)


# ============================================================
# In-memory mock store（MVP only；生产改为 Supabase / Postgres）
# ============================================================


def _now_utc() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


# ----- Feed mock seed -----
_MOCK_AUTHORS: list[FeedAuthor] = [
    FeedAuthor(user_id="u_001", nickname="拉面爱好者", avatar_emoji="🍜"),
    FeedAuthor(user_id="u_002", nickname="麻婆豆腐不辣", avatar_emoji="🌶️"),
    FeedAuthor(user_id="u_003", nickname="减脂第 32 天", avatar_emoji="🥗"),
    FeedAuthor(user_id="u_004", nickname="深夜食堂打卡", avatar_emoji="🍱"),
    FeedAuthor(user_id="u_005", nickname="今天也要好好吃饭", avatar_emoji="🍚"),
    FeedAuthor(user_id="u_006", nickname="寿司专业户", avatar_emoji="🍣"),
    FeedAuthor(user_id="u_007", nickname="烧烤不能停", avatar_emoji="🍢"),
    FeedAuthor(user_id="u_008", nickname="韩餐爱好者", avatar_emoji="🥘"),
    FeedAuthor(user_id="u_009", nickname="一人食便当", avatar_emoji="🍙"),
    FeedAuthor(user_id="u_010", nickname="甜品是另一个胃", avatar_emoji="🍰"),
]

_MOCK_FEED_SEED: list[tuple[str, str, str, int, int, int]] = [
    # (food_code, cuisine_tag, content, likes, comments, hours_ago)
    ("sushi_salmon", "日料", "今天的三文鱼腩寿司太顶了，入口即化 🤤", 128, 24, 1),
    ("malatang", "川菜", "麻辣烫中辣刚刚好，加了两份福袋，满足！", 96, 11, 2),
    ("chicken_salad", "轻食", "减脂期午餐：鸡胸肉沙拉 + 牛油果，吃完不犯困", 72, 8, 3),
    ("ramen_tonkotsu", "日料", "豚骨拉面加了溏心蛋和叉烧，汤头绝了", 210, 31, 4),
    ("mapo_tofu", "川菜", "第一次自己做麻婆豆腐，被四川室友说正宗！", 54, 6, 5),
    ("bibimbap", "韩餐", "石锅拌饭配泡菜汤，锅巴就是灵魂 🥄", 88, 13, 6),
    ("yakitori", "日料", "居酒屋的鸡皮串烤得微焦，配一杯 Highball", 144, 19, 7),
    ("budae_jjigae", "韩餐", "部队锅+拉面+芝士，周末就该吃点治愈的", 162, 22, 9),
    ("poke_bowl", "轻食", "三文鱼波奇饭，健康又好看，适合拍照发圈", 63, 9, 12),
    ("cheesecake_basque", "甜品", "巴斯克芝士蛋糕焦焦的表皮，爱惨了", 79, 14, 20),
]


def _build_seed_feed() -> list[FeedItem]:
    now = _now_utc()
    items: list[FeedItem] = []
    rng = random.Random(20260817)  # 固定种子，保证重启后 mock 展示稳定
    for idx, (food_code, cuisine_tag, content, likes, comments, hours_ago) in enumerate(_MOCK_FEED_SEED):
        # 加一点随机抖动避免每小时整点都一摸一样
        created_at = now - timedelta(hours=hours_ago, minutes=rng.randint(0, 59))
        items.append(
            FeedItem(
                id=f"fd_{idx+1:03d}",
                author=_MOCK_AUTHORS[idx % len(_MOCK_AUTHORS)],
                food_code=food_code,
                cuisine_tag=cuisine_tag,
                content=content,
                likes=likes,
                comments=comments,
                created_at=created_at,
                liked_by_me=False,
            )
        )
    return items


@dataclass(slots=True)
class _CommunityStore:
    """MVP 进程内状态：点赞去重 + 主题投票去重。"""

    feed_items: list[FeedItem] = field(default_factory=_build_seed_feed)
    # feed_id -> set[user_id]  （点赞去重集合）
    like_by_feed: dict[str, set[str]] = field(default_factory=dict)
    # theme_id -> option_key -> set[user_id]  （投票去重集合）
    votes_by_theme: dict[str, dict[str, set[str]]] = field(default_factory=dict)
    # theme vote count cache: theme_id -> option_key -> int
    vote_count_cache: dict[str, dict[str, int]] = field(default_factory=dict)
    seeded_at: float = field(default_factory=time.time)

    # ---- Theme ----
    THEME_ID = "weekly_2026w33_jp_vs_kr"
    THEME_TITLE = "本周主题：日料 vs 韩餐 PK 🍣 🆚 🥘"
    THEME_SUBTITLE = "连续 7 天分享餐食，完成打卡送 20 次 AI 推荐额度。"

    def ensure_theme_seeded(self) -> None:
        if self.THEME_ID in self.votes_by_theme:
            return
        base: dict[str, set[str]] = {"jp": set(), "kr": set()}
        # 预置一些随机票数，展示上不至于 0 / 0
        rng = random.Random(2026081701)
        # 先塞些假 user_id 的票（真实用户看到的「已投」不包含这些假 id，因为 get_me 返回的是 auth.id）
        for i in range(142):
            base["jp"].add(f"seed_jp_{i}")
        for i in range(118):
            base["kr"].add(f"seed_kr_{i}")
        self.votes_by_theme[self.THEME_ID] = base
        self._refresh_vote_cache()

    def _refresh_vote_cache(self) -> None:
        self.vote_count_cache[self.THEME_ID] = {
            k: len(v) for k, v in self.votes_by_theme[self.THEME_ID].items()
        }

    # ---- Like helpers ----
    def toggle_like(self, *, feed_id: str, user_id: str) -> tuple[int, bool]:
        """幂等点赞。返回 (当前点赞数, 是否重复点赞)。"""
        users = self.like_by_feed.setdefault(feed_id, set())
        duplicated = user_id in users
        if not duplicated:
            users.add(user_id)
        # 同步更新 feed_items 里的点赞数（seed likes + 真实点赞增量）
        for item in self.feed_items:
            if item.id == feed_id:
                # 真实点赞 = seed 基数 + set 里真实用户（去掉 seed_jp_ / seed_kr_ 这种前缀假用户）
                real_likes = sum(1 for u in users if not (u.startswith("seed_jp_") or u.startswith("seed_kr_")))
                item.likes = _MOCK_LIKES_BASE.get(item.id, 0) + real_likes
                return item.likes, duplicated
        raise KeyError(feed_id)


# 用于点赞后还原 seed likes 基数（与 mock seed 对齐）
_MOCK_LIKES_BASE: dict[str, int] = {
    f"fd_{i+1:03d}": seed[3] for i, seed in enumerate(_MOCK_FEED_SEED)
}


_STORE = _CommunityStore()


def _theme_ends_at() -> datetime:
    # 本周日 23:59:59 UTC+8 → 简化：取 today + (6 - weekday) 天，23:59:59（本地时区）
    now = datetime.now()
    days_to_sunday = 6 - now.weekday()  # Monday=0 ... Sunday=6
    sunday_2359 = datetime(now.year, now.month, now.day, 23, 59, 59) + timedelta(days=days_to_sunday)
    # 转 UTC：北京时间 = UTC+8
    return (sunday_2359 - timedelta(hours=8)).replace(tzinfo=UTC)


def _build_theme_options(voted_key: str | None = None) -> list[ThemeOption]:
    _STORE.ensure_theme_seeded()
    counts = _STORE.vote_count_cache[_STORE.THEME_ID]
    jp = counts.get("jp", 0)
    kr = counts.get("kr", 0)
    total = max(jp + kr, 1)
    options = [
        ThemeOption(
            key="jp",
            label="日料 🍣",
            votes=jp,
            percent=round(jp / total * 100, 1),
        ),
        ThemeOption(
            key="kr",
            label="韩餐 🥘",
            votes=kr,
            percent=round(kr / total * 100, 1),
        ),
    ]
    _ = voted_key  # 目前 UI 只看 voted_key 在 ThemeResponse 顶层
    return options


# ----- Trending seed -----
_TRENDING_SEED: list[tuple[str, str, int]] = [
    # (food_code, cuisine_tag, recommended_today)
    ("ramen_tonkotsu", "日料", 287),
    ("budae_jjigae", "韩餐", 241),
    ("sushi_salmon", "日料", 226),
    ("malatang", "川菜", 203),
    ("bibimbap", "韩餐", 178),
]

# 聚合最小阈值：真实数据不足时用 Seed 数据填充
_MIN_REAL_COUNT_FOR_PURE_REAL = 5

# 菜系映射：food_code → cuisine_tag（用于 Trending 榜展示）
_FOOD_CODE_TO_CUISINE: dict[str, str] = {
    "ramen_tonkotsu": "日料",
    "sushi_salmon": "日料",
    "budae_jjigae": "韩餐",
    "bibimbap": "韩餐",
    "malatang": "川菜",
    "mapo_tofu": "川菜",
    "chicken_salad": "轻食",
    "poke_bowl": "轻食",
    "cheesecake_basque": "甜品",
    "yakitori": "日料",
}


def _aggregate_trending_from_history() -> list[tuple[str, str, int]]:
    """从 Supabase history 表聚合今日推荐 Top 榜。

    返回: [(food_code, cuisine_tag, recommended_today), ...]
    """
    try:
        from app.core.supabase_client import get_supabase_admin

        sb = get_supabase_admin()
        if sb is None:
            return []

        today = datetime.now().date().isoformat()
        result = (
            sb.table("history")
            .select("food_code")
            .gte("created_at", today)
            .not_is("food_code", "null")
            .execute()
        )

        if not result.data:
            return []

        # 聚合统计
        counter: dict[str, int] = {}
        for row in result.data:
            fc = row.get("food_code")
            if fc:
                counter[fc] = counter.get(fc, 0) + 1

        # 排序取 Top 10
        sorted_items = sorted(counter.items(), key=lambda x: x[1], reverse=True)[:10]
        return [
            (fc, _FOOD_CODE_TO_CUISINE.get(fc, "其他"), count)
            for fc, count in sorted_items
        ]
    except Exception:
        log.warning("trending_aggregate_failed: fallback to seed data")
        return []


def _build_trending_items(
    real_data: list[tuple[str, str, int]],
    seed_data: list[tuple[str, str, int]],
    min_count: int = _MIN_REAL_COUNT_FOR_PURE_REAL,
) -> tuple[list[TrendingItem], DataSource, bool]:
    """构建 Trending 榜单，支持真实/混合/种子三种数据来源。

    返回: (items, data_source, is_example)
    """
    if len(real_data) >= min_count:
        # 纯真实数据
        items = [
            TrendingItem(rank=i + 1, food_code=fc, cuisine_tag=ct, recommended_today=count)
            for i, (fc, ct, count) in enumerate(real_data)
        ]
        return items, "real", False

    if len(real_data) > 0:
        # 混合：真实数据 + Seed 填充
        real_codes = {fc for fc, _, _ in real_data}
        remaining_slots = _MIN_REAL_COUNT_FOR_PURE_REAL - len(real_data)
        seed_fill = [(fc, ct, cnt) for fc, ct, cnt in seed_data if fc not in real_codes][:remaining_slots]
        combined = real_data + seed_fill
        items = [
            TrendingItem(rank=i + 1, food_code=fc, cuisine_tag=ct, recommended_today=count)
            for i, (fc, ct, count) in enumerate(combined)
        ]
        return items, "mixed", True

    # 纯 Seed 数据
    items = [
        TrendingItem(rank=i + 1, food_code=fc, cuisine_tag=ct, recommended_today=count)
        for i, (fc, ct, count) in enumerate(seed_data)
    ]
    return items, "seed", True


# ============================================================
# Routes
# ============================================================


@router.get("/feed", response_model=FeedListResponse)
async def get_community_feed(
    sort: Literal["hot", "latest"] = Query(
        default="latest",
        description="排序：hot=按热度；latest=按时间倒序",
    ),
    current_user: CurrentUser | None = Depends(get_current_user_optional),
) -> FeedListResponse:
    """「大家今天吃什么」流。

    - 匿名 / 登录都可读；登录态会填充每个 item 的 liked_by_me 字段。
    - MVP：返回内存 mock 的 10 条数据。
    """
    items = list(_STORE.feed_items)
    if sort == "hot":
        # 热度 = likes * 2 + comments * 3（简单评分）
        items.sort(key=lambda x: (x.likes * 2 + x.comments * 3, x.created_at), reverse=True)
    else:
        items.sort(key=lambda x: x.created_at, reverse=True)

    user_id = current_user.user_id if current_user is not None else None
    if user_id is not None:
        for it in items:
            liked_set = _STORE.like_by_feed.get(it.id)
            it.liked_by_me = bool(liked_set and user_id in liked_set)
    return FeedListResponse(sort="hot" if sort == "hot" else "latest", items=items)


@router.get("/trending", response_model=TrendingResponse)
async def get_community_trending() -> TrendingResponse:
    """今日推荐 Top 榜。

    P6-02：支持三种数据来源：
    - real：纯真实数据（≥5 条真实记录）
    - mixed：真实数据 + Seed 填充（1-4 条真实记录）
    - seed：纯示例数据（无真实记录）
    """
    real_data = _aggregate_trending_from_history()
    items, data_source, is_example = _build_trending_items(real_data, _TRENDING_SEED)
    return TrendingResponse(
        as_of=_now_utc(),
        top_n=len(items),
        items=items,
        data_source=data_source,
        is_example=is_example,
    )


@router.get("/theme", response_model=ThemeResponse)
async def get_community_theme(
    current_user: CurrentUser | None = Depends(get_current_user_optional),
) -> ThemeResponse:
    """本周主题 + 投票进度。

    匿名可读；登录会返回 voted_key（已投选项 key，否则 null）。
    """
    _STORE.ensure_theme_seeded()
    voted_key: str | None = None
    if current_user is not None:
        per_option = _STORE.votes_by_theme[_STORE.THEME_ID]
        for key, users in per_option.items():
            if current_user.user_id in users:
                voted_key = key
                break
    return ThemeResponse(
        theme_id=_STORE.THEME_ID,
        title=_STORE.THEME_TITLE,
        subtitle=_STORE.THEME_SUBTITLE,
        ends_at=_theme_ends_at(),
        voted_key=voted_key,
        options=_build_theme_options(voted_key),
    )


@router.post("/theme/vote", response_model=ThemeVoteResponse)
async def vote_community_theme(
    body: ThemeVoteRequest,
    current_user: CurrentUser = Depends(get_current_user),
) -> ThemeVoteResponse:
    """主题投票（🔒登录）。幂等：同一用户对同一选项重复投 → duplicated=true 不累加。

    - 禁止切换选项（MVP 简单策略，防止刷票）：若已投过其他选项 → 返回 409。
    """
    if current_user is None:  # Depends(get_current_user) 通常不为 None，但显式兜底
        raise HTTPException(status_code=401, detail="UNAUTHENTICATED")

    _STORE.ensure_theme_seeded()
    user_id = current_user.user_id
    option_key = body.option_key

    per_option = _STORE.votes_by_theme[_STORE.THEME_ID]
    if option_key not in per_option:
        raise HTTPException(status_code=400, detail=f"BAD_OPTION: {option_key}")

    # 1) 已投同一选项 → 幂等直接返回
    if user_id in per_option[option_key]:
        _STORE._refresh_vote_cache()  # noqa: SLF001
        return ThemeVoteResponse(
            ok=True,
            voted_key=option_key,
            duplicated=True,
            options=_build_theme_options(option_key),
        )

    # 2) 已投其他选项 → 禁止切换
    for k, users in per_option.items():
        if k != option_key and user_id in users:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "ALREADY_VOTED_OTHER",
                    "message": "已投过其他选项，本周不能改票哦",
                    "voted_key": k,
                },
            )

    # 3) 首次投票 → 累加
    per_option[option_key].add(user_id)
    _STORE._refresh_vote_cache()  # noqa: SLF001
    return ThemeVoteResponse(
        ok=True,
        voted_key=option_key,
        duplicated=False,
        options=_build_theme_options(option_key),
    )


@router.post("/feed/{id}/like", response_model=LikeResponse)
async def like_community_feed(
    id: Annotated[str, Path(..., description="社区动态 ID", max_length=32)],
    current_user: CurrentUser = Depends(get_current_user),
) -> LikeResponse:
    """点赞（🔒登录）。幂等：重复点不会无限 +1。

    MVP 阶段暂不实现「取消点赞」，连续多次调用都保持已赞状态（duplicated 标识）。
    """
    if current_user is None:
        raise HTTPException(status_code=401, detail="UNAUTHENTICATED")

    # 先校验 feed_id 存在
    exists = any(x.id == id for x in _STORE.feed_items)
    if not exists:
        raise HTTPException(status_code=404, detail="FEED_NOT_FOUND")

    try:
        likes, duplicated = _STORE.toggle_like(feed_id=id, user_id=current_user.user_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="FEED_NOT_FOUND") from None

    return LikeResponse(ok=True, liked=True, duplicated=duplicated, likes=likes)
