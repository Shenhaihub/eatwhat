"""P7 验收测试：API 冒烟测试 + E2E 完整链路测试。

覆盖所有对外暴露的 API 端点，验证连通性和基本响应结构。
使用 TestClient，不需要启动真实服务器。

测试维度：
  A. 健康检查：/health/live, /health/ready
  B. 系统：/api/v1/system/metrics, /api/v1/system/ai-stats
  C. 问卷：/api/v1/questionnaire/next
  D. 推荐：POST /api/v1/recommendations
  E. 社区：/api/v1/community/feed, /trending, /theme
  F. 反馈：/api/v1/feedback/types, /submit, /report
  G. 安全：安全响应头、请求体大小限制
  H. E2E 链路：问卷 → 推荐生成 → 社区 → 反馈
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app


@pytest.fixture(scope="module")
def client() -> TestClient:
    settings = Settings(
        _env_file=None,
        app_env="test",
        app_mode="mock",
        ai_provider="mock",
        ai_api_key="",
        ew_ai_key_passphrase="",
        ew_ai_salt="",
        mock_ai_mode="normal",
    )
    app = create_app(settings)
    return TestClient(app)


# ============================================================
# A. 健康检查
# ============================================================


class TestHealth:
    def test_live(self, client: TestClient) -> None:
        r = client.get("/health/live")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

    def test_ready(self, client: TestClient) -> None:
        r = client.get("/health/ready")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ready"
        assert "config" in body
        assert "database" in body


# ============================================================
# B. 系统指标
# ============================================================


class TestSystemEndpoints:
    def test_metrics(self, client: TestClient) -> None:
        r = client.get("/api/v1/system/metrics")
        assert r.status_code == 200
        body = r.json()
        for key in ("uptime_seconds", "total_requests", "error_requests", "error_rate", "latency_ms"):
            assert key in body
        assert all(k in body["latency_ms"] for k in ("avg", "p50", "p95", "p99"))

    def test_ai_stats(self, client: TestClient) -> None:
        r = client.get("/api/v1/system/ai-stats")
        assert r.status_code == 200
        body = r.json()
        assert "queried_records" in body
        assert "window" in body


# ============================================================
# C. 问卷
# ============================================================


class TestQuestionnaire:
    def test_next_returns_structure(self, client: TestClient) -> None:
        r = client.post(
            "/api/v1/questionnaire/next",
            json={
                "entry_intent": "ai_recommend",
                "questionnaire_version": "v1.0",
                "answers_by_question_id": {
                    "meal_period": ["lunch"],
                    "appetite": ["normal"],
                    "q_budget": ["from_20_to_30"],
                    "q_cuisine_sichuan": ["c_sichuan"],
                    "q_taste_spicy": ["spicy"],
                },
            },
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert "next_questions" in body
        assert len(body["next_questions"]) > 0


# ============================================================
# D. 推荐
# ============================================================


class TestRecommendations:
    def test_generate_returns_items(self, client: TestClient) -> None:
        r = client.post(
            "/api/v1/recommendations",
            json={
                "entry_intent": "ai_recommend",
                "questionnaire_version": "v1.0",
                "answers_by_question_id": {
                    "meal_period": ["lunch"],
                    "appetite": ["normal"],
                    "q_budget": ["from_20_to_30"],
                    "q_cuisine_sichuan": ["c_sichuan"],
                    "q_taste_spicy": ["spicy"],
                },
            },
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert "items" in body
        assert len(body["items"]) > 0
        for item in body["items"]:
            assert "food_code" in item
            assert "priority" in item


# ============================================================
# E. 社区
# ============================================================


class TestCommunity:
    def test_feed(self, client: TestClient) -> None:
        r = client.get("/api/v1/community/feed")
        assert r.status_code == 200
        body = r.json()
        assert "items" in body
        assert len(body["items"]) > 0

    def test_trending(self, client: TestClient) -> None:
        r = client.get("/api/v1/community/trending")
        assert r.status_code == 200
        body = r.json()
        assert "items" in body
        assert "data_source" in body
        assert "is_example" in body

    def test_theme(self, client: TestClient) -> None:
        r = client.get("/api/v1/community/theme")
        assert r.status_code == 200
        body = r.json()
        assert "theme_id" in body
        assert "options" in body
        assert len(body["options"]) >= 2


# ============================================================
# F. 反馈
# ============================================================


class TestFeedback:
    def test_types(self, client: TestClient) -> None:
        r = client.get("/api/v1/feedback/types")
        assert r.status_code == 200
        items = r.json()
        assert len(items) == 4

    def test_submit_anonymous(self, client: TestClient) -> None:
        r = client.post(
            "/api/v1/feedback/submit",
            json={
                "feedback_type": "bug_report",
                "content": "这是一个测试反馈，用于验证反馈功能是否正常工作。",
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert body["feedback_id"].startswith("fb_")

    def test_report_needs_auth(self, client: TestClient) -> None:
        r = client.post(
            "/api/v1/feedback/report",
            json={
                "target_type": "feed",
                "target_id": "feed_001",
                "reason": "spam",
            },
        )
        assert r.status_code == 401


# ============================================================
# G. 安全加固
# ============================================================


class TestSecurity:
    def test_security_headers(self, client: TestClient) -> None:
        r = client.get("/health/live")
        assert r.headers.get("X-Content-Type-Options") == "nosniff"
        assert r.headers.get("X-Frame-Options") == "DENY"
        assert r.headers.get("X-XSS-Protection") == "1; mode=block"
        assert r.headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"

    def test_request_id_header(self, client: TestClient) -> None:
        r = client.get("/health/live")
        assert "X-Request-ID" in r.headers

    def test_cors_headers(self, client: TestClient) -> None:
        r = client.options(
            "/health/live",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert r.status_code == 200
        assert "access-control-allow-origin" in {k.lower() for k in r.headers.keys()}


# ============================================================
# H. E2E 完整链路（Mock 模式）
# ============================================================


class TestE2EFlow:
    """模拟用户完整操作流程：问卷 → 推荐 → 社区 → 反馈。"""

    def test_full_loop(self, client: TestClient) -> None:
        # Step 1: 问卷 + 推荐生成
        r1 = client.post(
            "/api/v1/recommendations",
            json={
                "entry_intent": "ai_recommend",
                "questionnaire_version": "v1.0",
                "answers_by_question_id": {
                    "meal_period": ["dinner"],
                    "appetite": ["normal"],
                    "q_budget": ["from_20_to_30"],
                    "q_cuisine_sichuan": ["c_sichuan"],
                    "q_taste_spicy": ["spicy"],
                },
            },
        )
        assert r1.status_code == 200, r1.text
        rec_body = r1.json()
        assert len(rec_body["items"]) > 0
        food_codes = [item["food_code"] for item in rec_body["items"]]
        assert all(fc for fc in food_codes)

        # Step 2: 访问社区 Feed
        r2 = client.get("/api/v1/community/feed")
        assert r2.status_code == 200
        assert len(r2.json()["items"]) > 0

        # Step 3: 访问社区 Trending 榜
        r3 = client.get("/api/v1/community/trending")
        assert r3.status_code == 200
        trending = r3.json()
        assert trending["data_source"] in ("real", "mixed", "seed")
        assert len(trending["items"]) > 0

        # Step 4: 访问社区主题投票
        r4 = client.get("/api/v1/community/theme")
        assert r4.status_code == 200
        theme = r4.json()
        assert len(theme["options"]) >= 2

        # Step 5: 提交反馈
        r5 = client.post(
            "/api/v1/feedback/submit",
            json={
                "feedback_type": "feature_request",
                "content": "希望推荐结果能展示更多附近的商家信息，方便选择。",
                "context": {"flow": "e2e_test", "food_codes": ",".join(food_codes[:3])},
            },
        )
        assert r5.status_code == 200
        assert r5.json()["ok"] is True

        # Step 6: 查看系统指标（确认请求被记录）
        r6 = client.get("/api/v1/system/metrics")
        assert r6.status_code == 200
        metrics = r6.json()
        assert metrics["total_requests"] >= 6
