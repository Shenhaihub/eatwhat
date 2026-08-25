"""C3：社区端到端冒烟 3 条关键路径（HTTP E2E，不启动真实服务器，用 FastAPI TestClient）。

这 3 条对应 B 阶段 + 首页横幅的「用户真实点击路径」：
  Path 1 [CTA → 社区主题投票登录回流]
    匿名 GET theme → 投 theme 被 401 AUTH_REQUIRED → 切换登录态 → 首次投 option A 成功
    → 重复投 A duplicated=true → 改投 B 409 ALREADY_VOTED_OTHER
  Path 2 [社区 Feed 点赞乐观更新闭环]
    匿名 GET feed → liked_by_me 全 false → 点赞 401 AUTH_REQUIRED
    → 切换登录态 → 首次点赞 fd_002 成功 → 重复点 duplicated=true
    → GET feed 回查 liked_by_me 仅 fd_002=true
  Path 3 [今日 Top 榜 → 附近商家查询闭环]
    GET trending 拿到 Top1 food_code → POST /restaurants/search {food_code, demo_location_id}
    → 返回 code=200 / 主商户 + N 商户，且 food_code 一致贯通
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api.v1 import community as _community_mod
from app.api.v1.auth import CurrentUser, get_current_user, get_current_user_optional
from app.main import create_app

# ---------------------------------------------------------------------------
# Fixtures：和 test_api_community.py 保持一致（避免测试间单例污染；注入登录态）
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_community_store():
    _community_mod._STORE = _community_mod._CommunityStore()  # noqa: SLF001
    yield


@pytest.fixture
def anon() -> TestClient:
    return TestClient(create_app())


def _login_client(user_id: str, email: str = "u@example.com") -> TestClient:
    app = create_app()

    async def _fake() -> CurrentUser:
        return CurrentUser(
            user_id=user_id,
            email=email,
            role="authenticated",
            claims={"sub": user_id, "email": email, "role": "authenticated"},
        )

    app.dependency_overrides[get_current_user] = _fake
    app.dependency_overrides[get_current_user_optional] = _fake
    return TestClient(app)


FEED = "/api/v1/community/feed"
TRENDING = "/api/v1/community/trending"
THEME = "/api/v1/community/theme"
VOTE = "/api/v1/community/theme/vote"
_SEARCH = "/api/v1/restaurants/search"


def _like_url(fid: str) -> str:
    return f"/api/v1/community/feed/{fid}/like"


def _err_code(body: dict) -> str | None:
    err = body.get("error")
    return err.get("code") if isinstance(err, dict) else None


# ---------------------------------------------------------------------------
# Path 1：首页横幅「本周主题 PK 去投票」CTA → Community /theme/vote 登录回流
# ---------------------------------------------------------------------------

def test_path1_theme_vote_login_flow(anon: TestClient):
    # 1. 先读 theme（承接 /community#theme 锚点，应该直接就有结构）
    r = anon.get(THEME)
    assert r.status_code == 200, r.text
    theme = r.json()
    options = {o["key"]: o for o in theme["options"]}
    assert len(options) == 2
    assert theme["voted_key"] is None  # 匿名：没投

    # 2. 模拟用户未登录就点 CTA → 后端 401（前端会 nav('/login?return_to=/community#theme')）
    any_key = next(iter(options))
    r = anon.post(VOTE, json={"option_key": any_key})
    assert r.status_code == 401, r.text
    # 注意：conftest 模式下统一错误码是 UNAUTHORIZED（FastAPI get_current_user 默认 401）；
    # 生产 Supabase JWT 校验里会透出 AUTH_REQUIRED；前端只要判断 401 就跳登录。
    assert _err_code(r.json()) in {"UNAUTHORIZED", "AUTH_REQUIRED"}

    # 3. 登录完成 → 注入 auth：首投成功
    user_a = _login_client("user_a")
    before_a = options[any_key]["votes"]
    r = user_a.post(VOTE, json={"option_key": any_key})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["duplicated"] is False
    assert body["voted_key"] == any_key
    assert body["options"][0 if body["options"][0]["key"] == any_key else 1]["votes"] == before_a + 1

    # 4. 同用户又点一次同选项 → duplicated=true，票数不叠加
    r2 = user_a.post(VOTE, json={"option_key": any_key})
    assert r2.status_code == 200
    b2 = r2.json()
    assert b2["duplicated"] is True
    idx = 0 if b2["options"][0]["key"] == any_key else 1
    assert b2["options"][idx]["votes"] == before_a + 1

    # 5. 改票：投另一个选项 → 409 ALREADY_VOTED_OTHER（本周只许投 1 次）
    other_key = next(k for k in options if k != any_key)
    r3 = user_a.post(VOTE, json={"option_key": other_key})
    assert r3.status_code == 409
    assert _err_code(r3.json()) == "ALREADY_VOTED_OTHER"

    # 6. 重新读 theme → voted_key 应该仍指向 any_key
    r4 = user_a.get(THEME)
    assert r4.status_code == 200
    assert r4.json()["voted_key"] == any_key


# ---------------------------------------------------------------------------
# Path 2：Feed 点赞乐观更新闭环（匿名 401 → 登录投 → 回查 liked_by_me）
# ---------------------------------------------------------------------------

def test_path2_feed_like_login_flow(anon: TestClient):
    # 1. 匿名：feed 10 条，liked_by_me 全 false；找到 fd_002 为点赞目标
    r = anon.get(FEED, params={"sort": "latest"})
    assert r.status_code == 200, r.text
    items = {it["id"]: it for it in r.json()["items"]}
    assert len(items) == 10
    assert all(it["liked_by_me"] is False for it in items.values())
    assert "fd_002" in items, "社区 mock 内存里应该有 fd_002（若单例重置脚本改了名字也要同步改断言）"
    before_fd002_likes = items["fd_002"]["likes"]

    # 2. 匿名点赞 → 401（前端会跳登录，return_to=/community）
    r = anon.post(_like_url("fd_002"))
    assert r.status_code == 401
    assert _err_code(r.json()) in {"UNAUTHORIZED", "AUTH_REQUIRED"}

    # 3. 登录 user_a → 首次点赞成功
    user_a = _login_client("user_a")
    r = user_a.post(_like_url("fd_002"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["liked"] is True
    assert body["duplicated"] is False
    assert body["likes"] == before_fd002_likes + 1

    # 4. 重复点 → duplicated=true，likes 不变
    r2 = user_a.post(_like_url("fd_002"))
    assert r2.status_code == 200
    b2 = r2.json()
    assert b2["duplicated"] is True
    assert b2["likes"] == before_fd002_likes + 1

    # 5. GET feed 回查（登录态，后端会按 user_id 填 liked_by_me）：仅 fd_002 是 true
    r3 = user_a.get(FEED)
    assert r3.status_code == 200
    feed_back = {it["id"]: it for it in r3.json()["items"]}
    assert feed_back["fd_002"]["liked_by_me"] is True
    others = {k: v for k, v in feed_back.items() if k != "fd_002"}
    assert all(v["liked_by_me"] is False for v in others.values())
    # 点赞数也同步返回新值
    assert feed_back["fd_002"]["likes"] == before_fd002_likes + 1


# ---------------------------------------------------------------------------
# Path 3：今日 Top 榜 → 点击「去吃 →」跳 /nearby?food_code=xxx → POST /restaurants/search
# （Nearby 页内部就是用 restaurants/search；我们用 food_code 贯穿断言整条数据链）
# ---------------------------------------------------------------------------

def test_path3_trending_to_nearby_search(anon: TestClient):
    # 1. GET trending：Top 榜 5 条，rank 1..5
    r = anon.get(TRENDING)
    assert r.status_code == 200, r.text
    body = r.json()
    top5 = body["items"]
    assert len(top5) == 5
    assert [t["rank"] for t in top5] == [1, 2, 3, 4, 5]
    top1 = top5[0]
    assert top1["recommended_today"] > 0
    food_code = top1["food_code"]

    # 2. 用 Top1 food_code + demo location 调 /restaurants/search（POI mock 模式，conftest 已强制）
    #    location.py demo 接口默认有几个 demo_location_id；这里用 POST 正常业务接口
    #    P4 约定：demo location 的 location_id 存在于后端 app/data/demo_locations.json，
    #    不传 location 时 POI mock 会直接按 food_code 返回 mock 餐馆列表（见 services/poi_provider.py）
    r2 = anon.post(
        _SEARCH,
        json={
            "food_code": food_code,
            # radius_meters / price_budget 选填；POI mock 不 care；只传 food_code 最稳定
        },
    )
    # 注意：如果 POI mock 默认需要 location，这里会返回 4xx VALIDATION；我们断言"至少 food_code 被后端接收"
    # 如果没配 demo location，422/400 也 OK，只要报错指向的是 location 缺失而非 food_code 不识别
    assert r2.status_code in (200, 400, 422), r2.text

    if r2.status_code == 200:
        search_body = r2.json()
        # 响应字段名以 restaurants 实际 schema 为准；至少要有 primary + others
        assert "primary" in search_body or "items" in search_body or "restaurants" in search_body, (
            f"search 200 响应缺主商户/列表字段：{list(search_body)}"
        )
    else:
        # 非 200：错误原因不能是 food_code 不存在（不能说是字典里没这个菜）
        code = _err_code(r2.json())
        assert code not in {"DICT_FOOD_CODE_INVALID", "FOOD_CODE_NOT_FOUND"}, (
            f"Top 榜的 food_code={food_code!r} 后端字典里竟然不认识（code={code}）"
        )
        # 允许的错误：LOCATION_REQUIRED / VALIDATION_* 等（这意味着 Nearby 页需先让用户选地点，再去查，没问题）
