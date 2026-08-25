"""B 阶段：社区接口集成测试（feed / trending / theme / vote / like）。

覆盖点：
  1) GET /community/feed 默认 latest：返回 10 条，时间倒序；匿名 liked_by_me 全 false。
  2) GET /community/feed?sort=hot：热度 = likes*2 + comments*3，top1 = ramen_tonkotsu。
  3) GET /community/trending：固定 Top5，rank 1..5，recommended_today > 0。
  4) GET /community/theme：主题 + options=[jp,kr] + 票数总和 > 0，匿名 voted_key=null。
  5) POST /theme/vote：
       - 匿名 → 401
       - 登录首次投 jp → ok/duplicated=false，jp 票数 +1
       - 重复投 jp → duplicated=true，票数不变
       - 再投 kr → 409 ALREADY_VOTED_OTHER
       - 非法 option_key → 400
  6) POST /feed/{id}/like：
       - 匿名 → 401
       - 登录首次点赞 fd_001 → likes 基数 +1
       - 重复点 → duplicated=true，likes 不变
       - feed_id 不存在 → 404
  7) 登录后 GET /feed → fd_002 liked_by_me=true，其他 false
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api.v1 import community as _community_mod
from app.api.v1.auth import CurrentUser, get_current_user, get_current_user_optional
from app.main import create_app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

# 每次测试前重建社区内存单例，避免测试间互相污染
@pytest.fixture(autouse=True)
def _reset_community_store():
    _community_mod._STORE = _community_mod._CommunityStore()  # noqa: SLF001
    yield


@pytest.fixture
def anon_client() -> TestClient:
    """匿名客户端：不 override get_current_user → 读接口匿名可访问。"""
    return TestClient(create_app())


def _make_logged_in_client(user_id: str, email: str = "u@example.com") -> TestClient:
    """构造 override 了 get_current_user 的「已登录」TestClient。

    因为 conftest 里 JWT 校验在 mock 模式下也会 401（SUPABASE_URL 已被清空），
    所以这里直接用 FastAPI dependency_overrides 绕开 JWT。
    """
    app = create_app()

    async def _fake() -> CurrentUser:
        return CurrentUser(
            user_id=user_id,
            email=email,
            role="authenticated",
            claims={"sub": user_id, "email": email, "role": "authenticated"},
        )

    app.dependency_overrides[get_current_user] = _fake
    app.dependency_overrides[get_current_user_optional] = _fake  # 读接口 Depends 的是可选登录，也要同步 override
    return TestClient(app)


FEED_URL = "/api/v1/community/feed"
TRENDING_URL = "/api/v1/community/trending"
THEME_URL = "/api/v1/community/theme"
VOTE_URL = "/api/v1/community/theme/vote"


def _like_url(feed_id: str) -> str:
    return f"/api/v1/community/feed/{feed_id}/like"


# ---------------------------------------------------------------------------
# 1 / 2 / 3 / 4  : 读接口（匿名）
# ---------------------------------------------------------------------------


def test_feed_latest_default_returns_10_sorted_desc(anon_client: TestClient):
    r = anon_client.get(FEED_URL)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["sort"] == "latest"
    items = body["items"]
    assert len(items) == 10
    # 匿名：liked_by_me 全 false
    for it in items:
        assert it["liked_by_me"] is False
        assert set(it) >= {"id", "author", "food_code", "cuisine_tag", "content", "likes", "comments", "created_at"}
    # 时间倒序
    times = [it["created_at"] for it in items]
    assert times == sorted(times, reverse=True)


def test_feed_hot_sort_top1_is_ramen_tonkotsu(anon_client: TestClient):
    r = anon_client.get(FEED_URL, params={"sort": "hot"})
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert items[0]["food_code"] == "ramen_tonkotsu"

    def score(it: dict) -> int:
        return it["likes"] * 2 + it["comments"] * 3

    assert score(items[0]) >= score(items[1])


def test_trending_top5_valid(anon_client: TestClient):
    r = anon_client.get(TRENDING_URL)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["top_n"] == 5
    ranks = [it["rank"] for it in body["items"]]
    assert ranks == [1, 2, 3, 4, 5]
    for it in body["items"]:
        assert it["recommended_today"] > 0
        assert it["food_code"] and it["cuisine_tag"]
    assert body["as_of"]


def test_theme_default_has_jp_kr_and_total_votes(anon_client: TestClient):
    r = anon_client.get(THEME_URL)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["theme_id"] == "weekly_2026w33_jp_vs_kr"
    assert body["title"] and body["ends_at"]
    # 匿名：voted_key 为 null
    assert body["voted_key"] is None
    options = {o["key"]: o for o in body["options"]}
    assert set(options) == {"jp", "kr"}
    total = options["jp"]["votes"] + options["kr"]["votes"]
    assert total > 0
    assert abs(options["jp"]["percent"] + options["kr"]["percent"] - 100.0) <= 0.2


# ---------------------------------------------------------------------------
# 5 : 主题投票（鉴权 + 幂等 + 禁止切换 + 非法选项）
# ---------------------------------------------------------------------------


def test_theme_vote_anonymous_401(anon_client: TestClient):
    r = anon_client.post(VOTE_URL, json={"option_key": "jp"})
    # Depends(get_current_user) 会要求 Authorization 头；缺失返回 401
    assert r.status_code == 401, r.text


def test_theme_vote_login_scenario():
    alice = _make_logged_in_client("alice", "alice@eat.foo")

    # (a) 先取 theme 初始票数
    t0 = {o["key"]: o["votes"] for o in alice.get(THEME_URL).json()["options"]}

    # (b) 首次投 jp → ok + duplicated=false + jp +1
    r1 = alice.post(VOTE_URL, json={"option_key": "jp"})
    assert r1.status_code == 200, r1.text
    j1 = r1.json()
    assert j1["ok"] is True
    assert j1["voted_key"] == "jp"
    assert j1["duplicated"] is False
    opts_1 = {o["key"]: o for o in j1["options"]}
    assert opts_1["jp"]["votes"] == t0["jp"] + 1
    # 再读 /theme，alice voted_key == "jp"
    theme_1 = alice.get(THEME_URL).json()
    assert theme_1["voted_key"] == "jp"

    # (c) 重复投 jp → duplicated=true，票数不变
    r2 = alice.post(VOTE_URL, json={"option_key": "jp"})
    assert r2.status_code == 200, r2.text
    j2 = r2.json()
    assert j2["duplicated"] is True
    opts_2 = {o["key"]: o for o in j2["options"]}
    assert opts_2["jp"]["votes"] == t0["jp"] + 1

    # (d) 再投 kr → 409 禁止切换
    r3 = alice.post(VOTE_URL, json={"option_key": "kr"})
    assert r3.status_code == 409, r3.text
    body = r3.json()
    # 项目统一错误包装：{ "error": { "code": ..., "message": ... } }
    err = body.get("error") or {}
    assert err.get("code") == "ALREADY_VOTED_OTHER"

    # (e) 非法选项 → 400
    bob = _make_logged_in_client("bob", "bob@eat.foo")
    r4 = bob.post(VOTE_URL, json={"option_key": "xx"})
    assert r4.status_code == 400, r4.text


# ---------------------------------------------------------------------------
# 6 : 点赞（鉴权 + 幂等 + 不存在 404）
# ---------------------------------------------------------------------------


def test_like_anonymous_401(anon_client: TestClient):
    r = anon_client.post(_like_url("fd_001"))
    assert r.status_code == 401, r.text


def test_like_login_scenario():
    carol = _make_logged_in_client("carol", "carol@eat.foo")

    # (a) 先取初始 likes（匿名读）
    anon = TestClient(create_app())
    items = {x["id"]: x for x in anon.get(FEED_URL, params={"sort": "latest"}).json()["items"]}
    likes_base = items["fd_001"]["likes"]

    # (b) 首次点赞 fd_001
    r1 = carol.post(_like_url("fd_001"))
    assert r1.status_code == 200, r1.text
    j1 = r1.json()
    assert j1["ok"] is True
    assert j1["liked"] is True
    assert j1["duplicated"] is False
    assert j1["likes"] == likes_base + 1

    # (c) 重复点赞 fd_001 → duplicated=true，likes 不叠加
    r2 = carol.post(_like_url("fd_001"))
    assert r2.status_code == 200, r2.text
    j2 = r2.json()
    assert j2["duplicated"] is True
    assert j2["likes"] == likes_base + 1

    # (d) 不存在 feed_id → 404
    r3 = carol.post(_like_url("fd_999_not_exist"))
    assert r3.status_code == 404, r3.text


# ---------------------------------------------------------------------------
# 7 : 登录后 liked_by_me 正确标记
# ---------------------------------------------------------------------------


def test_liked_by_me_flag_after_like():
    dave = _make_logged_in_client("dave", "dave@eat.foo")
    # 先点 fd_002 赞
    r = dave.post(_like_url("fd_002"))
    assert r.status_code == 200, r.text

    # 再读 feed（登录）
    r2 = dave.get(FEED_URL)
    assert r2.status_code == 200, r2.text
    items = {it["id"]: it for it in r2.json()["items"]}
    assert items["fd_002"]["liked_by_me"] is True
    # 其他 feed 仍是 false
    assert items["fd_003"]["liked_by_me"] is False
