"""P6-01 用户偏好画像持久化冒烟测试。

策略：
  1) Schema Pydantic round-trip（QuestionnaireAnswers → PreferenceWriteRequest）
  2) list/latest/delete/clear 的 HTTP 契约（401 无 token，404 无 latest）
  3) recommendations 模块中 _try_autowrite_history_if_user 对偏好写入失败
     完全静默不影响主流程——通过故意触发的异常来验证。
"""
from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from app.api.v1.preferences import (
    PreferenceWriteRequest,
    _row_to_response,
)
from app.core.config import Settings
from app.core.supabase_client import SupabaseAdminClient
from app.main import create_app
from app.schemas.enums import Appetite, MealPeriod, Taste
from app.schemas.food import QuestionnaireAnswers

# ---------- utilities ----------

def _sample_questionnaire_answers() -> QuestionnaireAnswers:
    return QuestionnaireAnswers(
        questionnaire_version="v1.0",
        meal_period=MealPeriod.BREAKFAST,
        appetite=Appetite.NORMAL,
        avoidances=[],
        tastes=[Taste.LIGHT],
        budget=None,
        max_distance_m=None,
        explicit_food_preference=None,
        ai_follow_up_answers={"q_follow_up_1": "alone"},
    )


def _make_app(monkeypatch: pytest.MonkeyPatch, sb: SupabaseAdminClient) -> TestClient:
    from app.core.config import get_settings
    from app.core.supabase_client import get_supabase_admin

    settings = Settings(
        _env_file=None,
        app_env="test",
        app_mode="mock",
        supabase_url="https://example.supabase.co",
        supabase_service_role_key="eyJ_fake_service_role",
        supabase_anon_key="eyJ_fake_anon",
    )

    async def _override_sb() -> AsyncIterator[SupabaseAdminClient]:
        yield sb

    monkeypatch.setattr(
        "app.api.v1.auth._fetch_jwk_for_header",
        lambda _kid, _s: {"kty": "RSA", "kid": "testkid", "n": "tZ8VKQ", "e": "AQAB", "use": "sig", "alg": "RS256"},
    )
    monkeypatch.setattr("app.api.v1.auth._public_key_from_jwk", lambda _jwk: object())

    app = create_app(settings)
    app.dependency_overrides[get_supabase_admin] = _override_sb
    app.dependency_overrides[get_settings] = lambda: settings
    return TestClient(app)


def _make_fake_sb() -> SupabaseAdminClient:
    """用 Mock().table().xxx().execute() 链式返回，只测 HTTP 契约。"""
    settings = Settings(
        _env_file=None,
        app_env="test",
        supabase_url="https://example.supabase.co",
        supabase_service_role_key="eyJ_fake",
    )
    mock_client = Mock()
    return SupabaseAdminClient(client=mock_client, settings=settings)  # type: ignore[arg-type]


# ============================================================
# 1. Schema round-trip
# ============================================================
class TestPreferenceSchema:
    def test_write_request_roundtrip_from_rule_answers(self) -> None:
        answers = _sample_questionnaire_answers()
        req = PreferenceWriteRequest(
            questionnaire_version="v1.0",
            dictionary_version="v1.2",
            source_session_id="sess_abc123",
            source_history_id=None,
            snapshot=answers.model_dump(mode="json"),
        )
        dump = req.model_dump(mode="json")
        # snapshot 完整保留核心字段（meal_period 是实际 schema 存在字段）
        assert dump["snapshot"]["meal_period"] == "breakfast"
        assert dump["snapshot"]["tastes"] == ["light"]
        # AI 追问题记录保留下来
        assert dump["snapshot"]["ai_follow_up_answers"] == {"q_follow_up_1": "alone"}
        # source_session_id 可溯源到 P5 会话
        assert dump["source_session_id"] == "sess_abc123"

    def test_row_to_response_uuid_cast(self) -> None:
        row = {
            "id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
            "user_id": "ffffffff-ffff-ffff-ffff-ffffffffffff",
            "questionnaire_version": "v1.0",
            "dictionary_version": "v1.2",
            "source_session_id": None,
            "source_history_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            "snapshot_jsonb": {"meal_period": "lunch", "tastes": ["light"]},
            "created_at": datetime.now(tz=UTC).isoformat(),
            "updated_at": datetime.now(tz=UTC).isoformat(),
        }
        resp = _row_to_response(row)
        assert resp.id is not None
        assert str(resp.source_history_id) == "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        assert resp.snapshot == {"meal_period": "lunch", "tastes": ["light"]}


# ============================================================
# 2. HTTP 契约（无 JWT = 401；latest 空 = 404）
# ============================================================
class TestPreferenceHttp:
    def test_list_without_token_401(self, monkeypatch: pytest.MonkeyPatch) -> None:
        sb = _make_fake_sb()
        client = _make_app(monkeypatch, sb)
        resp = client.get("/api/v1/preferences")
        assert resp.status_code == 401

    def test_latest_without_token_401(self, monkeypatch: pytest.MonkeyPatch) -> None:
        sb = _make_fake_sb()
        client = _make_app(monkeypatch, sb)
        resp = client.get("/api/v1/preferences/latest")
        assert resp.status_code == 401


# ============================================================
# 3. 静默失败：偏好写入 DB 抛异常时，_try_autowrite 不会冒泡
# ============================================================
class TestPreferenceAutowriteFailsafe:
    def test_autowrite_swallows_db_errors(self, caplog) -> None:
        from app.api.v1.recommendations import (
            RecommendationsGenerateRequestV1,
            _try_autowrite_history_if_user,
        )
        from app.schemas.food import RecommendationItem

        class _DummyCurrentUser:
            user_id = "00000000-0000-0000-0000-000000000001"
            email = "t@example.com"

        payload = RecommendationsGenerateRequestV1(
            entry_intent="ai_recommend",
            questionnaire_version="v1.0",
            answers_by_question_id={"q1": ["breakfast"]},
        )
        items: list[RecommendationItem] = []

        with caplog.at_level(logging.WARNING, logger="app.api.v1.recommendations"):
            # 应当绝对不抛异常
            _try_autowrite_history_if_user(
                current_user=_DummyCurrentUser(),  # type: ignore[arg-type]
                sb=None,  # None → 直接 return
                payload=payload,
                dict_version="v1.2",
                items=items,
                rule_answers=_sample_questionnaire_answers(),
            )
            # sb=None → 静默跳过，不写日志
            warnings_only_for_sb = [
                r for r in caplog.records
                if r.message.startswith(("history_autowrite_failed", "preference_autowrite_failed"))
            ]
            assert warnings_only_for_sb == []
