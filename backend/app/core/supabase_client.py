"""Supabase 客户端封装。

- 后端仅使用 service_role key 访问（绕过 RLS，🔒敏感，绝对不要发给前端。
- 生命周期：
  - `get_supabase_admin()`：单例 httpx.AsyncClient。
  - 测试/开发可通过 override 注入 AsyncMock 替换实现，不需要真实 Supabase。
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


def _build_admin_client(settings: Settings) -> SupabaseClient:
    """基于 settings 创建 Supabase 客户端（必须 service_role key）。"""
    if not settings.supabase_url or not settings.supabase_service_role_key:
        msg = "Supabase 凭证缺失（SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY 至少一项未配置）"
        raise RuntimeError(msg)
    return create_client(settings.supabase_url, settings.supabase_service_role_key)


async def get_supabase_admin(
    settings: Annotated[Settings, Depends(get_settings)],
) -> AsyncIterator[SupabaseAdminClient]:
    """FastAPI Depends：获取 service_role 级别的 Supabase 客户端。

    使用方式：
        async def my_route(sb: Annotated[SupabaseAdminClient, Depends(get_supabase_admin)]):
            ...
    """
    admin_client = _build_admin_client(settings)
    yield SupabaseAdminClient(client=admin_client, settings=settings)
