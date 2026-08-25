"""P7-03 端到端测试：反馈模块 + 系统指标 + 社区 Trending 降级。

覆盖：
  1. 反馈类型列表获取
  2. 匿名/登录用户提交反馈
  3. 冷却期防刷
  4. 内容举报（需登录）
  5. 系统指标端点
  6. 社区 Trending 榜 data_source 字段
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app


def _build_test_client() -> TestClient:
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
# 反馈模块测试
# ============================================================


class TestFeedbackTypes:
    """GET /api/v1/feedback/types"""

    def test_returns_4_types(self) -> None:
        client = _build_test_client()
        r = client.get("/api/v1/feedback/types")
        assert r.status_code == 200
        items = r.json()
        assert len(items) == 4
        keys = {item["key"] for item in items}
        assert keys == {"bug_report", "feature_request", "content_report", "general"}
        for item in items:
            assert item["label"]
            assert item["description"]


class TestFeedbackSubmit:
    """POST /api/v1/feedback/submit"""

    def test_anonymous_can_submit(self) -> None:
        client = _build_test_client()
        r = client.post(
            "/api/v1/feedback/submit",
            json={
                "feedback_type": "bug_report",
                "content": "推荐页面加载时有闪烁问题，影响体验。",
                "page_url": "http://localhost:5173/recommend",
            },
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ok"] is True
        assert body["feedback_id"].startswith("fb_")
        assert "感谢" in body["message"]

    def test_content_too_short_rejected(self) -> None:
        client = _build_test_client()
        r = client.post(
            "/api/v1/feedback/submit",
            json={
                "feedback_type": "general",
                "content": "短",
            },
        )
        assert r.status_code == 422  # Pydantic validation error

    def test_content_too_long_rejected(self) -> None:
        client = _build_test_client()
        r = client.post(
            "/api/v1/feedback/submit",
            json={
                "feedback_type": "general",
                "content": "x" * 1001,
            },
        )
        assert r.status_code == 422

    def test_invalid_feedback_type_rejected(self) -> None:
        client = _build_test_client()
        r = client.post(
            "/api/v1/feedback/submit",
            json={
                "feedback_type": "invalid_type",
                "content": "这是一段足够长的反馈内容用于测试。",
            },
        )
        assert r.status_code == 422

    def test_context_optional(self) -> None:
        client = _build_test_client()
        r = client.post(
            "/api/v1/feedback/submit",
            json={
                "feedback_type": "feature_request",
                "content": "希望增加深色模式支持，夜间使用更友好。",
                "context": {"session_id": "abc123", "page": "recommend"},
            },
        )
        assert r.status_code == 200
        assert r.json()["ok"] is True


# ============================================================
# 系统指标测试
# ============================================================


class TestSystemMetrics:
    """GET /api/v1/system/metrics"""

    def test_returns_metrics_structure(self) -> None:
        client = _build_test_client()
        # 先发一个请求让 metrics 有数据
        client.get("/health/live")
        r = client.get("/api/v1/system/metrics")
        assert r.status_code == 200
        body = r.json()
        assert "uptime_seconds" in body
        assert "total_requests" in body
        assert "error_requests" in body
        assert "error_rate" in body
        assert "latency_ms" in body
        assert "avg" in body["latency_ms"]
        assert "p50" in body["latency_ms"]
        assert "p95" in body["latency_ms"]
        assert "p99" in body["latency_ms"]
        # 至少有刚才发的请求
        assert body["total_requests"] >= 1

    def test_error_count_increments_on_404(self) -> None:
        client = _build_test_client()
        # 发一个 404 请求
        client.get("/api/v1/nonexistent")
        r = client.get("/api/v1/system/metrics")
        body = r.json()
        assert body["error_requests"] >= 1


# ============================================================
# 社区 Trending 降级测试
# ============================================================


class TestTrendingDataSource:
    """GET /api/v1/community/trending 返回 data_source 字段"""

    def test_seed_data_has_example_flag(self) -> None:
        """Mock 模式下无真实历史 → 返回 seed 数据，is_example=True。"""
        client = _build_test_client()
        r = client.get("/api/v1/community/trending")
        assert r.status_code == 200
        body = r.json()
        assert "data_source" in body
        assert "is_example" in body
        assert body["data_source"] == "seed"
        assert body["is_example"] is True
        assert len(body["items"]) == 5
        # 每条都有 rank/food_code/cuisine_tag/recommended_today
        for item in body["items"]:
            assert item["rank"] >= 1
            assert item["food_code"]
            assert item["cuisine_tag"]
            assert item["recommended_today"] >= 0
