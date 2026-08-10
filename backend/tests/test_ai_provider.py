"""P5-03 多 Provider 契约 + MockAIProvider 四模式 + ChatService 回退 + seed 参数化排序测试。"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.repositories.food_dictionary import (
    get_food_dictionary_repository,
)
from app.schemas.ai import FinalRecommendationOutput, FollowUpQuestionOutput
from app.services.ai.base import AIProvider, ChatMessage
from app.services.ai.mock_provider import (
    MockAIProvider,
    _build_invalid_json,
    _detect_follow_up_round,
    _is_final_generation_request,
    _seed_shuffled_five,
)
from app.services.ai.service import ChatService

# ============== Mock Provider：helper 纯函数 ==============

def test_is_final_generation_request_detects_both_system_and_user_markers():
    # user 带标记
    msgs = [ChatMessage(role="user", content="随便写 [FINAL_GENERATION] 继续")]
    assert _is_final_generation_request(msgs) is True
    # system 带标记
    msgs2 = [
        ChatMessage(role="system", content="[FINAL_GENERATION] 你是推荐助手"),
        ChatMessage(role="user", content="我要吃饭"),
    ]
    assert _is_final_generation_request(msgs2) is True
    # 都不带
    msgs3 = [ChatMessage(role="user", content="我要吃饭")]
    assert _is_final_generation_request(msgs3) is False


def test_detect_follow_up_round_supports_123_and_defaults_0():
    m1 = [ChatMessage(role="user", content="[ROUND_1] 第一轮")]
    m2 = [ChatMessage(role="system", content="bla [ROUND_2] foo")]
    m3 = [ChatMessage(role="user", content="bla [ROUND_3] end")]
    mnone = [ChatMessage(role="user", content="没有标记")]
    assert _detect_follow_up_round(m1) == 0
    assert _detect_follow_up_round(m2) == 1
    assert _detect_follow_up_round(m3) == 2
    assert _detect_follow_up_round(mnone) == 0


# ============== Mock Provider：normal 模式 ==============

@pytest.mark.anyio
async def test_mock_normal_final_5_candidates_all_in_dictionary():
    prov = MockAIProvider(mode="normal", seed=0)
    msgs = [ChatMessage(role="user", content="[FINAL_GENERATION] go")]
    raw = await prov.chat(messages=msgs, temperature=0.3, timeout_ms=1000)
    out = FinalRecommendationOutput.model_validate_json(raw)
    repo = get_food_dictionary_repository()
    assert len(out.candidates) == 5
    for c in out.candidates:
        assert repo.contains_enabled(c.food_code), f"越界 code: {c.food_code}"
    # 5 条互不相同
    assert len({c.food_code for c in out.candidates}) == 5


@pytest.mark.anyio
async def test_mock_normal_follow_up_3_rounds_produces_3_questions_and_last_stops():
    prov = MockAIProvider(mode="normal", seed=0)
    qs: list[FollowUpQuestionOutput] = []
    for r1based in (1, 2, 3):
        msgs = [ChatMessage(role="user", content=f"[ROUND_{r1based}] 继续")]
        raw = await prov.chat(messages=msgs, temperature=0.5, timeout_ms=1000)
        qs.append(FollowUpQuestionOutput.model_validate_json(raw))
    ids = [q.question_id for q in qs]
    # 3 轮 id 互不相同
    assert len(set(ids)) == 3
    # 第 3 题 should_continue=False
    assert qs[2].should_continue is False
    # 每道题 option 唯一值
    for q in qs:
        vals = [o.value for o in q.options]
        assert len(vals) == len(set(vals))
        assert 2 <= len(vals) <= 6


# ============== MEM-024：seed 参数化排序扰动 ==============

def test_seed_shuffled_five_different_seeds_produce_distinct_first_codes():
    firsts = [_seed_shuffled_five(seed=i)[0] for i in range(4)]
    # 4 个首候选至少有 2 个不同（反"固定首候选"Oracle）
    assert len(set(firsts)) >= 2, f"seed=0..3 首候选竟然相同: {firsts}"


@pytest.mark.anyio
async def test_seed_0_and_seed_1_produce_distinct_final_orderings():
    prov0 = MockAIProvider(mode="normal", seed=0)
    prov1 = MockAIProvider(mode="normal", seed=1)
    msgs = [ChatMessage(role="user", content="[FINAL_GENERATION]")]
    out0 = FinalRecommendationOutput.model_validate_json(
        await prov0.chat(messages=msgs, temperature=0.3, timeout_ms=1000)
    )
    out1 = FinalRecommendationOutput.model_validate_json(
        await prov1.chat(messages=msgs, temperature=0.3, timeout_ms=1000)
    )
    # 整个排序不同（至少有一条位置不同）——实际通常是 5 条几乎全不同
    assert [c.food_code for c in out0.candidates] != [c.food_code for c in out1.candidates]


# ============== Mock Provider：四种异常模式 ==============

@pytest.mark.anyio
async def test_mock_invalid_json_fails_model_validate():
    prov = MockAIProvider(mode="invalid_json", seed=0)
    msgs = [ChatMessage(role="user", content="[FINAL_GENERATION]")]
    raw = await prov.chat(messages=msgs, temperature=0.3, timeout_ms=1000)
    with pytest.raises(ValidationError):
        FinalRecommendationOutput.model_validate_json(raw)


def test_invalid_json_follow_up_is_also_invalid():
    raw = _build_invalid_json(is_final=False)
    with pytest.raises(ValidationError):
        FollowUpQuestionOutput.model_validate_json(raw)


@pytest.mark.anyio
async def test_mock_out_of_bounds_food_code_is_rejected_by_schema_validator():
    prov = MockAIProvider(mode="out_of_bounds_food_code", seed=0)
    msgs = [ChatMessage(role="user", content="[FINAL_GENERATION]")]
    raw = await prov.chat(messages=msgs, temperature=0.3, timeout_ms=1000)
    # Pydantic 的 field_validator 在 model_validate_json 阶段就会抛出
    with pytest.raises(ValidationError, match="不在启用字典中"):
        FinalRecommendationOutput.model_validate_json(raw)


# ============== ChatService：回退逻辑（核心契约）==============

def _base_settings() -> Settings:
    return Settings(
        _env_file=None,
        app_env="test",
        app_mode="mock",
        ai_provider="mock",
        ai_api_key="",
        ew_ai_key_passphrase="",
        ew_ai_salt="",
        mock_ai_mode="normal",
        mock_ai_seed=0,
        database_url="sqlite+aiosqlite:///:memory:",
        supabase_url="",
        supabase_anon_key="",
        supabase_service_role_key="",
    )


@pytest.mark.anyio
async def test_chat_service_final_with_normal_override_returns_valid_output():
    svc = ChatService(
        settings=_base_settings(),
        provider_override=MockAIProvider(mode="normal", seed=2),
    )
    out = await svc.generate_final_recommendation(
        system_prompt="你是吃什么推荐助手", user_prompt="用户想吃清淡的"
    )
    assert out is not None
    assert isinstance(out, FinalRecommendationOutput)
    assert len(out.candidates) == 5


@pytest.mark.anyio
async def test_chat_service_invalid_json_fallback_none():
    svc = ChatService(
        settings=_base_settings(),
        provider_override=MockAIProvider(mode="invalid_json"),
    )
    out = await svc.generate_final_recommendation(
        system_prompt="", user_prompt="whatever"
    )
    assert out is None  # 回退规则引擎


@pytest.mark.anyio
async def test_chat_service_out_of_bounds_food_code_fallback_none():
    svc = ChatService(
        settings=_base_settings(),
        provider_override=MockAIProvider(mode="out_of_bounds_food_code"),
    )
    out = await svc.generate_final_recommendation(
        system_prompt="", user_prompt="whatever"
    )
    assert out is None  # 越界 food_code 被 schema validator 拦截 → 判失败


@pytest.mark.anyio
async def test_chat_service_slow_with_short_timeout_fallback_none():
    svc = ChatService(
        settings=_base_settings(),
        provider_override=MockAIProvider(mode="slow", slow_delay_seconds=2),
        default_timeout_ms=200,  # 硬卡 200ms → 必定超时
    )
    out = await svc.generate_final_recommendation(
        system_prompt="", user_prompt="whatever"
    )
    assert out is None  # 超时 → 回退规则


@pytest.mark.anyio
async def test_chat_service_follow_up_round_oob_4_returns_none_info_sufficient():
    svc = ChatService(
        settings=_base_settings(),
        provider_override=MockAIProvider(mode="normal"),
    )
    out = await svc.generate_follow_up(
        system_prompt="", user_prompt="bla", round_index_1based=4
    )
    assert out is None  # 超出 3 轮 → 判失败，业务层直接生成最终推荐


# ============== ChatService：Provider 选择（AI_PROVIDER 分支）==============

def test_chat_service_build_provider_deepseek_empty_key_raises_value_error():
    cfg = _base_settings().model_copy(
        update={"ai_provider": "deepseek", "ai_api_key": ""}
    )
    svc = ChatService(settings=cfg)
    with pytest.raises(ValueError, match="AI_API_KEY 未配置"):
        svc._build_provider()


def test_chat_service_build_provider_auto_empty_key_falls_back_to_mock_protocol():
    cfg = _base_settings().model_copy(
        update={"ai_provider": "auto", "ai_api_key": ""}
    )
    svc = ChatService(settings=cfg)
    prov = svc._build_provider()
    # 必须是 runtime_checkable Protocol `AIProvider` 的实现
    assert isinstance(prov, AIProvider)
    # 具体是 MockAIProvider
    assert isinstance(prov, MockAIProvider)


# ============== Settings：mock_ai_seed 边界 fail-fast ==============

def test_settings_mock_ai_seed_negative_raises_validation_error():
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            mock_ai_seed=-1,
        )


def test_settings_mock_ai_seed_10001_raises_validation_error():
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            mock_ai_seed=10_001,
        )


def test_settings_default_ai_model_is_deepseek_v4_flash():
    s = Settings(_env_file=None)
    assert s.ai_model == "deepseek-v4-flash"
    assert s.ai_provider == "mock"
    assert s.mock_ai_mode == "normal"
    assert s.mock_ai_seed == 0
