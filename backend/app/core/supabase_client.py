"""Supabase 客户端封装。

- 后端仅使用 service_role key 访问（绕过 RLS，🔒敏感，绝对不要发给前端。
- 生命周期：
  - `get_supabase_admin()`：每次调用创建一个 client（FastAPI Depends 按请求调用）。
  - 测试/开发可通过 override 注入 AsyncMock 替换实现，不需要真实 Supabase。
- 凭证缺失策略：
  - 不抛 RuntimeError（会导致 FastAPI Depends 阶段就炸、连路由 handler 都进不去）。
  - 改为返回 None，由各路由 handler 自行判断：
    * 匿名无历史写入场景（POST /recommendations）→ `if sb is not None:` 短路即可。
    * 必须依赖 Supabase 的路由（History CRUD / DELETE /auth/me）→ 体内检查 None 抛 503 级错误。
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import Depends
from supabase import Client as SupabaseClient
from supabase import create_client

from app.core.config import Settings, get_settings


@dataclass(slots=True)
class SupabaseAdminClient:
    """持有 service_role 授权的 Supabase 客户端。"""

    client: SupabaseClient
    settings: Settings

    @property
    def auth_admin(self) -> Any:
        """快速访问 auth.admin 子 client（查用户、revoke sessions 等）。
        Supabase SDK 的 auth_admin 返回类型复杂且随版本变，这里用 Any 避免 mypy strict 下的类型噪声。"""
        return self.client.auth.admin


def _build_admin_client(settings: Settings) -> SupabaseClient | None:
    """基于 settings 创建 Supabase 客户端（必须 service_role key）。

    缺失凭证时返回 None（而非抛错），让 Depends 阶段顺利通过，把"是否真的需要 Supabase"的
    判断权交给具体业务 handler。
    """
    if not settings.supabase_url or not settings.supabase_service_role_key:
        return None
    return create_client(settings.supabase_url, settings.supabase_service_role_key)


async def get_supabase_admin(
    settings: Annotated[Settings, Depends(get_settings)],
) -> AsyncIterator[SupabaseAdminClient | None]:
    """FastAPI Depends：获取 service_role 级别的 Supabase 客户端，缺失时返回 None。

    使用方式：
        # 必须有 Supabase 的路由
        async def my_route(sb: Annotated[SupabaseAdminClient | None, Depends(get_supabase_admin)] = None):
            if sb is None:
                raise AppError(INTERNAL_ERROR, details={"reason": "supabase_not_configured"})
            ...

        # 可选依赖 Supabase 的路由（例如匿名推荐，历史写入失败不影响主流程）
        async def anon_route(sb: Annotated[SupabaseAdminClient | None, Depends(get_supabase_admin)] = None):
            if current_user is not None and sb is not None:
                write_user_recommendation(...)
            ...
    """
    admin_client = _build_admin_client(settings)
    if admin_client is None:
        yield None
        return
    yield SupabaseAdminClient(client=admin_client, settings=settings)
