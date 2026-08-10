"""P4-03 推荐历史记录 API。

4 条接口：
  POST   /api/v1/history                 → 写一条（由推荐接口调用，也可前端直接补写）
  GET    /api/v1/history                 → 当前用户的列表（created_at DESC，分页 limit/offset）
  DELETE /api/v1/history/{id}            → 删除一条（必须是当前用户的）
  DELETE /api/v1/history                 → 清空当前用户所有历史

🔒 关键防御：
  后端持有 service_role，默认绕过 Supabase RLS。
  因此所有 CRUD 都显式加 WHERE user_id = current.user_id，禁止省略。
  RLS 作为第二道防线（万一以后 anon 客户端直接连 PostgREST）。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Annotated, Any, cast
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from postgrest.types import CountMethod
from pydantic import BaseModel, Field

from app.api.v1.auth import CurrentUser, get_current_user
from app.core.config import Settings, get_settings
from app.core.exceptions import INTERNAL_ERROR, AppError
from app.core.supabase_client import SupabaseAdminClient, get_supabase_admin


def _require_sb(sb: SupabaseAdminClient | None) -> SupabaseAdminClient:
    """History 路由强依赖 Supabase：缺失直接返回 500 级错误。"""
    if sb is None:
        raise AppError(
            INTERNAL_ERROR,
            message="历史记录服务暂不可用（Supabase 未配置）",
            details={"reason": "supabase_not_configured"},
        )
    return sb

log = logging.getLogger("app.api.v1.history")

router = APIRouter(prefix="/api/v1/history", tags=["history"])


# ============== Schemas ==============

class HistoryRecordBase(BaseModel):
    food_code: str | None = None
    location: dict[str, Any] | None = Field(default_factory=dict)
    radius_meters: int | None = None
    tags: list[str] | None = Field(default_factory=list)
    recommendation_snapshot: dict[str, Any]
    result_count: int = 0
    poi_provider: str | None = None


class HistoryWriteRequest(HistoryRecordBase):
    """写历史请求；也可以直接通过 Python 函数 write_user_recommendation() 调用。"""

    created_at: datetime | None = None


class HistoryRecordResponse(HistoryRecordBase):
    id: UUID
    user_id: UUID
    created_at: datetime
    updated_at: datetime


class HistoryListResponse(BaseModel):
    items: list[HistoryRecordResponse]
    total: int
    limit: int
    offset: int


class HistoryDeleteAllResponse(BaseModel):
    deleted: int


# ============== 核心业务函数（同时暴露给 /recommendations 调用） ==============

def write_user_recommendation(
    *,
    sb: SupabaseAdminClient,
    user: CurrentUser,
    payload: HistoryWriteRequest,
) -> HistoryRecordResponse:
    """写一条推荐历史（同步；因为 Supabase Python client 不是 async 版）。

    强制写入 user_id=user.user_id，防 service_role 误用。

    GDPR 额外防线：写之前再校验 auth.user 仍然存在（删号后旧 token 还能过 JWT 校验，但
    此时 auth.users 已经被物理删除；该检查保证"死 token"无法再写脏数据）。
    """
    # 1) 存活校验：user 必须仍在 Supabase auth.users 中
    try:
        sb.auth_admin.get_user_by_id(str(user.user_id))
    except Exception as exc:
        # Supabase Admin: 404 抛 AuthApiError；网络超时抛 ConnectTimeout 等
        log.warning("history_write user_gone user_id=%s err=%r", user.user_id, exc)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="账号已不存在，请重新登录",
        ) from exc

    insert_payload: dict[str, Any] = {
        "user_id": str(user.user_id),
        "food_code": payload.food_code,
        "location_json": payload.location or {},
        "radius_meters": payload.radius_meters,
        "tags_json": payload.tags or [],
        "recommendation_snapshot": payload.recommendation_snapshot,
        "result_count": payload.result_count,
        "poi_provider": payload.poi_provider,
    }
    if payload.created_at is not None:
        insert_payload["created_at"] = payload.created_at.isoformat()

    # 写 + 读回（RETURNING 风格；Supabase Python client 会自动返回新行）
    result = sb.client.table("user_recommendations").insert(insert_payload).execute()
    rows = getattr(result, "data", None) or []
    if not rows:
        raise HTTPException(status_code=500, detail="写历史失败：数据库未返回新行")
    return _row_to_response(cast(dict[str, Any], rows[0]))


def _row_to_response(row: dict[str, Any]) -> HistoryRecordResponse:
    return HistoryRecordResponse(
        id=UUID(str(row["id"])),
        user_id=UUID(str(row["user_id"])),
        food_code=row.get("food_code"),
        location=row.get("location_json") or {},
        radius_meters=row.get("radius_meters"),
        tags=row.get("tags_json") or [],
        recommendation_snapshot=row.get("recommendation_snapshot") or {},
        result_count=int(row.get("result_count") or 0),
        poi_provider=row.get("poi_provider"),
        created_at=_parse_ts(row.get("created_at")),
        updated_at=_parse_ts(row.get("updated_at")),
    )


def _parse_ts(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        # 处理 ISO 格式（Z / 带时区）
        s = value.replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(s)
        except ValueError:
            pass
    # 兜底：当前时间
    return datetime.now().astimezone()


# ============== 路由实现 ==============

@router.post("", response_model=HistoryRecordResponse, status_code=status.HTTP_201_CREATED)
async def create_history(
    payload: HistoryWriteRequest,
    current: Annotated[CurrentUser, Depends(get_current_user)],
    settings: Annotated[Settings, Depends(get_settings)],
    sb: Annotated[SupabaseAdminClient | None, Depends(get_supabase_admin)] = None,
) -> HistoryRecordResponse:
    sb_ok = _require_sb(sb)
    log.info("history_write user=%s result_count=%s food_code=%s", current.user_id, payload.result_count, payload.food_code)
    return write_user_recommendation(sb=sb_ok, user=current, payload=payload)


@router.get("", response_model=HistoryListResponse)
async def list_history(
    current: Annotated[CurrentUser, Depends(get_current_user)],
    sb: Annotated[SupabaseAdminClient | None, Depends(get_supabase_admin)] = None,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> HistoryListResponse:
    sb_ok = _require_sb(sb)
    # 1) 强制过滤 user_id + 排序
    query = (
        sb_ok.client.table("user_recommendations")
        .select("*")
        .eq("user_id", str(current.user_id))
        .order("created_at", desc=True)
    )
    # 2) 取总数（先不加 limit/offset 做 count）
    #    Supabase client 不能同时做 select * + count；分两次或用 header。
    #    MVP 用子查询 count（简便写法）：
    count_res = sb_ok.client.table("user_recommendations").select("id", count=CountMethod.exact).eq("user_id", str(current.user_id)).execute()
    total = int(count_res.count or 0)

    # 3) 取分页数据
    data_res = query.range(offset, offset + limit - 1).execute()
    rows = list(data_res.data or [])
    items = [_row_to_response(cast(dict[str, Any], r)) for r in rows]

    return HistoryListResponse(items=items, total=total, limit=limit, offset=offset)


@router.delete("/{record_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_history_item(
    record_id: UUID,
    current: Annotated[CurrentUser, Depends(get_current_user)],
    sb: Annotated[SupabaseAdminClient | None, Depends(get_supabase_admin)] = None,
) -> None:
    """删除单条；若不归当前用户则抛 404（防越权枚举）。"""
    sb_ok = _require_sb(sb)
    delete_res = (
        sb_ok.client.table("user_recommendations")
        .delete()
        .eq("id", str(record_id))
        .eq("user_id", str(current.user_id))
        .execute()
    )
    rows = list(delete_res.data or [])
    if not rows:
        raise HTTPException(status_code=404, detail="历史记录不存在或不归你所有")
    log.info("history_delete_one user=%s id=%s", current.user_id, record_id)


@router.delete("", response_model=HistoryDeleteAllResponse)
async def delete_all_history(
    current: Annotated[CurrentUser, Depends(get_current_user)],
    sb: Annotated[SupabaseAdminClient | None, Depends(get_supabase_admin)] = None,
) -> HistoryDeleteAllResponse:
    """清空当前用户的所有历史；返回删除条数。"""
    sb_ok = _require_sb(sb)
    delete_res = (
        sb_ok.client.table("user_recommendations")
        .delete(count=CountMethod.exact)
        .eq("user_id", str(current.user_id))
        .execute()
    )
    deleted = int(delete_res.count or 0)
    log.info("history_delete_all user=%s deleted=%s", current.user_id, deleted)
    return HistoryDeleteAllResponse(deleted=deleted)
