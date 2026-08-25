"""P5-02 动态推荐会话状态机单元测试。

覆盖：
1. create/get 会话正常工作
2. start_and_get_next 给第 1 道追问题（AI/模板回退）
3. answer_and_advance 三轮顺利推进 → 最后返回 None
4. 重复答同一题 → QuestionAlreadyAnsweredError（幂等 409）
5. round 不匹配 / option value 非法 → InvalidOptionValueError
6. AI 增益 finalize 成功 → 5 条 generation_mode=ai
7. AI finalize 失败 → 回退规则引擎，final_reason=rule_engine_fallback_ai_fail
8. 会话过期 → SessionNotFoundError（惰性清理）
9. get_recommendation_session_manager 单例一致性
10. finalize_recommendation 幂等（二次调用不重建）
11. AI slow 模式 → ChatService timeout 回退 → 规则兜底
"""
from __future__ import annotations

import time
from typing import Literal

import pytest

from app.core.config import Settings
from app.repositories.food_dictionary import get_food_dictionary_repository
from app.schemas import GenerationMode, QuestionnaireAnswers, SourceType
from app.services.ai.mock_provider import FOLLOW_UP_TEMPLATES
from app.services.ai.service import ChatService
from app.services.recommendation_session import (
    InvalidOptionValueError,
    QuestionAlreadyAnsweredError,
    RecommendationSessionManager,
    SessionNotFoundError,
    get_recommendation_session_manager,
)

# ============== helper ==============


def _make_settings(
    *,
    mock_ai_mode: Literal["normal", "slow", "invalid_json", "out_of_bounds_food_code"] = "normal",
    mock_ai_seed: int = 42,
) -> Settings:
    return Settings(
        app_env="test",
        app_mode="mock",
        poi_provider="mock",
        ai_provider="mock",
        ai_api_key="",
        ew_ai_key_passphrase="",
        ew_ai_salt="",
        mock_ai_mode=mock_ai_mode,
        mock_ai_seed=mock_ai_seed,
    )


def _make_rule_answers() -> QuestionnaireAnswers:
    """最小合法 QuestionnaireAnswers（供规则引擎回退和 AI prompt 摘要）。"""
    from app.schemas.enums import BudgetTier, MealPeriod, Taste

    return QuestionnaireAnswers(
        meal_period=MealPeriod.LUNCH,
        tastes=[Taste.SPICY],
        budget=BudgetTier.FROM_20_TO_30,
    )


# ============== 1. create/get 会话 ==============


def test_create_and_get_session_round_trip() -> None:
    mgr: RecommendationSessionManager = RecommendationSessionManager(settings=_make_settings())
    s = mgr.create_session(
        questionnaire_answers_by_qid={"q01": ["lunch"]},
        questionnaire_version="v1.0",
        dictionary_version="v1.0",
        rule_answers=_make_rule_answers(),
    )
    assert s.round_index_1based_next == 1
    assert s.follow_up_history == []
    assert s.stage == "follow_up"
    got = mgr.get_session(s.session_id)
    assert got.session_id == s.session_id
    # last_active_at 被 get_session 更新
    assert got.last_active_at >= s.started_at


def test_get_unknown_session_raises_session_not_found() -> None:
    mgr = RecommendationSessionManager(settings=_make_settings())
    with pytest.raises(SessionNotFoundError):
        mgr.get_session("no-such-session-deadbeef")


# ============== 2. start → 第 1 道追问题 ==============


@pytest.mark.anyio
async def test_start_and_get_next_returns_first_question_template_or_ai() -> None:
    mgr = RecommendationSessionManager(settings=_make_settings())
    s = mgr.create_session(
        questionnaire_answers_by_qid={},
        questionnaire_version="v1.0",
        dictionary_version="v1.0",
        rule_answers=_make_rule_answers(),
    )
    q = await mgr.start_and_get_next(session=s)
    assert q is not None
    assert 2 <= len(q.options) <= 6
    values = [o.value for o in q.options]
    assert len(values) == len(set(values))


# ============== 3. answer 三轮推进 → 最后停 ==============


@pytest.mark.anyio
async def test_answer_three_rounds_advances_correctly_then_final() -> None:
    from app.schemas.enums import BudgetTier, MealPeriod

    mgr = RecommendationSessionManager(settings=_make_settings())
    repo = get_food_dictionary_repository()
    # 用一个"七维几乎未覆盖"的 rule_answers（不含 tastes），保证三轮都能问，
    # 否则已覆盖维度会被 _pick_fallback_template / _question_is_redundant 跳过，
    # 导致三轮推进的意图失效。
    rule = QuestionnaireAnswers(
        meal_period=MealPeriod.LUNCH,
        budget=BudgetTier.FROM_20_TO_30,
    )
    s = mgr.create_session(
        questionnaire_answers_by_qid={},
        questionnaire_version="v1.0",
        dictionary_version="v1.0",
        rule_answers=rule,
    )
    t1 = FOLLOW_UP_TEMPLATES[0]
    await mgr.start_and_get_next(session=s)
    q2 = await mgr.answer_and_advance(
        session=s,
        question_id=t1.question_id,
        selected_option_value=t1.options[0].value,
        repo=repo,
    )
    assert s.round_index_1based_next == 2
    assert len(s.follow_up_history) == 1
    assert q2 is not None
    t2 = FOLLOW_UP_TEMPLATES[1]
    await mgr.answer_and_advance(  # q3 不再使用，断言 round 即可
        session=s,
        question_id=t2.question_id,
        selected_option_value=t2.options[0].value,
        repo=repo,
    )
    assert s.round_index_1based_next == 3
    t3 = FOLLOW_UP_TEMPLATES[2]
    final_q = await mgr.answer_and_advance(
        session=s,
        question_id=t3.question_id,
        selected_option_value=t3.options[0].value,
        repo=repo,
    )
    assert final_q is None
    assert len(s.follow_up_history) == 3


# ============== 4. 幂等：同一题不能答两次（409）==============


@pytest.mark.anyio
async def test_duplicate_answer_same_round_raises_already_answered() -> None:
    mgr = RecommendationSessionManager(settings=_make_settings())
    repo = get_food_dictionary_repository()
    s = mgr.create_session(
        questionnaire_answers_by_qid={},
        questionnaire_version="v1.0",
        dictionary_version="v1.0",
        rule_answers=_make_rule_answers(),
    )
    t1 = FOLLOW_UP_TEMPLATES[0]
    await mgr.start_and_get_next(session=s)
    await mgr.answer_and_advance(
        session=s,
        question_id=t1.question_id,
        selected_option_value=t1.options[0].value,
        repo=repo,
    )
    with pytest.raises(QuestionAlreadyAnsweredError):
        await mgr.answer_and_advance(
            session=s,
            question_id=t1.question_id,
            selected_option_value=t1.options[1].value,
            repo=repo,
        )


# ============== 5. InvalidOptionValueError：轮次错 / value 错 ==============


@pytest.mark.anyio
async def test_answer_wrong_question_id_raises_invalid_option_value() -> None:
    mgr = RecommendationSessionManager(settings=_make_settings())
    repo = get_food_dictionary_repository()
    s = mgr.create_session(
        questionnaire_answers_by_qid={},
        questionnaire_version="v1.0",
        dictionary_version="v1.0",
        rule_answers=_make_rule_answers(),
    )
    await mgr.start_and_get_next(session=s)
    wrong_qid = FOLLOW_UP_TEMPLATES[1].question_id
    with pytest.raises(InvalidOptionValueError, match="round mismatch"):
        await mgr.answer_and_advance(
            session=s,
            question_id=wrong_qid,
            selected_option_value="any",
            repo=repo,
        )


@pytest.mark.anyio
async def test_answer_invalid_option_value_raises_invalid_option_value() -> None:
    mgr = RecommendationSessionManager(settings=_make_settings())
    repo = get_food_dictionary_repository()
    s = mgr.create_session(
        questionnaire_answers_by_qid={},
        questionnaire_version="v1.0",
        dictionary_version="v1.0",
        rule_answers=_make_rule_answers(),
    )
    t1 = FOLLOW_UP_TEMPLATES[0]
    await mgr.start_and_get_next(session=s)
    with pytest.raises(InvalidOptionValueError, match="不在合法集合"):
        await mgr.answer_and_advance(
            session=s,
            question_id=t1.question_id,
            selected_option_value="definitely_not_an_option_value",
            repo=repo,
        )


# ============== 6+7. AI 增益 finalize + 失败回退 ==============


@pytest.mark.anyio
async def test_try_ai_finalize_success_generation_mode_ai() -> None:
    settings = _make_settings()
    chat = ChatService(settings=settings)
    mgr = RecommendationSessionManager(settings=settings, chat_service=chat)
    repo = get_food_dictionary_repository()
    s = mgr.create_session(
        questionnaire_answers_by_qid={},
        questionnaire_version="v1.0",
        dictionary_version="v1.0",
        generation_mode="ai",  # 需要走 AI 增益链路（V1 默认 rule 会直接短路）
        rule_answers=_make_rule_answers(),
    )
    items = await mgr.try_ai_finalize_recommendation(session=s, repo=repo)
    assert len(items) == 5
    assert s.final_reason == "ai_gain"
    for it in items:
        assert it.generation_mode == GenerationMode.AI
        assert it.source_type == SourceType.AI_RECOMMENDED
    assert sorted(it.priority for it in items) == [1, 2, 3, 4, 5]


@pytest.mark.anyio
async def test_try_ai_finalize_fail_fallback_to_rule_engine() -> None:
    settings = _make_settings(mock_ai_mode="invalid_json", mock_ai_seed=0)
    bad_chat = ChatService(settings=settings)
    mgr = RecommendationSessionManager(settings=settings, chat_service=bad_chat)
    repo = get_food_dictionary_repository()
    s = mgr.create_session(
        questionnaire_answers_by_qid={},
        questionnaire_version="v1.0",
        dictionary_version="v1.0",
        generation_mode="ai",  # 需要走 AI 增益链路（V1 默认 rule 会直接短路）
        rule_answers=_make_rule_answers(),
    )
    items = await mgr.try_ai_finalize_recommendation(session=s, repo=repo)
    assert len(items) == 5
    # mock_ai_mode=invalid_json → schema 校验失败 → 细分码 schema
    assert s.final_reason == "rule_engine_fallback_ai_schema"
    for it in items:
        assert it.generation_mode == GenerationMode.RULE


# ============== 8. 会话过期（TTL） ==============


def test_session_expired_by_ttl_raises_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    mgr = RecommendationSessionManager(settings=_make_settings())
    s = mgr.create_session(
        questionnaire_answers_by_qid={},
        questionnaire_version="v1.0",
        dictionary_version="v1.0",
        rule_answers=_make_rule_answers(),
    )
    from app.services import recommendation_session as rs_mod

    future = s.last_active_at + rs_mod.SESSION_TTL_SECONDS + 1
    monkeypatch.setattr(time, "monotonic", lambda: future)
    with pytest.raises(SessionNotFoundError):
        mgr.get_session(s.session_id)
    assert s.session_id not in mgr._sessions


# ============== 9. 单例一致性 ==============


def test_get_recommendation_session_manager_same_settings_returns_singleton() -> None:
    s1 = _make_settings()
    m1 = get_recommendation_session_manager(s1)
    m2 = get_recommendation_session_manager(s1)
    assert m1 is m2


# ============== 10. finalize 幂等 ==============


def test_finalize_recommendation_is_idempotent_same_list() -> None:
    mgr = RecommendationSessionManager(settings=_make_settings())
    repo = get_food_dictionary_repository()
    s = mgr.create_session(
        questionnaire_answers_by_qid={},
        questionnaire_version="v1.0",
        dictionary_version="v1.0",
        rule_answers=_make_rule_answers(),
    )
    items1 = mgr.finalize_recommendation(session=s, repo=repo)
    items2 = mgr.finalize_recommendation(session=s, repo=repo)
    assert [it.food_code for it in items1] == [it.food_code for it in items2]
    # V1 默认纯规则路径 → legacy_rule_engine（rule_engine_fallback_empty_ai 仅旧回退语义）
    assert s.final_reason == "legacy_rule_engine"


# ============== 11. AI slow 模式 → ChatService timeout 回退 ==============


@pytest.mark.anyio
async def test_slow_ai_triggers_timeout_fallback_via_chat_service() -> None:
    settings = _make_settings(mock_ai_mode="slow", mock_ai_seed=0)
    # 显式短超时（500ms），确保 mock slow（16s 延迟）必定触发 asyncio.timeout 回退，
    # 不受配置里 ai_timeout_ms（默认 30s）变化影响。
    slow_chat = ChatService(settings=settings, default_timeout_ms=500)
    repo = get_food_dictionary_repository()
    out = await slow_chat.generate_final_recommendation(
        system_prompt="sys", user_prompt="[FINAL_GENERATION] user"
    )
    assert out is None
    mgr = RecommendationSessionManager(settings=settings, chat_service=slow_chat)
    s = mgr.create_session(
        questionnaire_answers_by_qid={},
        questionnaire_version="v1.0",
        dictionary_version="v1.0",
        generation_mode="ai",  # 需要走 AI 增益链路（V1 默认 rule 会直接短路）
        rule_answers=_make_rule_answers(),
    )
    items = await mgr.try_ai_finalize_recommendation(session=s, repo=repo)
    assert len(items) == 5
    # mock_ai_mode=slow → timeout → 细分码 timeout
    assert s.final_reason == "rule_engine_fallback_ai_timeout"
