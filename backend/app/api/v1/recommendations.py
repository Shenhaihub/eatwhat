"""问卷答案直接生成推荐。

P2-04 交付物：POST /api/v1/recommendations。
P5-02 新增动态追问会话：
    POST /api/v1/recommendations/session/start
    GET  /api/v1/recommendations/session/{session_id}
    POST /api/v1/recommendations/session/{session_id}/answer
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Annotated, Any

from fastapi import APIRouter, Depends, Path, Request
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

if TYPE_CHECKING:
    from app.services.recommendation_session import RecommendationSession

from app.api.v1.auth import CurrentUser, get_current_user_optional
from app.api.v1.history import HistoryWriteRequest, write_user_recommendation
from app.core.config import Settings, get_settings
from app.core.exceptions import BAD_REQUEST, CONFLICT, INTERNAL_ERROR, NOT_FOUND, AppError
from app.core.supabase_client import SupabaseAdminClient, get_supabase_admin
from app.repositories.food_dictionary import (
    DEFAULT_DICTIONARY_VERSION,
    get_food_dictionary_repository,
)
from app.schemas import RecommendationItem
from app.schemas.ai import FollowUpQuestionOutput
from app.schemas.questionnaire import QuestionBankV1
from app.services.questionnaire_state import load_question_bank
from app.services.questionnaire_to_rule import questionnaire_answers_by_qid_to_rule_input
from app.services.recommendation_session import (
    InvalidOptionValueError,
    QuestionAlreadyAnsweredError,
    SessionNotFoundError,
    get_recommendation_session_manager,
)
from app.services.rule_engine import generate_rule_recommendations

# 与 questionnaire.py 的 version pattern 保持一致（避免在 regex 字符串里交叉引用）
QUESTIONNAIRE_VERSION_PATTERN = r"^v[0-9]+\.[0-9]+$"
# 字典版本正则（与 v1.0 兼容）
_DICT_VERSION_PATTERN = r"^v[0-9]+\.[0-9]+$"

router = APIRouter(prefix="/api/v1/recommendations", tags=["recommendations"])


# ============== 请求 schema ==============


class RecommendationsGenerateRequestV1(BaseModel):
    """P2-04 推荐生成请求体。严格 G-07：extra=forbid。"""

    model_config = ConfigDict(extra="forbid")

    # P2 阶段仅实现 ai_recommend；其他入口在 P3/P4 接入
    entry_intent: str = Field(..., pattern=r"^(ai_recommend|community|activity|user_choice)$")
    questionnaire_version: str = Field(..., pattern=QUESTIONNAIRE_VERSION_PATTERN)
    # 复用 `/questionnaire/next` 的形状；通过 Pydantic 约束正则长度
    answers_by_question_id: dict[str, list[str]] = Field(default_factory=dict)
    dictionary_version: str | None = Field(
        default=None,
        pattern=_DICT_VERSION_PATTERN,
        description="不传 = 食物字典默认版本（当前 v1.0）",
    )

    @model_validator(mode="after")
    def _validate_answers_shapes(self) -> RecommendationsGenerateRequestV1:
        """answers_by_question_id 形状校验（与 QuestionnaireNextRequestV1 保持一致，G-09）。"""
        import re

        qid_re = re.compile(r"^[a-z0-9_]{2,40}$")
        for qid, vals in self.answers_by_question_id.items():
            if not qid_re.match(qid):
                raise ValueError(
                    f"answers_by_question_id key={qid!r} not match "
                    f"question_id pattern ^[a-z0-9_]{{2,40}}$ (G-09)"
                )
            if not isinstance(vals, list):  # pragma: no cover - Pydantic 先拦
                raise TypeError(f"answers_by_question_id[{qid!r}] must be a list")
            for idx, v in enumerate(vals):
                if not isinstance(v, str):  # pragma: no cover
                    raise TypeError(f"answers_by_question_id[{qid!r}][{idx}] must be str")
                if len(v) < 1 or len(v) > 32:
                    raise ValueError(
                        f"answers_by_question_id[{qid!r}][{idx}] length out of "
                        f"range [1,32]: {len(v)} (G-09)"
                    )
        return self


# ============== G-07：递归查 source_type ==============


def _find_source_type_keys(obj: Any, *, path_prefix: str = "body") -> list[str]:
    hits: list[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            current = f"{path_prefix}.{k}"
            if k == "source_type":
                hits.append(current)
            hits.extend(_find_source_type_keys(v, path_prefix=current))
    elif isinstance(obj, list):
        for idx, v in enumerate(obj):
            hits.extend(_find_source_type_keys(v, path_prefix=f"{path_prefix}[{idx}]"))
    return hits


# ============== 路由 ==============


@router.post("", response_model=list[RecommendationItem])
async def recommendations_generate(
    request: Request,
    current_user: Annotated[CurrentUser | None, Depends(get_current_user_optional)],
    sb: Annotated[SupabaseAdminClient | None, Depends(get_supabase_admin)] = None,
    settings: Annotated[Settings | None, Depends(get_settings)] = None,
) -> list[dict[str, Any]]:
    # 0) 原始 JSON → 先 G-07 再 Pydantic
    try:
        raw_body: Any = await request.json()
    except json.JSONDecodeError:
        raw_body = {}
    except Exception:  # noqa: BLE001
        raw_body = {}

    source_type_paths = _find_source_type_keys(raw_body)
    if source_type_paths:
        raise AppError(
            code=BAD_REQUEST,
            status_code=400,
            message="请求体不得携带 source_type（来源必须由服务端派生）",
            details={"detected_keys": source_type_paths, "g_rule": "G-07"},
        )

    if not isinstance(raw_body, dict):
        raise RequestValidationError(
            errors=[
                {
                    "type": "value_error",
                    "loc": ("body",),
                    "msg": "请求体必须是 JSON 对象",
                    "input": raw_body,
                }
            ]
        )
    try:
        payload = RecommendationsGenerateRequestV1.model_validate(raw_body)
    except ValidationError as exc:
        raise RequestValidationError(errors=exc.errors(include_url=False)) from exc

    # P2 阶段只支持 entry_intent=ai_recommend；其他走 P3/P4
    if payload.entry_intent != "ai_recommend":
        raise AppError(
            code=BAD_REQUEST,
            status_code=400,
            message=(
                f"entry_intent={payload.entry_intent!r} 暂未接入推荐生成，"
                "P2 阶段仅支持 ai_recommend"
            ),
            details={"supported_entry_intents": ["ai_recommend"], "hint": "P3 接入其他入口"},
        )

    # 1) 加载题库（用于把 qid 映射到七维字段）
    try:
        bank: QuestionBankV1 = load_question_bank(
            questionnaire_version=payload.questionnaire_version
        )
    except FileNotFoundError as exc:
        raise AppError(
            code=NOT_FOUND,
            status_code=404,
            message=f"不存在的问卷版本：{payload.questionnaire_version}（当前仅提供 v1.0）",
            details={"hint": str(exc)},
        ) from exc
    except ValueError as exc:
        raise AppError(
            code=INTERNAL_ERROR,
            status_code=500,
            message="问卷库加载失败，请联系维护者",
            details={"reason": str(exc)},
        ) from exc

    # 2) 加载食物字典（用于规则引擎回退池 G-08）
    dict_version = payload.dictionary_version or DEFAULT_DICTIONARY_VERSION
    try:
        get_food_dictionary_repository.cache_clear()
        repo = get_food_dictionary_repository(dict_version)
    except FileNotFoundError as exc:
        raise AppError(
            code=NOT_FOUND,
            status_code=404,
            message=f"不存在的食物字典版本：{dict_version}（当前仅提供 v1.0）",
            details={"hint": str(exc)},
        ) from exc
    except ValueError as exc:
        raise AppError(
            code=INTERNAL_ERROR,
            status_code=500,
            message="食物字典加载失败，请联系维护者",
            details={"reason": str(exc)},
        ) from exc

    # 3) answers_by_question_id → QuestionnaireAnswers（纯映射，无启发式）
    rule_answers = questionnaire_answers_by_qid_to_rule_input(
        bank=bank,
        answers_by_question_id=payload.answers_by_question_id,
        questionnaire_version=payload.questionnaire_version,
    )

    # 4) 规则引擎确定性生成 5 条（G-08：任何合法输入都返回正好 5）
    try:
        items = generate_rule_recommendations(rule_answers, repo=repo)
    except ValueError as exc:
        raise AppError(
            code=INTERNAL_ERROR,
            status_code=500,
            message="推荐生成失败，请稍后再试",
            details={"reason": str(exc)},
        ) from exc

    # 5) 返回正好 5 条（response_model=list[RecommendationItem] 会再兜底校验长度/字段）
    result = [i.model_dump() for i in items]

    # 6) 登录态下自动入库历史（失败静默不影响返回值；单条日志记 warning）
    if current_user is not None and sb is not None:
        try:
            # 从 answers_by_question_id 里尽量提取 food_code
            food_code = _extract_food_code(payload.answers_by_question_id)
            # 从 answers 里提取 tags（多选型答案）
            tags = _extract_tags(payload.answers_by_question_id)
            snap = {
                "entry_intent": payload.entry_intent,
                "questionnaire_version": payload.questionnaire_version,
                "dictionary_version": dict_version,
                "items": result,
            }
            write_user_recommendation(
                sb=sb,
                user=current_user,
                payload=HistoryWriteRequest(
                    food_code=food_code,
                    location=None,
                    radius_meters=None,
                    tags=tags,
                    recommendation_snapshot=snap,
                    result_count=len(result),
                    poi_provider=None,
                ),
            )
        except Exception as exc:  # noqa: BLE001 - 不阻塞主流程
            log = logging.getLogger("app.api.v1.recommendations")
            log.warning("history_autowrite_failed user=%s err=%r", current_user.user_id, exc)

    return result


def _extract_food_code(answers_by_question_id: dict[str, list[str]]) -> str | None:
    """答案里找一个像 "菜系/类型" 的单选型问题当 food_code。目前 MVP：取 qid 含 cuisine/type 的第一个值。"""
    import re

    for qid, vals in answers_by_question_id.items():
        if re.search(r"(cuisine|type|food_type|cat)", qid) and vals:
            return vals[0]
    for vals in answers_by_question_id.values():
        if len(vals) == 1 and vals[0]:  # 单选题作为弱推断
            return vals[0]
    return None


def _extract_tags(answers_by_question_id: dict[str, list[str]]) -> list[str]:
    """把所有多选型答案（长度 >= 2）的选项值拼成 tags，便于后续检索历史。"""
    tags: list[str] = []
    for vals in answers_by_question_id.values():
        if isinstance(vals, list) and len(vals) >= 2:
            for v in vals:
                if isinstance(v, str) and v and v not in tags:
                    tags.append(v)
    return tags


# ============== P5-02 动态追问会话 Schemas ==============


class SessionAnswerRequestV1(BaseModel):
    """POST /session/{session_id}/answer 请求体。严格 G-07。"""
    model_config = ConfigDict(extra="forbid")
    question_id: str = Field(..., min_length=4, max_length=32)
    selected_option_value: str = Field(..., min_length=1, max_length=32)


class SessionStateResponseV1(BaseModel):
    """会话统一响应体（start/get/answer 三路由都返回这个形状）。"""
    model_config = ConfigDict(extra="forbid")
    session_id: str
    stage: str = Field(..., pattern=r"^(follow_up|final)$")
    # stage=follow_up 时必有 question；final 时可为 null
    question: FollowUpQuestionOutput | None = None
    # 已答轮次进度（方便前端显示进度条 "1/3"）
    rounds_completed: int = Field(..., ge=0, le=3)
    max_rounds: int = Field(3, ge=3, le=3)
    # stage=final 时必有 candidates（长度正好 5，G-08 保障）
    candidates: list[RecommendationItem] | None = None
    # 说明 final 是来自 AI 增益还是规则引擎回退（供前端 trace/调试面板）
    final_reason: str | None = None


# ============== P5-02 复用的校验/加载小工具 ==============


def _validate_and_load_for_session_start(
    *,
    raw_body: Any,
) -> tuple[RecommendationsGenerateRequestV1, QuestionBankV1, Any]:
    """G-07 检查 → Pydantic 校验 → 加载题库 → 翻译 rule_answers。
    返回 (payload, bank, rule_answers)。任何异常 raise 上层转 AppError。
    """
    source_type_paths = _find_source_type_keys(raw_body)
    if source_type_paths:
        raise AppError(
            code=BAD_REQUEST,
            status_code=400,
            message="请求体不得携带 source_type（来源必须由服务端派生）",
            details={"detected_keys": source_type_paths, "g_rule": "G-07"},
        )
    if not isinstance(raw_body, dict):
        raise RequestValidationError(
            errors=[{"type": "value_error", "loc": ("body",),
                     "msg": "请求体必须是 JSON 对象", "input": raw_body}]
        )
    try:
        payload = RecommendationsGenerateRequestV1.model_validate(raw_body)
    except ValidationError as exc:
        raise RequestValidationError(errors=exc.errors(include_url=False)) from exc

    if payload.entry_intent != "ai_recommend":
        raise AppError(
            code=BAD_REQUEST,
            status_code=400,
            message=(
                f"entry_intent={payload.entry_intent!r} 暂未接入推荐生成，"
                "P2/P5 阶段仅支持 ai_recommend"
            ),
            details={"supported_entry_intents": ["ai_recommend"], "hint": "P3 接入其他入口"},
        )

    try:
        bank: QuestionBankV1 = load_question_bank(
            questionnaire_version=payload.questionnaire_version
        )
    except FileNotFoundError as exc:
        raise AppError(
            code=NOT_FOUND,
            status_code=404,
            message=f"不存在的问卷版本：{payload.questionnaire_version}（当前仅提供 v1.0）",
            details={"hint": str(exc)},
        ) from exc
    except ValueError as exc:
        raise AppError(
            code=INTERNAL_ERROR,
            status_code=500,
            message="问卷库加载失败，请联系维护者",
            details={"reason": str(exc)},
        ) from exc

    rule_answers = questionnaire_answers_by_qid_to_rule_input(
        bank=bank,
        answers_by_question_id=payload.answers_by_question_id,
        questionnaire_version=payload.questionnaire_version,
    )
    return payload, bank, rule_answers


def _try_autowrite_history_if_user(
    *,
    current_user: CurrentUser | None,
    sb: SupabaseAdminClient | None,
    payload: RecommendationsGenerateRequestV1,
    dict_version: str,
    items: list[RecommendationItem],
) -> None:
    """登录态下把最终推荐结果写入历史；失败静默日志，不阻塞主流程。"""
    if current_user is None or sb is None:
        return
    try:
        food_code = _extract_food_code(payload.answers_by_question_id)
        tags = _extract_tags(payload.answers_by_question_id)
        snap = {
            "entry_intent": payload.entry_intent,
            "questionnaire_version": payload.questionnaire_version,
            "dictionary_version": dict_version,
            "items": [i.model_dump() for i in items],
        }
        write_user_recommendation(
            sb=sb,
            user=current_user,
            payload=HistoryWriteRequest(
                food_code=food_code,
                location=None,
                radius_meters=None,
                tags=tags,
                recommendation_snapshot=snap,
                result_count=len(items),
                poi_provider=None,
            ),
        )
    except Exception as exc:  # noqa: BLE001 - 不阻塞主流程
        log = logging.getLogger("app.api.v1.recommendations")
        log.warning("history_autowrite_failed user=%s err=%r", current_user.user_id, exc)


def _session_to_state_response(session: RecommendationSession) -> SessionStateResponseV1:
    candidates_dump: list[RecommendationItem] | None = None
    if session.final_items is not None:
        candidates_dump = list(session.final_items)
    return SessionStateResponseV1(
        session_id=session.session_id,
        stage=session.stage,
        question=None,  # 外层按需填
        rounds_completed=len(session.follow_up_history),
        max_rounds=3,
        candidates=candidates_dump,
        final_reason=session.final_reason,
    )


# ============== P5-02 路由 ==============


@router.post("/session/start", response_model=SessionStateResponseV1)
async def recommendations_session_start(
    request: Request,
    current_user: Annotated[CurrentUser | None, Depends(get_current_user_optional)],
    sb: Annotated[SupabaseAdminClient | None, Depends(get_supabase_admin)] = None,
    settings: Annotated[Settings | None, Depends(get_settings)] = None,
) -> SessionStateResponseV1:
    # 0) 原始 JSON 解析（与 POST "" 完全一致的防御路径）
    try:
        raw_body: Any = await request.json()
    except json.JSONDecodeError:
        raw_body = {}
    except Exception:  # noqa: BLE001
        raw_body = {}

    payload, _bank, rule_answers = _validate_and_load_for_session_start(raw_body=raw_body)
    dict_version = payload.dictionary_version or DEFAULT_DICTIONARY_VERSION
    try:
        get_food_dictionary_repository.cache_clear()
        repo = get_food_dictionary_repository(dict_version)
    except FileNotFoundError as exc:
        raise AppError(
            code=NOT_FOUND,
            status_code=404,
            message=f"不存在的食物字典版本：{dict_version}（当前仅提供 v1.0）",
            details={"hint": str(exc)},
        ) from exc
    except ValueError as exc:
        raise AppError(
            code=INTERNAL_ERROR,
            status_code=500,
            message="食物字典加载失败，请联系维护者",
            details={"reason": str(exc)},
        ) from exc

    assert settings is not None  # Depends(get_settings) 保证非空；防御 mypy
    # 1) 创建会话 + 尝试生成第 1 道 AI 追问
    mgr = get_recommendation_session_manager(settings)
    uid = current_user.user_id if current_user is not None else None
    session = mgr.create_session(
        user_id=uid,
        questionnaire_answers_by_qid=payload.answers_by_question_id,
        questionnaire_version=payload.questionnaire_version,
        dictionary_version=dict_version,
        rule_answers=rule_answers,
    )
    next_q = await mgr.start_and_get_next(session=session)

    # 2) 若 AI 明确认为信息已充分（返回 None）或已经 3 轮，则直接 final
    if next_q is None or session.round_index_1based_next > 3:
        items = await mgr.try_ai_finalize_recommendation(session=session, repo=repo)
        _try_autowrite_history_if_user(
            current_user=current_user, sb=sb, payload=payload,
            dict_version=dict_version, items=items,
        )
        resp = _session_to_state_response(session)
        resp.question = None
        return resp

    resp = _session_to_state_response(session)
    resp.question = next_q
    return resp


@router.get("/session/{session_id}", response_model=SessionStateResponseV1)
async def recommendations_session_get(
    session_id: Annotated[str, Path(min_length=8, max_length=64)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> SessionStateResponseV1:
    mgr = get_recommendation_session_manager(settings)
    try:
        session = mgr.get_session(session_id)
    except SessionNotFoundError as exc:
        raise AppError(
            code=NOT_FOUND,
            status_code=404,
            message="会话不存在或已过期（超过 15 分钟未操作），请重新开始",
            details={"session_id_prefix": str(exc)[:10] + "…"},
        ) from exc
    resp = _session_to_state_response(session)
    if session.stage == "follow_up" and 1 <= session.round_index_1based_next <= 3:
        # 查询接口给一个默认题显示（幂等：与 ChatService 当时可能生成的题不一定一一对应，
        # 但保证前端有可交互的内容）
        from app.services.ai.mock_provider import FOLLOW_UP_TEMPLATES
        resp.question = FOLLOW_UP_TEMPLATES[session.round_index_1based_next - 1]
    return resp


@router.post("/session/{session_id}/answer", response_model=SessionStateResponseV1)
async def recommendations_session_answer(
    request: Request,
    session_id: Annotated[str, Path(min_length=8, max_length=64)],
    current_user: Annotated[CurrentUser | None, Depends(get_current_user_optional)],
    sb: Annotated[SupabaseAdminClient | None, Depends(get_supabase_admin)] = None,
    settings: Annotated[Settings | None, Depends(get_settings)] = None,
) -> SessionStateResponseV1:
    # 0) 解析 body（带 G-07：answer 请求体里也不能夹带 source_type）
    try:
        raw_body: Any = await request.json()
    except json.JSONDecodeError:
        raw_body = {}
    except Exception:  # noqa: BLE001
        raw_body = {}
    source_type_paths = _find_source_type_keys(raw_body)
    if source_type_paths:
        raise AppError(
            code=BAD_REQUEST,
            status_code=400,
            message="请求体不得携带 source_type（来源必须由服务端派生）",
            details={"detected_keys": source_type_paths, "g_rule": "G-07"},
        )
    if not isinstance(raw_body, dict):
        raise RequestValidationError(
            errors=[{"type": "value_error", "loc": ("body",),
                     "msg": "请求体必须是 JSON 对象", "input": raw_body}]
        )
    try:
        answer = SessionAnswerRequestV1.model_validate(raw_body)
    except ValidationError as exc:
        raise RequestValidationError(errors=exc.errors(include_url=False)) from exc

    assert settings is not None  # Depends(get_settings) 保证非空
    mgr = get_recommendation_session_manager(settings)
    try:
        session = mgr.get_session(session_id)
    except SessionNotFoundError as exc:
        raise AppError(
            code=NOT_FOUND,
            status_code=404,
            message="会话不存在或已过期（超过 15 分钟未操作），请重新开始",
            details={"session_id_prefix": str(exc)[:10] + "…"},
        ) from exc

    # 加载 repo（最后生成 final 需要它；答问阶段也要用 repo 校验对照默认题 value）
    dict_version = session.dictionary_version
    try:
        repo = get_food_dictionary_repository(dict_version)
    except FileNotFoundError as exc:
        raise AppError(
            code=NOT_FOUND,
            status_code=404,
            message=f"不存在的食物字典版本：{dict_version}",
            details={"hint": str(exc)},
        ) from exc
    except ValueError as exc:
        raise AppError(
            code=INTERNAL_ERROR,
            status_code=500,
            message="食物字典加载失败，请联系维护者",
            details={"reason": str(exc)},
        ) from exc

    try:
        next_q = await mgr.answer_and_advance(
            session=session,
            question_id=answer.question_id,
            selected_option_value=answer.selected_option_value,
            repo=repo,
        )
    except QuestionAlreadyAnsweredError as exc:
        raise AppError(
            code=CONFLICT,
            status_code=409,
            message="该追问题已答过（请勿重复提交或点击过快）",
            details={"conflict_question_id": str(exc)},
        ) from exc
    except InvalidOptionValueError as exc:
        raise AppError(
            code=BAD_REQUEST,
            status_code=400,
            message="答案不合法（选项值与当前轮次不匹配，或该题已进入下一阶段）",
            details={"reason": str(exc)},
        ) from exc

    # 1) 仍需追问 → 返回下一题
    if next_q is not None and session.round_index_1based_next <= 3:
        resp = _session_to_state_response(session)
        resp.question = next_q
        return resp

    # 2) 信息充分 → 生成最终 Top5（AI 增益 + 规则兜底）
    items = await mgr.try_ai_finalize_recommendation(session=session, repo=repo)
    # 写历史（登录态 + Supabase 已配）
    start_payload_like = RecommendationsGenerateRequestV1(
        entry_intent="ai_recommend",
        questionnaire_version=session.questionnaire_version,
        answers_by_question_id=session.questionnaire_answers_by_qid,
        dictionary_version=(
            None if session.dictionary_version == DEFAULT_DICTIONARY_VERSION
            else session.dictionary_version
        ),
    )
    _try_autowrite_history_if_user(
        current_user=current_user, sb=sb, payload=start_payload_like,
        dict_version=session.dictionary_version, items=items,
    )
    resp = _session_to_state_response(session)
    resp.question = None
    return resp
