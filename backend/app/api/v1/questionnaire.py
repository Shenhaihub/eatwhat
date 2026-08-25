"""问卷决策相关 HTTP 接口。

当前仅提供 P2-03A 的 1 个端点：

- `POST /api/v1/questionnaire/next`
  接收当前 entry_intent + questionnaire_version + answers_by_question_id，
  返回 `QuestionnaireRecomputeResult` 的对外版（剔除 deprecated 字段）。
  遵循 G-07：请求里如果出现任何 `source_type` key，直接 400 BAD_REQUEST。

路由层不做业务决策；业务完全落在 `app.services.questionnaire_state`，
这里只负责：参数校验→G-07 拦截→调用 service→异常统一映射→返回响应。

关于 G-07 检查位置：FastAPI 默认会在进入函数体之前，先用 Pydantic 解析 `payload`
作为依赖参数——此时顶层出现 `source_type` 会被 extra=forbid 先 422，函数体里的检查
就来不及了。因此本路由 **手动** `await request.json()` → **先**做 G-07 检查，
**再**用 `QuestionnaireNextRequestV1.model_validate` 跑 schema，422 由
`RequestValidationError` 抛出，结构与 FastAPI 默认一致（全局 handler 已经能接住）。
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Request
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError

from app.core.exceptions import BAD_REQUEST, INTERNAL_ERROR, NOT_FOUND, AppError
from app.schemas.questionnaire import (
    QuestionnaireNextRequestV1,
    QuestionnaireRecomputeResult,
)
from app.services.questionnaire_state import (
    load_question_bank,
    recompute_questionnaire,
)

router = APIRouter(
    prefix="/api/v1/questionnaire",
    tags=["questionnaire"],
)


# ---- G-07：递归检测请求体里是否出现 source_type key（无论层级） ----
def _find_source_type_keys(obj: Any, *, path_prefix: str = "body") -> list[str]:
    """递归遍历 JSON 结构，找出所有名为 source_type 的 key 所在位置。

    返回位置数组（比如 ["body.source_type", "body.answers_by_question_id.x[0].source_type"]）。
    用于在 Pydantic schema 校验之前先拦截，保证 G-07 对"顶层/嵌套"都生效。
    """
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


# ---- deprecated 字段：HTTP 响应中剔除，避免前端误用 ----
_DEPRECATED_FIELDS_TO_EXCLUDE: set[str] = {
    "recommendations_source",
    "questionnaire_mismatch",
}


@router.post("/next", response_model=QuestionnaireRecomputeResult)
async def questionnaire_next(
    request: Request,
) -> dict[str, Any]:
    # 0) 先拿原始 JSON：先 G-07 拦截，后 Pydantic 校验
    try:
        raw_body: Any = await request.json()
    except json.JSONDecodeError:
        # body 不是合法 JSON → 让 Pydantic 抛空对象校验失败（422），信息更准确
        raw_body = {}
    except Exception:
        raw_body = {}

    # 1) G-07：递归检查 source_type，命中直接 400 BAD_REQUEST
    source_type_paths = _find_source_type_keys(raw_body)
    if source_type_paths:
        raise AppError(
            code=BAD_REQUEST,
            status_code=400,
            message="请求体不得携带 source_type（来源必须由服务端派生）",
            details={
                "detected_keys": source_type_paths,
                "g_rule": "G-07",
            },
        )

    # 2) 手动跑 Pydantic schema；失败 → 抛 RequestValidationError → 外层 422
    if not isinstance(raw_body, dict):
        # 顶层 JSON 不是 object（可能是 array/null/...）
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
        payload = QuestionnaireNextRequestV1.model_validate(raw_body)
    except ValidationError as exc:
        raise RequestValidationError(errors=exc.errors(include_url=False)) from exc

    # 3) 加载题库：FileNotFound → 404；完整性 ValueError → 500
    try:
        bank = load_question_bank(questionnaire_version=payload.questionnaire_version)
    except FileNotFoundError as exc:
        raise AppError(
            code=NOT_FOUND,
            status_code=404,
            message=f"不存在的问卷版本：{payload.questionnaire_version}（当前仅提供 v1.0）",
            details={"hint": str(exc)},
        ) from exc
    except ValueError as exc:
        # 题库自身完整性校验失败（display_if 引用错误、ID 冲突等）
        raise AppError(
            code=INTERNAL_ERROR,
            status_code=500,
            message="问卷库加载失败，请联系维护者",
            details={"reason": str(exc)},
        ) from exc

    # 4) 重算：unknown entry_intent → 400；其他 ValueError 归 500
    try:
        result = recompute_questionnaire(
            bank=bank,
            entry_intent=payload.entry_intent,
            answers_by_question_id=payload.answers_by_question_id,
        )
    except ValueError as exc:
        msg = str(exc)
        if msg.startswith("unknown entry_intent"):
            raise AppError(
                code=BAD_REQUEST,
                status_code=400,
                message=(
                    "未知 entry_intent："
                    f"{payload.entry_intent}，允许值为 ai_recommend / community / activity / user_choice"
                ),
                details={"hint": msg},
            ) from exc
        raise AppError(
            code=INTERNAL_ERROR,
            status_code=500,
            message="问卷重算失败，请联系维护者",
            details={"reason": msg},
        ) from exc

    # 5) 返回：排除 deprecated 字段（对外契约里没有）
    return result.model_dump(exclude=_DEPRECATED_FIELDS_TO_EXCLUDE)
