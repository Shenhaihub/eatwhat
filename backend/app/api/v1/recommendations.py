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
from typing import TYPE_CHECKING, Annotated, Any, NamedTuple
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Request
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

if TYPE_CHECKING:
    from app.services.recommendation_session import RecommendationSession

from app.api.v1.auth import CurrentUser, get_current_user_optional
from app.api.v1.history import HistoryWriteRequest, write_user_recommendation
from app.api.v1.preferences import PreferenceWriteRequest, write_user_preference_snapshot
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
    prefer_ai_gain: bool = Field(
        default=False,
        description=(
            "用户是否希望使用 AI 优化推荐（G-07：这是「用户偏好指示」，"
            "最终 generation_mode 由后端派生，客户端不直接指定）。"
            "True：已登录 → 走 AI 增益链路（每天 3 次额度）；未登录 → 401。"
            "False（默认）→ 走免费的确定性规则引擎，不扣额度，不要求登录。"
        ),
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


class DirectRecommendationsResponseV1(BaseModel):
    """POST /api/v1/recommendations 的响应（P7-07：附加冷启动画像合并明细）。"""
    model_config = ConfigDict(extra="forbid")
    # 正好 5 条推荐结果（G-08 兜底保障）
    items: list[RecommendationItem]
    # P7-07：冷启动画像合并实际改变的 answers 字段；未登录/无画像命中时为空数组
    merged_pref_fields: list[dict[str, Any]] = Field(default_factory=list)
    # P0 修复：自动写入结果（前端据此显示 "已自动保存" / "手动保存" 按钮）
    autowrite: dict[str, Any] = Field(default_factory=dict)
    # P5-04A：是否真正走了 AI 增益链路（= final_reason == 'ai_gain'）；
    # 若 prefer_ai_gain=True 但 final_reason 落入 rule_engine_fallback_*，则 used_ai=False
    used_ai: bool = False
    # P5-07：今日 AI 额度使用情况（prefer_ai_gain=False / 未登录 → user_used 为 0）
    #   {user_used, user_limit(默认 3), global_used, global_limit(默认 100)}
    ai_quota: dict[str, int] = Field(default_factory=dict)
    # P5-04A：前端展示给用户看的 final_reason（来源徽章颜色的真源）
    final_reason: str | None = None


# ============== 路由 ==============


@router.post("", response_model=DirectRecommendationsResponseV1)
async def recommendations_generate(
    request: Request,
    current_user: Annotated[CurrentUser | None, Depends(get_current_user_optional)],
    sb: Annotated[SupabaseAdminClient | None, Depends(get_supabase_admin)] = None,
    settings: Annotated[Settings | None, Depends(get_settings)] = None,
) -> dict[str, Any]:
    assert settings is not None  # Depends(get_settings) 保证非空

    # 0) 原始 JSON → 先 G-07 再 Pydantic
    try:
        raw_body: Any = await request.json()
    except json.JSONDecodeError:
        raw_body = {}
    except Exception:
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

    # 0b) P5-04：AI 增益登录前置（只有用户主动开启 AI 优化才需要登录；
    # 默认 prefer_ai_gain=False 不要求登录，和之前行为一致。
    # G-07：generation_mode='ai' 只有服务端在登录态 + prefer_ai_gain=True 才派生。
    derived_generation_mode: str = "rule"  # G-07：真源永远在后端派生
    quota_user_id: str | None = None
    if payload.prefer_ai_gain:
        if current_user is None:
            from fastapi import HTTPException, status
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=(
                    "「AI 优化推荐」需要先登录后使用（每天 3 次额度）；"
                    "未登录可使用免费的确定性规则引擎推荐（默认模式，开关关闭即可）。"
                ),
            )
        derived_generation_mode = "ai"
        quota_user_id = current_user.user_id

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

    # 3b) P6-02：冷启动画像合并——用最近 3 次偏好快照填充用户未回答的字段（当前答案优先）
    merged_fields_direct = _try_merge_recent_preferences_into_answers(
        current_user=current_user,
        sb=sb,
        answers=rule_answers,
    )

    # 4) 派生 generation_mode 对应的生成逻辑
    mgr = get_recommendation_session_manager(settings)
    final_reason: str | None = None

    if derived_generation_mode == "ai":
        # AI 模式：创建一次性会话（无 AI 追问题，直接 final generate；因为
        # 推荐结果页目前是单步生成而非动态追问+单步问答的流程）
        assert current_user is not None
        session = mgr.create_session(
            user_id=current_user.user_id,
            questionnaire_answers_by_qid=payload.answers_by_question_id,
            questionnaire_version=payload.questionnaire_version,
            dictionary_version=dict_version,
            generation_mode="ai",  # P5：用户明确希望 AI 优化，服务端派生
            rule_answers=rule_answers,
        )
        session.merged_pref_fields = list(merged_fields_direct)
        # P6-04：把最近 3 条偏好画像 summary 注入 AI system prompt 的先验
        _hydrate_session_preference_context(
            session=session, current_user=current_user, sb=sb, limit=3
        )
        items = await mgr.try_ai_finalize_recommendation(session=session, repo=repo)
        final_reason = session.final_reason
    else:
        # 默认免费规则模式（prefer_ai_gain=False，或 G-07 强制兜底）
        try:
            items = generate_rule_recommendations(rule_answers, repo=repo)
        except ValueError as exc:
            raise AppError(
                code=INTERNAL_ERROR,
                status_code=500,
                message="推荐生成失败，请稍后再试",
                details={"reason": str(exc)},
            ) from exc
        final_reason = "legacy_rule_engine"

    # 5) 返回正好 5 条（P7-07：顶层为对象，含 merged_pref_fields 便于前端 banner 展示）
    items_dumped = [i.model_dump() for i in items]

    # 6) 登录态下自动入库历史 + 偏好画像（失败不抛，结果放进 autowrite 字段回传前端）
    autowrite_result = _try_autowrite_history_if_user(
        current_user=current_user,
        sb=sb,
        payload=payload,
        dict_version=dict_version,
        items=items,
        rule_answers=rule_answers,
        final_reason=final_reason,
    )

    # 7) P5-07：返回 ai_quota 给前端展示（默认免费模式仍返回一份方便 UI 统一展示"今日额度 3/3"等）
    chat_service = getattr(mgr, "_chat_service", None)
    quota_info: dict[str, int]
    if chat_service is not None and hasattr(chat_service, "peek_quota"):
        quota_info = chat_service.peek_quota(user_id=quota_user_id)
    else:
        quota_info = {
            "user_used": 0,
            "user_limit": int(getattr(settings, "ai_daily_user_limit", 3) or 3),
            "global_used": 0,
            "global_limit": int(getattr(settings, "ai_global_daily_limit", 100) or 100),
        }
    used_ai = bool(final_reason == "ai_gain")

    return {
        "items": items_dumped,
        "merged_pref_fields": merged_fields_direct,
        "autowrite": autowrite_result.to_dict(),
        "used_ai": used_ai,
        "ai_quota": quota_info,
        "final_reason": final_reason,
    }


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
    """会话态统一响应（P5-02：follow_up 或 final 二选一）。"""
    model_config = ConfigDict(extra="forbid")
    session_id: str = Field(..., min_length=8, max_length=64)
    stage: str = Field(..., pattern=r"^(follow_up|final)$")
    question: FollowUpQuestionOutput | None = None
    # 已答轮次进度（方便前端显示进度条 "1/3"）
    rounds_completed: int = Field(..., ge=0, le=3)
    max_rounds: int = Field(3, ge=3, le=3)
    # stage=final 时必有 candidates（长度正好 5，G-08 保障）
    candidates: list[RecommendationItem] | None = None
    # 说明 final 是来自 AI 增益还是规则引擎回退（供前端 trace/调试面板）
    final_reason: str | None = None
    # P7-07：P6-02 冷启动画像合并实际上改变的 answers 字段（仅限 session/start 可能非空）
    # 空数组 = 本次未合并（未登录/没有画像命中）
    merged_pref_fields: list[dict[str, Any]] = Field(default_factory=list)
    # P0 修复：stage=final 时有值，告知前端自动写入情况
    autowrite: dict[str, Any] = Field(default_factory=dict)
    # P5-04A：final 阶段实际走了 AI = final_reason == 'ai_gain'
    used_ai: bool = False
    # P5-07：今日 AI 额度情况（登录态 + prefer_ai_gain=true 时非空）
    ai_quota: dict[str, int] = Field(default_factory=dict)


# ============== P5-02 复用的校验/加载小工具 ==============


def _validate_and_load_for_session_start(
    *,
    raw_body: Any,
    current_user: CurrentUser | None = None,
    sb: SupabaseAdminClient | None = None,
) -> tuple[RecommendationsGenerateRequestV1, QuestionBankV1, Any, list[dict[str, Any]]]:
    """G-07 检查 → Pydantic 校验 → 加载题库 → 翻译 rule_answers。
    返回 (payload, bank, rule_answers, merged_pref_fields)。任何异常 raise 上层转 AppError。
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
    # P6-02：冷启动画像合并（用户显式答案优先）
    merged_pref_fields = _try_merge_recent_preferences_into_answers(
        current_user=current_user,
        sb=sb,
        answers=rule_answers,
    )
    return payload, bank, rule_answers, merged_pref_fields


def _try_merge_recent_preferences_into_answers(
    *,
    current_user: CurrentUser | None,
    sb: SupabaseAdminClient | None,
    answers: Any,  # QuestionnaireAnswers duck-type（含 .model_dump / model_copy）
) -> list[dict[str, Any]]:
    """P6-02 冷启动画像合并：把最近 3 条偏好快照合并回当前 rule_answers。

    返回合并差异列表（每项 {field, kind, before, after}），用于前端 banner 展示。
    未合并/失败都返回空列表。
    """
    empty: list[dict[str, Any]] = []
    if current_user is None or sb is None:
        return empty
    # 避免无model_dump的对象炸掉
    if not (hasattr(answers, "model_dump") and hasattr(answers, "model_copy")):
        return empty

    try:
        from app.api.v1.preferences import load_recent_preference_snapshots

        snaps = load_recent_preference_snapshots(
            sb=sb, user_id=UUID(str(current_user.user_id)), limit=3
        )
    except Exception as exc:
        log = logging.getLogger("app.api.v1.recommendations")
        log.warning("preference_merge_load_fail user=%s err=%r", current_user.user_id, exc)
        return empty
    if not snaps:
        return empty

    logger = logging.getLogger("app.api.v1.recommendations")
    try:
        # P7-07：先保存 before 快照用于 diff
        before = answers.model_dump(mode="python")
        cur = dict(before)
        # 从旧→新遍历，越新的覆盖越旧（最终当前答案再覆盖快照）
        for snap in reversed(snaps):
            sd = snap.snapshot
            if not isinstance(sd, dict):
                continue
            # 单值字段：仅当前空时填
            for k in ("meal_period", "appetite", "budget", "explicit_food_preference", "max_distance_m"):
                if k in sd and cur.get(k) in (None, []):
                    cur[k] = sd[k]
            # 列表字段：合并去重
            for k in ("tastes", "avoidances"):
                if isinstance(sd.get(k), list):
                    merged: list[Any] = list(cur.get(k) or [])
                    for item in sd[k]:
                        if item not in merged:
                            merged.append(item)
                    cur[k] = merged
            # ai_follow_up_answers：合并但不覆盖当前已有键
            if isinstance(sd.get("ai_follow_up_answers"), dict):
                cur_fua = cur.get("ai_follow_up_answers") or {}
                if isinstance(cur_fua, dict):
                    for fk, fv in sd["ai_follow_up_answers"].items():
                        cur_fua.setdefault(fk, fv)
                    cur["ai_follow_up_answers"] = cur_fua
        # 写回到 answers（QuestionnaireAnswers.extra=forbid；确保不传多余键）
        fields = set(type(answers).model_fields.keys())
        filtered_raw = {k: v for k, v in cur.items() if k in fields}
        # 关键：snapshot.model_dump(mode="json") 会把 enum 序列化成字符串；
        # 写回时用 Pydantic 再 validate 一次，保证 enum/列表元素类型回到真实类型（否则 rule_engine 里 .value 会炸）
        try:
            validated = type(answers).model_validate(filtered_raw)
            filtered_typed = validated.model_dump(mode="python")
        except Exception:
            filtered_typed = filtered_raw
        for k, v in filtered_typed.items():
            try:
                object.__setattr__(answers, k, v)
            except Exception:
                # frozen / 不可写对象：跳过
                break
        # P7-07：构建 diff
        after = answers.model_dump(mode="python")
        diff: list[dict[str, Any]] = []
        SINGLE_FIELDS = ("meal_period", "appetite", "budget", "explicit_food_preference", "max_distance_m")
        LIST_FIELDS = ("tastes", "avoidances")
        for k in SINGLE_FIELDS:
            b = before.get(k)
            a = after.get(k)
            if b != a and (b in (None, [], "")) and (a not in (None, [], "")):
                diff.append(
                    {
                        "field": k,
                        "kind": "single",
                        "before": b,
                        "after": a,
                        "change": "filled",
                    }
                )
        for k in LIST_FIELDS:
            b_list: list[Any] = list(before.get(k) or [])
            a_list: list[Any] = list(after.get(k) or [])
            added = [x for x in a_list if x not in b_list]
            if added:
                diff.append(
                    {
                        "field": k,
                        "kind": "list",
                        "before": b_list,
                        "after": a_list,
                        "change": "appended",
                        "added_items": added,
                    }
                )
        b_fua: dict[str, Any] = before.get("ai_follow_up_answers") or {}
        a_fua: dict[str, Any] = after.get("ai_follow_up_answers") or {}
        if isinstance(b_fua, dict) and isinstance(a_fua, dict):
            added_keys = sorted(k for k in a_fua if k not in b_fua)
            if added_keys:
                added_map = {k: a_fua[k] for k in added_keys}
                diff.append(
                    {
                        "field": "ai_follow_up_answers",
                        "kind": "ai_follow_up",
                        "before": b_fua,
                        "after": a_fua,
                        "change": "appended",
                        "added_keys": added_keys,
                        "added_items": added_map,
                    }
                )
        logger.info(
            "preference_merged user=%s snaps=%d fields_changed=%d",
            current_user.user_id,
            len(snaps),
            len(diff),
        )
        return diff
    except Exception as exc:
        logger.warning("preference_merge_apply_fail user=%s err=%r", current_user.user_id, exc)
        return empty


def _hydrate_session_preference_context(
    *,
    session: RecommendationSession,
    current_user: CurrentUser | None,
    sb: SupabaseAdminClient | None,
    limit: int = 3,
) -> None:
    """P6-04：把最近 N 条偏好快照 summary 注入 session.preference_context。

    失败静默：AI 调用绝不能因为画像缺失/加载失败被阻塞。
    """
    if current_user is None or sb is None:
        return
    # 已注入过则跳过（同一会话多轮追问题不需要重复读库）
    if session.preference_context:
        return
    logger = logging.getLogger("app.api.v1.recommendations")
    try:
        from app.api.v1.preferences import (
            load_recent_preference_snapshots,
            summarize_preference_snapshots_for_prompt,
        )

        snaps = load_recent_preference_snapshots(
            sb=sb, user_id=UUID(str(current_user.user_id)), limit=limit
        )
        summary = summarize_preference_snapshots_for_prompt(snaps)
        session.preference_context = summary
        # P7-05：实际合并条数（summary 非空才算有效合并；空串意味着"有快照但无有效总结"，count 保留原值 0）
        session.preference_context_snapshot_count = len(snaps) if summary else 0
    except Exception as exc:
        session.preference_context = ""
        session.preference_context_snapshot_count = 0
        logger.warning(
            "preference_context_hydrate_fail user=%s session=%s err=%r",
            current_user.user_id,
            session.session_id,
            exc,
        )


class _AutowriteResult(NamedTuple):
    """_try_autowrite_history_if_user 返回的结构化结果，供前端展示使用。"""
    logged_in: bool
    history_saved: bool
    history_id: str | None
    preference_saved: bool
    preference_id: str | None
    reason: str  # 中文简短说明，前端可直接展示

    def to_dict(self) -> dict[str, Any]:
        return {
            "logged_in": self.logged_in,
            "history_saved": self.history_saved,
            "history_id": self.history_id,
            "preference_saved": self.preference_saved,
            "preference_id": self.preference_id,
            "reason": self.reason,
        }


def _try_autowrite_history_if_user(
    *,
    current_user: CurrentUser | None,
    sb: SupabaseAdminClient | None,
    payload: RecommendationsGenerateRequestV1,
    dict_version: str,
    items: list[RecommendationItem],
    rule_answers: Any,  # QuestionnaireAnswers duck-type
    source_session_id: str | None = None,
    final_reason: str | None = None,
) -> _AutowriteResult:
    """登录态下：1) 写最终推荐历史；2) 写用户偏好画像快照（P6-01）。

    始终返回 _AutowriteResult（失败不抛异常，原因写进 reason 字段）。
    """
    logger = logging.getLogger("app.api.v1.recommendations")
    if current_user is None or sb is None:
        return _AutowriteResult(
            logged_in=False,
            history_saved=False,
            history_id=None,
            preference_saved=False,
            preference_id=None,
            reason="未登录或数据库暂不可用，未自动保存；可在结果页点「保存到画像」手动保存。",
        )

    hist_resp = None
    try:
        food_code = _extract_food_code(payload.answers_by_question_id)
        tags = _extract_tags(payload.answers_by_question_id)
        hist_snap: dict[str, Any] = {
            "entry_intent": payload.entry_intent,
            "questionnaire_version": payload.questionnaire_version,
            "dictionary_version": dict_version,
            "items": [i.model_dump() for i in items],
        }
        hist_resp = write_user_recommendation(
            sb=sb,
            user=current_user,
            payload=HistoryWriteRequest(
                food_code=food_code,
                location=None,
                radius_meters=None,
                tags=tags,
                recommendation_snapshot=hist_snap,
                result_count=len(items),
                poi_provider=None,
                session_id=source_session_id,
                final_reason=final_reason,
            ),
        )
    except Exception as exc:
        logger.warning("history_autowrite_failed user=%s err=%r", current_user.user_id, exc)
        hist_resp = None

    pref_resp = None
    pref_err_reason: str | None = None
    try:
        snapshot_dump: dict[str, Any]
        if hasattr(rule_answers, "model_dump"):
            snapshot_dump = rule_answers.model_dump(mode="json")
        else:
            snapshot_dump = dict(rule_answers) if isinstance(rule_answers, dict) else {"raw": str(rule_answers)}
        if final_reason:
            snapshot_dump["_meta"] = {"final_reason": final_reason}
        pref_resp = write_user_preference_snapshot(
            sb=sb,
            user=current_user,
            payload=PreferenceWriteRequest(
                questionnaire_version=payload.questionnaire_version,
                dictionary_version=dict_version,
                source_session_id=source_session_id,
                source_history_id=(hist_resp.id if hist_resp is not None else None),
                snapshot=snapshot_dump,
            ),
        )
    except Exception as exc:
        logger.warning("preference_autowrite_failed user=%s err=%r", current_user.user_id, exc)
        pref_err_reason = f"{type(exc).__name__}: {exc}"
        pref_resp = None

    history_ok = hist_resp is not None
    pref_ok = pref_resp is not None
    if history_ok and pref_ok:
        reason = "✓ 已自动保存：推荐历史 + 饮食偏好画像"
    elif history_ok:
        reason = f"已保存历史记录；画像写入失败（{pref_err_reason or '未知原因'}），可手动重试"
    elif pref_ok:
        reason = "已保存画像；推荐历史写入失败（不影响画像时间轴）"
    else:
        reason = f"保存失败：历史和画像均未写入（{pref_err_reason or '数据库或账号异常'}），请稍后重试或手动保存"

    return _AutowriteResult(
        logged_in=True,
        history_saved=history_ok,
        history_id=str(hist_resp.id) if hist_resp is not None else None,
        preference_saved=pref_ok,
        preference_id=str(pref_resp.id) if pref_resp is not None else None,
        reason=reason,
    )


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
        merged_pref_fields=list(session.merged_pref_fields or []),
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
    except Exception:
        raw_body = {}

    payload, _bank, rule_answers, merged_pref_fields = _validate_and_load_for_session_start(
        raw_body=raw_body,
        current_user=current_user,
        sb=sb,
    )

    # 0b) P5-04A：AI 增益登录前置；后端派生 derived_generation_mode
    assert settings is not None
    derived_generation_mode: str = "rule"
    quota_user_id: str | None = None
    if payload.prefer_ai_gain:
        if current_user is None:
            from fastapi import HTTPException, status
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=(
                    "「AI 优化推荐」需要先登录后使用（每天 3 次额度）；"
                    "未登录可使用免费的确定性规则引擎推荐（默认模式，开关关闭即可）。"
                ),
            )
        derived_generation_mode = "ai"
        quota_user_id = current_user.user_id

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

    # 1) 创建会话 + 尝试生成第 1 道 AI 追问
    mgr = get_recommendation_session_manager(settings)
    uid = current_user.user_id if current_user is not None else None
    session = mgr.create_session(
        user_id=uid,
        questionnaire_answers_by_qid=payload.answers_by_question_id,
        questionnaire_version=payload.questionnaire_version,
        dictionary_version=dict_version,
        # G-07：generation_mode 最终由服务端派生（结合 prefer_ai_gain + 登录态）
        generation_mode=derived_generation_mode,
        rule_answers=rule_answers,
    )
    # P7-07：冷启动画像合并信息传给前端 banner
    session.merged_pref_fields = list(merged_pref_fields)
    # P6-04：把最近 3 条偏好画像 summary 注入 session，让 system prompt 拥有长期先验
    _hydrate_session_preference_context(
        session=session, current_user=current_user, sb=sb, limit=3,
    )
    next_q = await mgr.start_and_get_next(session=session)

    # 2) 若 AI 明确认为信息已充分（返回 None）或已经 3 轮，则直接 final
    if next_q is None or session.round_index_1based_next > 3:
        items = await mgr.try_ai_finalize_recommendation(session=session, repo=repo)
        autowrite_result = _try_autowrite_history_if_user(
            current_user=current_user, sb=sb, payload=payload,
            dict_version=dict_version, items=items,
            rule_answers=session.rule_answers,
            source_session_id=session.session_id,
            final_reason=session.final_reason,
        )
        resp = _session_to_state_response(session)
        resp.question = None
        resp.autowrite = autowrite_result.to_dict()
        # P5-04A / P5-07：附加 used_ai + ai_quota
        resp.used_ai = bool(session.final_reason == "ai_gain")
        chat_service = getattr(mgr, "_chat_service", None)
        if chat_service is not None and hasattr(chat_service, "peek_quota"):
            resp.ai_quota = chat_service.peek_quota(user_id=quota_user_id)
        else:
            resp.ai_quota = {
                "user_used": 0,
                "user_limit": int(getattr(settings, "ai_daily_user_limit", 3) or 3),
                "global_used": 0,
                "global_limit": int(getattr(settings, "ai_global_daily_limit", 100) or 100),
            }
        return resp

    resp = _session_to_state_response(session)
    resp.question = next_q
    # P5-07：follow_up 阶段也给一份额度（前端开关上方展示 "今日 X/3" 让用户知道还有多少）
    chat_service = getattr(mgr, "_chat_service", None)
    if chat_service is not None and hasattr(chat_service, "peek_quota"):
        resp.ai_quota = chat_service.peek_quota(user_id=quota_user_id)
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
    except Exception:
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

    # P6-04：同一会话多轮共享一次 preference_context（start 时注入过的这里跳过）
    _hydrate_session_preference_context(
        session=session, current_user=current_user, sb=sb, limit=3,
    )

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
    autowrite_result = _try_autowrite_history_if_user(
        current_user=current_user, sb=sb, payload=start_payload_like,
        dict_version=session.dictionary_version, items=items,
        rule_answers=session.rule_answers,
        source_session_id=session.session_id,
        final_reason=session.final_reason,
    )
    resp = _session_to_state_response(session)
    resp.question = None
    resp.autowrite = autowrite_result.to_dict()
    # P5-04A / P5-07：answer→final 同样回传 used_ai + ai_quota（与 start→final 保持一致）
    resp.used_ai = bool(session.final_reason == "ai_gain")
    quota_user_id = session.user_id
    chat_service = getattr(mgr, "_chat_service", None)
    if chat_service is not None and hasattr(chat_service, "peek_quota"):
        resp.ai_quota = chat_service.peek_quota(user_id=quota_user_id)
    else:
        resp.ai_quota = {
            "user_used": 0,
            "user_limit": int(getattr(settings, "ai_daily_user_limit", 3) or 3),
            "global_used": 0,
            "global_limit": int(getattr(settings, "ai_global_daily_limit", 100) or 100),
        }
    return resp
