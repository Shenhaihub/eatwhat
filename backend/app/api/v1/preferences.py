"""P6-01 用户偏好画像快照 API。

append-only 快照模型：
  POST   /api/v1/preferences              → 写一条（推荐生成后自动调用）
  GET    /api/v1/preferences              → 当前用户的快照列表（created_at DESC 分页）
  GET    /api/v1/preferences/latest       → 最近一条（用于首页/偏好卡片渲染）
  DELETE /api/v1/preferences/{id}         → 删除一条
  DELETE /api/v1/preferences              → 清空全部

与 history 模块设计保持一致：
  - service_role + 显式 WHERE user_id = current.user_id 双保险
  - 写前 auth.users 存活校验（GDPR 死 token 防护）
  - 所有 Pydantic model 启用 extra="forbid"
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from postgrest.types import CountMethod
from pydantic import BaseModel, ConfigDict, Field

from app.api.v1.auth import CurrentUser, get_current_user
from app.core.config import Settings, get_settings
from app.core.exceptions import INTERNAL_ERROR, NOT_FOUND, AppError
from app.core.supabase_client import SupabaseAdminClient, get_supabase_admin


def _require_sb(sb: SupabaseAdminClient | None) -> SupabaseAdminClient:
    if sb is None:
        raise AppError(
            INTERNAL_ERROR,
            message="偏好画像服务暂不可用（Supabase 未配置）",
            details={"reason": "supabase_not_configured"},
        )
    return sb


log = logging.getLogger("app.api.v1.preferences")

router = APIRouter(prefix="/api/v1/preferences", tags=["preferences"])

_TABLE = "user_preference_snapshots"

# ============================================================
# Schemas
# ============================================================


class PreferenceSnapshotBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    questionnaire_version: str = Field(default="v1.0", max_length=32)
    dictionary_version: str = Field(default="v1.0", max_length=32)
    # P7-06：画像快照 schema 版本号；当前恒为 "v1.0"，未来若 snapshot 结构变更（新增/删除大字段）可 bump。
    snapshot_version: str = Field(default="v1.0", max_length=32)
    source_session_id: str | None = Field(default=None, max_length=64)
    source_history_id: UUID | None = None
    # QuestionnaireAnswers.model_dump() 结果；不强约束字段，保持画像维度可扩展
    snapshot: dict[str, Any]


class PreferenceWriteRequest(PreferenceSnapshotBase):
    created_at: datetime | None = None


class PreferenceSnapshotResponse(PreferenceSnapshotBase):
    id: UUID
    user_id: UUID
    created_at: datetime
    updated_at: datetime


class PreferenceListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[PreferenceSnapshotResponse]
    total: int
    limit: int
    offset: int | None = None  # offset 模式才填充
    # P7-02：cursor 分页（created_at DESC + id DESC）。before=null 表示已经到尾。
    # 若调用方用 before= 则下一页 next_cursor 非空，可继续请求；若用 offset= 则忽略该字段。
    next_cursor: str | None = None
    page_cursor: str | None = None  # 本次请求使用的 before= 原文（调试用，原样回显）


class PreferenceDeleteAllResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deleted: int


# ============================================================
# 核心业务函数（推荐接口直接调用，不经过 HTTP）
# ============================================================


def load_recent_preference_snapshots(
    *,
    sb: SupabaseAdminClient,
    user_id: UUID,
    limit: int = 3,
) -> list[PreferenceSnapshotResponse]:
    """读最近 N 条偏好快照（按 created_at DESC）。

    - 返回空列表表示无历史/查询失败。
    - 只在登录态下被 recommendations 模块调用，用于冷启动画像合并。
    """
    if limit <= 0:
        return []
    try:
        res = (
            sb.client.table(_TABLE)
            .select("*")
            .eq("user_id", str(user_id))
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("preference_load_fail user=%s err=%r", user_id, exc)
        return []
    return [_row_to_response(r) for r in res.data]


def write_user_preference_snapshot(
    *,
    sb: SupabaseAdminClient,
    user: CurrentUser,
    payload: PreferenceWriteRequest,
) -> PreferenceSnapshotResponse:
    """append-only 写一条偏好快照。

    GDPR 死 token 防线：写前再确认真实 auth.users 仍然存在。
    """
    try:
        sb.auth_admin.get_user_by_id(str(user.user_id))
    except Exception as exc:
        log.warning("preference_write user_gone user_id=%s err=%r", user.user_id, exc)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="账号已不存在，请重新登录",
        ) from exc

    insert_payload: dict[str, Any] = {
        "user_id": str(user.user_id),
        "questionnaire_version": payload.questionnaire_version,
        "dictionary_version": payload.dictionary_version,
        # P7-06：snapshot_version 是新加字段；如果 DB 还没跑 HOTFIX 迁移（缺列），
        # 这里会抛 "column 'snapshot_version' of relation ... does not exist"。
        # 策略：先尝试带 snapshot_version 写，失败若是"缺列"错误 → 自动重试去掉该列（不阻塞用户），
        # 并在 logger 里提醒运维补 ALTER TABLE。
        "source_session_id": payload.source_session_id,
        "source_history_id": str(payload.source_history_id) if payload.source_history_id is not None else None,
        "snapshot_jsonb": payload.snapshot or {},
    }
    if payload.snapshot_version:
        insert_payload["snapshot_version"] = payload.snapshot_version
    if payload.created_at is not None:
        insert_payload["created_at"] = payload.created_at.isoformat()

    try:
        try:
            result = sb.client.table(_TABLE).insert(insert_payload).execute()
        except Exception as first_exc:  # noqa: BLE001
            # 检测是否"缺 snapshot_version 列"；是则去掉该字段重试（向前兼容旧 DB 结构）
            first_msg = str(first_exc).lower()
            if "snapshot_version" in first_msg and (
                "column" in first_msg or "does not exist" in first_msg or "不存在" in first_msg
            ):
                log.warning(
                    "preference_write_missing_snapshot_version_col user=%s fallback_without_it",
                    user.user_id,
                )
                insert_payload.pop("snapshot_version", None)
                result = sb.client.table(_TABLE).insert(insert_payload).execute()
            else:
                raise
    except Exception as exc:
        log.exception("preference_write_db_fail user=%s", user.user_id)
        raise AppError(
            INTERNAL_ERROR,
            message="写入偏好画像失败",
            details={"reason": "db_insert_error", "err_type": type(exc).__name__},
        ) from exc

    if not result.data:
        raise AppError(INTERNAL_ERROR, message="写入偏好画像失败（DB 无返回）")
    return _row_to_response(result.data[0])


def _row_to_response(row: dict[str, Any]) -> PreferenceSnapshotResponse:
    return PreferenceSnapshotResponse(
        id=UUID(str(row["id"])),
        user_id=UUID(str(row["user_id"])),
        questionnaire_version=str(row.get("questionnaire_version") or "v1.0"),
        dictionary_version=str(row.get("dictionary_version") or "v1.0"),
        snapshot_version=str(row.get("snapshot_version") or "v1.0"),
        source_session_id=row.get("source_session_id"),
        source_history_id=(UUID(str(row["source_history_id"])) if row.get("source_history_id") else None),
        snapshot=dict(row.get("snapshot_jsonb") or {}),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


# ============================================================
# HTTP 路由
# ============================================================


@router.post("", response_model=PreferenceSnapshotResponse, status_code=status.HTTP_201_CREATED)
def create_preference_snapshot(
    current: Annotated[CurrentUser, Depends(get_current_user)],
    payload: PreferenceWriteRequest,
    sb: Annotated[SupabaseAdminClient | None, Depends(get_supabase_admin)] = None,
    _settings: Annotated[Settings, Depends(get_settings)] = None,
) -> PreferenceSnapshotResponse:
    sb_ok = _require_sb(sb)
    log.info(
        "preference_write user=%s qv=%s dv=%s session=%s",
        current.user_id,
        payload.questionnaire_version,
        payload.dictionary_version,
        payload.source_session_id,
    )
    return write_user_preference_snapshot(sb=sb_ok, user=current, payload=payload)


@router.get("", response_model=PreferenceListResponse)
def list_preference_snapshots(
    current: Annotated[CurrentUser, Depends(get_current_user)],
    sb: Annotated[SupabaseAdminClient | None, Depends(get_supabase_admin)] = None,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    # P7-02：cursor 分页（created_at DESC + id DESC），优先于 offset
    before: str | None = Query(default=None, max_length=256),
) -> PreferenceListResponse:
    sb_ok = _require_sb(sb)
    count_res = (
        sb_ok.client.table(_TABLE)
        .select("id", count=CountMethod.exact)
        .eq("user_id", str(current.user_id))
        .execute()
    )
    total = int(count_res.count or 0)

    if before:
        # Cursor 模式：created_at DESC + id DESC
        cursor_created, cursor_id = _decode_cursor(before)
        peek_limit = limit + 1
        # 先按 created_at 过滤（PostgREST 不支持复杂 OR 条件内联，分两步：取 created_at <= ... 后再本地切 id= 的情况）
        raw = (
            sb_ok.client.table(_TABLE)
            .select("*")
            .eq("user_id", str(current.user_id))
            .lt("created_at", cursor_created.isoformat())  # 严格更早：created_at < X
            .order("created_at", desc=True)
            .order("id", desc=True)
            .limit(peek_limit)
            .execute()
        )
        # 补充 created_at == 同值 且 id < cursor_id 的条目（为了 cursor 是严格单调）
        extra_raw = (
            sb_ok.client.table(_TABLE)
            .select("*")
            .eq("user_id", str(current.user_id))
            .eq("created_at", cursor_created.isoformat())
            .lt("id", str(cursor_id))
            .order("id", desc=True)
            .limit(peek_limit)
            .execute()
        )
        # 合并两段，再按 (created_at desc, id desc) 整体排一次
        all_rows: list[dict[str, Any]] = list(raw.data or []) + list(extra_raw.data or [])
        all_rows.sort(
            key=lambda r: (
                _sort_key_dt(r.get("created_at")),
                _sort_key_str(r.get("id")),
            ),
            reverse=True,
        )
        # 只取前 peek_limit，之后判断 next
        trimmed = all_rows[:peek_limit]
        items_rows = trimmed[:limit]
        has_more = len(trimmed) > limit
        items = [_row_to_response(r) for r in items_rows]
        # 下一页 cursor：基于本页最后一条"已返回"的行（inclusive 边界），
        # 下一页 before= 取严格更早，保证未被 peek 的"第 limit+1 条"能正确落在下一页首条。
        if has_more and items:
            last = items_rows[-1]
            next_cursor = _encode_cursor(last["created_at"], UUID(str(last["id"])))
        else:
            next_cursor = None
        return PreferenceListResponse(
            items=items,
            total=total,
            limit=limit,
            offset=None,
            next_cursor=next_cursor,
            page_cursor=before,
        )

    # Offset 模式（兼容旧客户端）
    items_res = (
        sb_ok.client.table(_TABLE)
        .select("*")
        .eq("user_id", str(current.user_id))
        .order("created_at", desc=True)
        .order("id", desc=True)
        .range(offset, offset + limit - 1)
        .execute()
    )
    rows = list(items_res.data or [])
    items = [_row_to_response(r) for r in rows]
    # P7-02：offset 模式下也产出 next_cursor，便于前端首屏之后无缝用 cursor 翻页
    has_more = total > offset + len(items)
    next_cursor: str | None = None
    if has_more and rows:
        last = rows[-1]
        try:
            next_cursor = _encode_cursor(last["created_at"], UUID(str(last["id"])))
        except Exception:
            next_cursor = None
    return PreferenceListResponse(
        items=items,
        total=total,
        limit=limit,
        offset=offset,
        next_cursor=next_cursor,
        page_cursor=None,
    )


def _sort_key_dt(v: Any) -> tuple:
    # datetime iso 字符串字典序等于时间序（UTC/带 tz 都成立），空排最后
    if not v:
        return ()
    return (v,)


def _sort_key_str(v: Any) -> tuple:
    if not v:
        return ()
    return (str(v),)


def _encode_cursor(created_at: Any, id_val: UUID) -> str:
    import base64

    dt_str = created_at.isoformat() if hasattr(created_at, "isoformat") else str(created_at)
    raw = f"{dt_str}|{id_val}"
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii").rstrip("=")


def _decode_cursor(cursor: str) -> tuple[datetime, UUID]:
    import base64

    if not cursor:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "before cursor 不能为空")
    pad = 4 - (len(cursor) % 4)
    padded = cursor + ("=" * pad if pad != 4 else "")
    try:
        decoded = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
    except Exception as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "before cursor 格式非法") from exc
    if "|" not in decoded:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "before cursor 载荷非法")
    created_iso, id_str = decoded.split("|", 1)
    try:
        created_dt = datetime.fromisoformat(created_iso)
    except Exception as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "before cursor 时间戳非法") from exc
    try:
        return created_dt, UUID(id_str)
    except Exception as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "before cursor id 非法") from exc


@router.get("/latest", response_model=PreferenceSnapshotResponse)
def get_latest_snapshot(
    current: Annotated[CurrentUser, Depends(get_current_user)],
    sb: Annotated[SupabaseAdminClient | None, Depends(get_supabase_admin)] = None,
) -> PreferenceSnapshotResponse:
    sb_ok = _require_sb(sb)
    res = (
        sb_ok.client.table(_TABLE)
        .select("*")
        .eq("user_id", str(current.user_id))
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    if not res.data:
        raise AppError(NOT_FOUND, message="还没有偏好画像记录")
    return _row_to_response(res.data[0])


@router.delete("/{snapshot_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_preference_snapshot(
    snapshot_id: UUID,
    current: Annotated[CurrentUser, Depends(get_current_user)],
    sb: Annotated[SupabaseAdminClient | None, Depends(get_supabase_admin)] = None,
) -> None:
    sb_ok = _require_sb(sb)
    # 先 SELECT 确认是自己的（service_role 绕过 RLS，必须手动校验）
    existing = (
        sb_ok.client.table(_TABLE)
        .select("id,user_id")
        .eq("id", str(snapshot_id))
        .eq("user_id", str(current.user_id))
        .limit(1)
        .execute()
    )
    if not existing.data:
        raise AppError(NOT_FOUND, message="偏好画像不存在或无权删除")
    sb_ok.client.table(_TABLE).delete().eq("id", str(snapshot_id)).eq("user_id", str(current.user_id)).execute()


@router.delete("", response_model=PreferenceDeleteAllResponse)
def clear_all_preference_snapshots(
    current: Annotated[CurrentUser, Depends(get_current_user)],
    sb: Annotated[SupabaseAdminClient | None, Depends(get_supabase_admin)] = None,
) -> PreferenceDeleteAllResponse:
    sb_ok = _require_sb(sb)
    res = (
        sb_ok.client.table(_TABLE)
        .delete(count=CountMethod.exact)
        .eq("user_id", str(current.user_id))
        .execute()
    )
    deleted = int(res.count or 0)
    log.info("preference_clear_all user=%s deleted=%s", current.user_id, deleted)
    return PreferenceDeleteAllResponse(deleted=deleted)


# ============================================================
# P6-04：偏好快照 → 自然语言摘要（喂 DeepSeek system prompt）
# ============================================================


_DIM_LABELS: dict[str, str] = {
    "meal_period": "用餐时段",
    "appetite": "当前食欲",
    "cuisine_preference": "菜系偏好",
    "taste_preference": "口味偏好",
    "ambience_preference": "用餐氛围",
    "dietary_restrictions": "忌口/禁忌",
    "budget_tier": "预算档位",
    "explicit_preference": "是否有明确想吃",
    "ai_follow_up_clarified": "追问澄清项",
}


def _dim_title(key: str) -> str:
    return _DIM_LABELS.get(key, key)


def _scalar_repr(v: Any) -> str:
    if v is None:
        return "无"
    if isinstance(v, bool):
        return "是" if v else "否"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, str):
        return v
    if isinstance(v, list):
        if not v:
            return "无"
        return "、".join(_scalar_repr(x) for x in v)
    return str(v)


def summarize_preference_snapshots_for_prompt(
    snaps: list[PreferenceSnapshotResponse],
    *,
    max_chars: int = 2400,
) -> str:
    """把最近 N 条偏好快照转成适合 system prompt 的中文自然语言摘要。

    设计原则：
    - 静默返回 "" 而不是抛错（fail-open，AI 调用绝不能被画像缺失阻塞）。
    - 只提取对推荐有影响的维度；_meta 字段直接跳过。
    - 每条快照不超过 ~800 字；整体截断到 max_chars，保持 prompt 预算合理。
    - P6-04b：顶部加软提示——若画像与本次最新问卷答案冲突，强优先取本次答案
      （防止画像太重压过用户当下明确的选择）。
    """
    if not snaps:
        return ""
    bullets: list[str] = []
    for idx, snap in enumerate(snaps, start=1):
        snap_obj = snap.snapshot or {}
        dims: list[str] = []
        for k, v in snap_obj.items():
            if k.startswith("_"):
                continue
            if k in ("questionnaire_version", "dictionary_version"):
                continue
            val = _scalar_repr(v)
            if not val or val in ("无", "否", "—"):
                continue
            dims.append(f"{_dim_title(k)}={val}")
        created = snap.created_at.strftime("%Y-%m-%d %H:%M") if snap.created_at else "未知时间"
        bullet = f"- 快照#{idx}（{created}）：{'；'.join(dims) or '维度未记录'}"
        bullets.append(bullet)
    inner = "\n".join(bullets)
    # 整体正文（不含软前缀）的预算 = max_chars - 预留前缀长度
    PREFIX = (
        "【历史偏好画像参考（非强制）】\n"
        "- 以下是用户过去留下的画像摘要，仅供你判断用户长期偏好时参考；\n"
        "- 若下面的画像与用户本次问卷最新答案/当下明确的选择存在冲突，必须优先采用本次最新答案。\n"
        "---\n"
    )
    prefix_chars = len(PREFIX)
    if len(inner) > max(0, max_chars - prefix_chars - 3):
        inner = inner[: max(0, max_chars - prefix_chars - 3)] + "..."
    if not inner or inner == "...":
        return ""  # 裁剪后完全没内容就别挂个空壳前缀
    return f"{PREFIX}{inner}"
