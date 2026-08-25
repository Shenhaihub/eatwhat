"""P6-04 反馈闭环 API。

3 条接口：
  POST /api/v1/feedback              → 提交反馈（🔒登录可选）
  GET  /api/v1/feedback/types        → 获取反馈类型列表
  POST /api/v1/feedback/report       → 举报内容（🔒登录）

MVP 阶段：
  - 反馈数据存内存（进程内 dict），后续接 DB
  - 支持 4 种反馈类型：bug_report / feature_request / content_report / general
  - 举报功能针对 Feed 内容
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.api.v1.auth import CurrentUser, get_current_user, get_current_user_optional

log = logging.getLogger("app.api.v1.feedback")

router = APIRouter(prefix="/api/v1/feedback", tags=["feedback"])

FeedbackType = Literal["bug_report", "feature_request", "content_report", "general"]


# ============================================================
# Schemas
# ============================================================


class FeedbackTypeOption(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: FeedbackType
    label: str
    description: str


class FeedbackSubmitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    feedback_type: FeedbackType = Field(..., description="反馈类型")
    content: str = Field(..., min_length=2, max_length=1000, description="反馈内容（2-1000 字）")
    page_url: str | None = Field(None, max_length=500, description="反馈来源页面 URL")
    app_version: str | None = Field(None, max_length=32, description="应用版本号")
    # 上下文信息
    context: dict[str, str] | None = Field(
        default=None,
        description="上下文信息（如 session_id、推荐 food_code 等）",
    )


class FeedbackSubmitResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ok: bool
    feedback_id: str
    message: str


class ReportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_type: Literal["feed", "comment", "user"] = Field(..., description="举报目标类型")
    target_id: str = Field(..., max_length=64, description="举报目标 ID")
    reason: Literal[
        "spam", "inappropriate", "misinformation", "harassment", "other"
    ] = Field(..., description="举报原因")
    description: str | None = Field(None, max_length=500, description="补充说明（可选）")


class ReportResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ok: bool
    report_id: str
    message: str


# ============================================================
# In-memory store（MVP only）
# ============================================================

_FEEDBACK_TYPE_OPTIONS: list[FeedbackTypeOption] = [
    FeedbackTypeOption(
        key="bug_report",
        label="🐛 Bug 报告",
        description="发现了功能异常、崩溃或显示错误",
    ),
    FeedbackTypeOption(
        key="feature_request",
        label="💡 功能建议",
        description="希望添加新功能或改进现有功能",
    ),
    FeedbackTypeOption(
        key="content_report",
        label="⚠️ 内容举报",
        description="社区中存在不当内容需要处理",
    ),
    FeedbackTypeOption(
        key="general",
        label="💬 一般反馈",
        description="其他任何意见或建议",
    ),
]

_REPORT_REASONS: dict[str, str] = {
    "spam": "垃圾/广告",
    "inappropriate": "不当内容",
    "misinformation": "虚假信息",
    "harassment": "骚扰/攻击",
    "other": "其他",
}


@dataclass(slots=True)
class _FeedbackStore:
    """进程内反馈存储（MVP）。"""

    feedbacks: list[dict] = field(default_factory=list)
    reports: list[dict] = field(default_factory=list)
    # 反馈 ID 计数器
    _id_counter: int = 0
    # 用户冷却（防止刷反馈）：user_id -> last_submit_time
    _user_cooldown: dict[str, float] = field(default_factory=dict)
    COOLDOWN_SECONDS = 60  # 同一用户 60 秒内只能提交一次反馈

    def next_id(self, prefix: str) -> str:
        self._id_counter += 1
        ts = int(time.time())
        return f"{prefix}_{ts}_{self._id_counter:04d}"

    def check_cooldown(self, user_id: str | None) -> bool:
        """检查用户是否在冷却期。返回 True 表示可以提交。"""
        if user_id is None:
            return True  # 匿名用户不限制（MVP 简化）
        last_time = self._user_cooldown.get(user_id, 0)
        return (time.time() - last_time) >= self.COOLDOWN_SECONDS

    def update_cooldown(self, user_id: str | None) -> None:
        if user_id is not None:
            self._user_cooldown[user_id] = time.time()


_STORE = _FeedbackStore()


# ============================================================
# Routes
# ============================================================


@router.get("/types", response_model=list[FeedbackTypeOption])
async def get_feedback_types() -> list[FeedbackTypeOption]:
    """获取反馈类型列表（前端展示用）。"""
    return _FEEDBACK_TYPE_OPTIONS


@router.post("/submit", response_model=FeedbackSubmitResponse)
async def submit_feedback(
    body: FeedbackSubmitRequest,
    current_user: CurrentUser | None = Depends(get_current_user_optional),
) -> FeedbackSubmitResponse:
    """提交反馈（登录可选）。

    - 登录用户：有 60 秒冷却期
    - 匿名用户：不限制但无法追踪
    """
    user_id = current_user.user_id if current_user is not None else None

    # 冷却检查
    if not _STORE.check_cooldown(user_id):
        raise HTTPException(
            status_code=429,
            detail={
                "code": "COOLDOWN",
                "message": "提交过于频繁，请稍后再试",
                "cooldown_seconds": _STORE.COOLDOWN_SECONDS,
            },
        )

    feedback_id = _STORE.next_id("fb")
    _STORE.feedbacks.append(
        {
            "id": feedback_id,
            "user_id": user_id,
            "feedback_type": body.feedback_type,
            "content": body.content,
            "page_url": body.page_url,
            "app_version": body.app_version,
            "context": body.context,
            "created_at": datetime.now(UTC).isoformat(),
            "status": "pending",
        }
    )

    _STORE.update_cooldown(user_id)

    log.info(
        "feedback_submitted id=%s type=%s user=%s content_len=%d",
        feedback_id,
        body.feedback_type,
        user_id or "anonymous",
        len(body.content),
    )

    return FeedbackSubmitResponse(
        ok=True,
        feedback_id=feedback_id,
        message="反馈已提交，感谢你的建议！",
    )


@router.post("/report", response_model=ReportResponse)
async def submit_report(
    body: ReportRequest,
    current_user: CurrentUser = Depends(get_current_user),
) -> ReportResponse:
    """举报内容（🔒登录）。

    MVP：只记录不处理，后续由运营审核。
    """
    if current_user is None:
        raise HTTPException(status_code=401, detail="UNAUTHENTICATED")

    report_id = _STORE.next_id("rp")
    _STORE.reports.append(
        {
            "id": report_id,
            "reporter_id": current_user.user_id,
            "target_type": body.target_type,
            "target_id": body.target_id,
            "reason": body.reason,
            "reason_label": _REPORT_REASONS.get(body.reason, "其他"),
            "description": body.description,
            "created_at": datetime.now(UTC).isoformat(),
            "status": "pending",
        }
    )

    log.info(
        "report_submitted id=%s target=%s:%s reason=%s reporter=%s",
        report_id,
        body.target_type,
        body.target_id,
        body.reason,
        current_user.user_id,
    )

    return ReportResponse(
        ok=True,
        report_id=report_id,
        message="举报已提交，我们会尽快处理",
    )
