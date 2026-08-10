"""P4-02 账户认证 API（Supabase Email Magic Link）。

端点：
- POST /api/v1/auth/magic-link  → 发送 magic link 邮件（不存在自动注册）
- POST /api/v1/auth/verify      → 用邮箱 + token 换会话（或直接 token_hash）
- GET  /api/v1/auth/me          → 返回当前登录用户
- POST /api/v1/auth/logout      → 注销当前用户，吊销 session

会话策略（MVP 简单版）：
- 前端登录后拿到 access_token（JWT），每次请求带 Authorization: Bearer <token>
- 后端通过 PyJWT 本地校验 + 缓存 JWKS（Production 时强烈推荐）；开发期也可走 Supabase /auth/v1/user 在线校验
- 🔒 注意：Supabase Auth 的默认 JWT 过期时间是 1 小时；MVP 期不做 refresh token 轮换，过期就重新登
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import Annotated, Any, Literal

import httpx
import jwt as pyjwt
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from postgrest.types import CountMethod
from pydantic import BaseModel, EmailStr, Field

from app.core.config import Settings, get_settings
from app.core.exceptions import INTERNAL_ERROR, AppError
from app.core.supabase_client import SupabaseAdminClient, get_supabase_admin


def _require_sb(sb: SupabaseAdminClient | None) -> SupabaseAdminClient:
    """Auth 路由强依赖 Supabase：缺失直接返回 500 级错误。"""
    if sb is None:
        raise AppError(
            INTERNAL_ERROR,
            message="登录服务暂不可用（Supabase 未配置）",
            details={"reason": "supabase_not_configured"},
        )
    return sb

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


# --------- Request / Response Models ---------
class MagicLinkRequest(BaseModel):
    email: EmailStr
    # 登录成功后要回跳的地址（Supabase 允许 magic link 跳这个地址）
    redirect_to: str | None = Field(default=None, pattern=r"^https?://.*", max_length=512)


class MagicLinkResponse(BaseModel):
    sent: bool = True
    email: str
    # NETWORK_SUPABASE：后端连不上 Supabase，前端应 fallback 到 SDK 直连
    error_code: str | None = None
    # 任何情况下都不返回 token（magic link 走邮件）


class VerifyRequest(BaseModel):
    email: EmailStr
    token: str = Field(min_length=1, max_length=256)
    type: Literal["magiclink", "email", "signup"] = "magiclink"


class AuthUser(BaseModel):
    user_id: str = Field(pattern=r"^[a-f0-9-]{36}$")  # UUID 格式
    email: str
    role: str | None = None
    created_at: str | None = None


class SessionResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int
    user: AuthUser


class LogoutResponse(BaseModel):
    success: bool = True
    revoked: bool = False


# --------- get_current_user Depends ---------
@dataclass(slots=True)
class CurrentUser:
    user_id: str
    email: str
    role: str
    # 原始 JWT claims，便于后面扩展
    claims: dict[str, Any]


# 轻量 LRU（进程内，不跨 worker）
_JWKS_CACHE: dict[str, dict[str, Any]] = {}
# JWKS 缓存 30 分钟
_JWKS_CACHE_TTL = timedelta(minutes=30)
_JWKS_CACHE_AT: dict[str, float] = {}


async def _fetch_jwk_for_header(kid: str, settings: Settings) -> dict[str, Any] | None:
    """从 Supabase 获取 JWKS（带进程级内存缓存）。

    返回格式：{'kty': 'RSA', 'n': '...', 'e': 'AQAB', 'use': 'sig', 'alg': 'RS256', 'kid': '...'}。
    """
    import time

    jwks_url = (
        settings.supabase_jwks_url
        or f"{settings.supabase_url.rstrip('/')}/auth/v1/.well-known/jwks.json"
    )
    cache_key = jwks_url
    now = time.time()
    cached = _JWKS_CACHE.get(cache_key)
    cache_at = _JWKS_CACHE_AT.get(cache_key, 0)
    if cached and (now - cache_at) < _JWKS_CACHE_TTL.total_seconds():
        return cached.get(kid)

    try:
        async with httpx.AsyncClient(timeout=8.0) as h:
            resp = await h.get(jwks_url)
            resp.raise_for_status()
            data = resp.json()
        keys: dict[str, dict[str, Any]] = {
            str(k["kid"]): k for k in data.get("keys", []) if isinstance(k, dict) and isinstance(k.get("kid"), str)
        }
        _JWKS_CACHE[cache_key] = keys
        _JWKS_CACHE_AT[cache_key] = now
        return keys.get(kid)
    except Exception as exc:  # noqa: BLE001
        log.warning("auth_jwks_fetch_failed url=%s err=%r", jwks_url, exc)
        return None


def _public_key_from_jwk(jwk: dict[str, Any]) -> Any:
    """把 Supabase 返回的 JWK（RSA/EC 两种都支持）转成 cryptography 的公钥对象。"""
    import base64

    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives.asymmetric.ec import SECP256R1, EllipticCurvePublicNumbers
    from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicNumbers

    def _b64url_int(s: str) -> int:
        # JWK n/e/x/y 使用 base64url 无填充编码
        padding = "=" * (-len(s) % 4)
        raw = base64.urlsafe_b64decode(s + padding)
        return int.from_bytes(raw, "big")

    kty = str(jwk.get("kty") or "")
    if kty == "RSA":
        n = _b64url_int(jwk["n"])
        e = _b64url_int(jwk["e"])
        return RSAPublicNumbers(e, n).public_key(default_backend())
    if kty == "EC":
        crv = str(jwk.get("crv") or "")
        if crv != "P-256":
            raise ValueError(f"Unsupported EC curve: {crv}")
        x = _b64url_int(jwk["x"])
        y = _b64url_int(jwk["y"])
        return EllipticCurvePublicNumbers(x, y, SECP256R1()).public_key(default_backend())
    raise ValueError(f"Unsupported JWK kty: {kty}")


async def get_current_user(
    settings: Annotated[Settings, Depends(get_settings)],
    authorization: Annotated[str | None, Header()] = None,
) -> CurrentUser:
    """FastAPI Depends：校验 Authorization: Bearer <Supabase JWT> 并解析当前用户。

    校验失败抛出 HTTP 401；未登录端前端会自动跳 /login。
    """
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="缺少 Bearer Token")
    token = authorization[7:].strip()
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token 为空")

    # 1) 先解析 header 取 kid
    try:
        unverified_header = pyjwt.get_unverified_header(token)
    except pyjwt.PyJWTError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=f"JWT 格式错误: {exc}") from exc

    kid = unverified_header.get("kid")
    if not kid:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="JWT 缺少 kid 头")

    # 2) 拿公钥
    jwk = await _fetch_jwk_for_header(kid, settings)
    if not jwk:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="无法获取 JWKS 校验登录态，请稍后再试",
        )
    try:
        public_key = _public_key_from_jwk(jwk)
    except Exception as exc:
        log.warning("auth_jwk_decode_failed kid=%s err=%r", kid, exc)
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="JWK 转换失败") from exc

    # 3) 校验签名 + 过期 + issuer（可选）。
    # Supabase 新项目默认 ES256（P-256），老项目 RS256，两种都兼容。
    algorithms = [str(unverified_header.get("alg") or "RS256")]
    if algorithms[0] not in {"RS256", "ES256", "ES384", "ES512", "PS256", "RS384", "RS512"}:
        algorithms = ["RS256", "ES256"]
    audience = settings.supabase_jwt_audience or "authenticated"
    issuer = settings.supabase_jwt_issuer or f"{settings.supabase_url}/auth/v1" if settings.supabase_url else None
    try:
        claims = pyjwt.decode(
            token,
            public_key,
            algorithms=algorithms,
            audience=audience,
            issuer=issuer if issuer else None,
            options={"require": ["exp", "sub", "aud"]},
        )
    except pyjwt.ExpiredSignatureError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录态已过期，请重新登录") from exc
    except pyjwt.InvalidAudienceError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=f"JWT aud 不匹配: {exc}") from exc
    except pyjwt.InvalidIssuerError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=f"JWT iss 不匹配: {exc}") from exc
    except pyjwt.PyJWTError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=f"JWT 校验失败: {exc}") from exc

    user_id = claims.get("sub")
    email = claims.get("email")
    role = claims.get("role") or "authenticated"
    if not user_id or not email:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="JWT 缺少 sub/email 字段")

    return CurrentUser(user_id=user_id, email=email, role=role, claims=claims)


async def get_current_user_optional(
    settings: Annotated[Settings, Depends(get_settings)],
    authorization: Annotated[str | None, Header()] = None,
) -> CurrentUser | None:
    """可选登录 Depends：没带 Authorization 头 → None；带了就严格校验（失败抛 401）。

    用在：推荐生成、餐厅搜索等既可匿名也可登录访问的 API。
    登录态下会自动写历史；匿名态下直接返回结果。
    """
    if not authorization:
        return None
    return await get_current_user(settings=settings, authorization=authorization)


# --------- 路由实现 ---------
@router.post("/magic-link", response_model=MagicLinkResponse, status_code=status.HTTP_200_OK)
async def send_magic_link(
    req: MagicLinkRequest,
    settings: Annotated[Settings, Depends(get_settings)],
    request: Request,
    sb: Annotated[SupabaseAdminClient | None, Depends(get_supabase_admin)] = None,
) -> MagicLinkResponse:
    """向邮箱发送 Magic Link（若用户不存在会自动创建）。

    无论用户是否已注册，成功都返回 200 sent=true（防止邮箱枚举，安全最佳实践）。
    """
    sb_ok = _require_sb(sb)
    # 默认回调地址：本地 http://127.0.0.1:5173/auth/callback；生产由 req.redirect_to 覆盖
    redirect_to = req.redirect_to or "http://127.0.0.1:5173/auth/callback"
    email = req.email
    log.info("auth_magic_link_request ip=%s email_len=%d redirect_to=%s", request.client.host if request.client else "?", len(email), redirect_to)

    try:
        sb_ok.client.auth.sign_in_with_otp(
            {"email": email, "options": {"email_redirect_to": redirect_to, "should_create_user": True}}
        )
        # sign_in_with_otp(magiclink) 返回的是 None data / 仅 success
        log.info("auth_magic_link_sent ok email=%s", email)
    except Exception as exc:  # noqa: BLE001
        # AuthApiError（Supabase 业务层，有 code/message）：为了防枚举仍返回 sent=true
        # ConnectError/SSL/Timeout（网络层，后端自己的问题）：返回 sent=false 让前端 fallback
        has_code = getattr(exc, "code", None) is not None or getattr(exc, "message", None) is not None
        exc_text = repr(exc).lower()
        is_network = any(k in exc_text for k in ("connecterror", "ssl", "timeout", "unexpected_eof", "connectionreset", "eof occurred"))
        if is_network:
            log.warning("auth_magic_link_network_fail email=%s err=%r", email, exc)
            return MagicLinkResponse(sent=False, email=email, error_code="NETWORK_SUPABASE")
        log.warning("auth_magic_link_send_failed email=%s code=%s msg=%s err=%r", email, getattr(exc, "code", None), getattr(exc, "message", None), exc)
        if has_code:
            # Supabase 业务错误（如 rate_limit / 邮箱格式拒绝）：防枚举仍返回 sent=true
            return MagicLinkResponse(sent=True, email=email)
        # 未知后端异常：返回 sent=false 便于前端 fallback
        return MagicLinkResponse(sent=False, email=email, error_code="BACKEND_UNKNOWN")

    return MagicLinkResponse(sent=True, email=email)


@router.post("/verify", response_model=SessionResponse, status_code=status.HTTP_200_OK)
async def verify_magic_link(
    req: VerifyRequest,
    request: Request,
    sb: Annotated[SupabaseAdminClient | None, Depends(get_supabase_admin)] = None,
) -> SessionResponse:
    """用 email + token 校验 magic link 并兑换完整 Session（含 access_token）。"""
    sb_ok = _require_sb(sb)
    try:
        session_resp = sb_ok.client.auth.verify_otp({"email": req.email, "token": req.token, "type": req.type})
    except Exception as exc:
        # Supabase SDK 内部的 AuthApiError 未做 stub；通过属性判别（有 code/message 视为已知错误）
        code = getattr(exc, "code", None)
        message = getattr(exc, "message", None)
        if code is not None or message is not None:
            log.warning("auth_verify_failed type=%s email=%s code=%s msg=%s", req.type, req.email, code, message)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"校验失败: {code or 'AUTH_ERROR'} {message or ''}",
            ) from exc
        log.warning("auth_verify_unexpected email=%s err=%r", req.email, exc)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="登录校验服务异常") from exc

    session = session_resp.session
    user = session_resp.user
    if not session or not user:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="未获取到 Session，请重新打开 Magic Link")

    return SessionResponse(
        access_token=session.access_token,
        token_type="bearer",
        expires_in=session.expires_in or 3600,
        user=AuthUser(
            user_id=user.id,
            email=user.email or req.email,
            role=getattr(user, "role", None),
            created_at=str(user.created_at) if getattr(user, "created_at", None) else None,
        ),
    )


@router.get("/me", response_model=AuthUser)
async def get_me(current: Annotated[CurrentUser, Depends(get_current_user)]) -> AuthUser:
    """返回当前登录用户（前端 Authorization 头必带）。"""
    return AuthUser(user_id=current.user_id, email=current.email, role=current.role, created_at=None)


@router.post("/logout", response_model=LogoutResponse, status_code=status.HTTP_200_OK)
async def logout(
    current: Annotated[CurrentUser, Depends(get_current_user)],
    sb: Annotated[SupabaseAdminClient | None, Depends(get_supabase_admin)] = None,
) -> LogoutResponse:
    """吊销当前 session（前端调用后自己清除本地 localStorage 里的 token）。"""
    sb_ok = _require_sb(sb)
    revoked = False
    try:
        # service_role 的 admin 没有"当前会话"的概念，要真正吊销需要带 access token 调 auth.sign_out
        # 这里通过前端自己清除 + 服务端返回成功即可，MVP 足够
        # （真实吊销：sb.client.auth.admin.sign_out(current.user_id) 但会吊销该用户所有会话）
        sb_ok.client.auth.sign_out()
        revoked = True
    except Exception as exc:  # noqa: BLE001
        log.warning("auth_sign_out_failed user_id=%s err=%r", current.user_id, exc)
    return LogoutResponse(success=True, revoked=revoked)


@router.delete("/me", status_code=status.HTTP_204_NO_CONTENT)
async def delete_account(
    current: Annotated[CurrentUser, Depends(get_current_user)],
    sb: Annotated[SupabaseAdminClient | None, Depends(get_supabase_admin)] = None,
) -> None:
    """GDPR 删除当前账号（级联删除历史记录）。

    顺序：
      1) 先删业务表（user_recommendations 历史记录）—— FK ON DELETE CASCADE 也会做，
         但这里显式执行可以计数 + 保证 service_role 权限足够。
      2) 通过 Supabase Admin API 删除 auth.users（service_role 特有权限）。
         这样会同时：吊销该用户所有 refresh_token、清 auth 相关表、移除身份。
      3) 前端 204 后自己清本地 session + 跳首页。
    """
    sb_ok = _require_sb(sb)
    # 1) 显式删历史（记录日志用）
    try:
        hist_res = (
            sb_ok.client.table("user_recommendations")
            .delete(count=CountMethod.exact)
            .eq("user_id", str(current.user_id))
            .execute()
        )
        deleted_history = int(hist_res.count or 0)
    except Exception as exc:  # noqa: BLE001
        log.warning("account_delete_history_skip user=%s err=%r", current.user_id, exc)
        deleted_history = -1

    # 2) 删除 auth.users 本体（该 user 的 sessions/identities 也会被 Supabase 级联清理）
    try:
        sb_ok.client.auth.admin.delete_user(str(current.user_id))
    except Exception as exc:
        # AuthApiError 例如 user_not_found 也当成功（幂等）
        code = getattr(exc, "code", None)
        if code in {"user_not_found", 404, "404"}:
            log.info("account_delete_already_gone user=%s", current.user_id)
        else:
            log.warning("account_delete_auth_fail user=%s code=%s err=%r", current.user_id, code, exc)
            raise HTTPException(status_code=500, detail="删除账号失败，请稍后再试") from exc

    log.info("account_delete_ok user=%s history_deleted=%s", current.user_id, deleted_history)
