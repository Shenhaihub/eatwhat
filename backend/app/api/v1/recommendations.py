"""问卷答案直接生成推荐。

P2-04 交付物：POST /api/v1/recommendations。

请求体（Pydantic）：
- entry_intent: ai_recommend（P2 阶段唯一支持的入口；其他入口预留但返回 400）
- questionnaire_version: v1.0
- answers_by_question_id: 与 `/questionnaire/next` 完全一致的 dict[str, list[str]]
- （可选）dictionary_version: 不传 = 用 DEFAULT_DICTIONARY_VERSION（当前 v1.0）

响应体：list[RecommendationItem]，长度固定 5（G-02/G-08）。

G-07：请求体任何层级若出现 source_type → 400。
G-09：answers_by_question_id 复用 QuestionnaireNextRequestV1 自带的形状校验。
G-11：不询问医学过敏原，医学过敏原仅从食物字典继承（输出项若含则带免责 note）。
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Request
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.core.exceptions import BAD_REQUEST, INTERNAL_ERROR, NOT_FOUND, AppError
from app.repositories.food_dictionary import (
    DEFAULT_DICTIONARY_VERSION,
    get_food_dictionary_repository,
)
from app.schemas import RecommendationItem
from app.schemas.questionnaire import QuestionBankV1
from app.services.questionnaire_state import load_question_bank
from app.services.questionnaire_to_rule import questionnaire_answers_by_qid_to_rule_input
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
    return [i.model_dump() for i in items]
