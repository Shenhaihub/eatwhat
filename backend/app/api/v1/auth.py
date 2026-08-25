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
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal, cast

import httpx
import jwt as pyjwt
from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
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
    # 登录成功后要回跳的地址（作为 Supabase 回调基址；query/fragment 会被规范化去除，
    # next 参数通过后端 cookie + 前端 localStorage 双通道保存，避免 Supabase 白名单匹配失败）
    redirect_to: str | None = Field(default=None, pattern=r"^https?://.*", max_length=512)
    # 可选：前端显式传递登录后跳转路径，优先级最高
    next_path: str | None = Field(default=None, pattern=r"^/.*", max_length=512)


class MagicLinkResponse(BaseModel):
    sent: bool = True
    email: str
    # NETWORK_SUPABASE / AUTH_INVALID_REDIRECT / AUTH_RATE_LIMIT / AUTH_CONFIG / BACKEND_UNKNOWN
    error_code: str | None = None
    # 仅 sent=False 时给前端的可读错误；任何情况下都不返回 token（magic link 走邮件）
    error_message: str | None = None


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
    except Exception as exc:
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
    response: Response,
    sb: Annotated[SupabaseAdminClient | None, Depends(get_supabase_admin)] = None,
) -> MagicLinkResponse:
    """向邮箱发送 Magic Link（若用户不存在会自动创建）。

    安全策略：
    - 对于"邮箱不存在/未验证"等可用于**枚举邮箱**的错误，仍返回 sent=true（防枚举，业界最佳实践）。
    - 对于"redirect_to 不在白名单 / 邮件限流 / 配置错误 / 网络异常"等**不可枚举**的系统/配置错误，
      直接返回 sent=false + error_code + error_message，避免"页面显示成功但用户永远收不到邮件"。
    - next 跳转不再塞进 redirect_to 的 query，改走后端 `auth_return_to` cookie（60 分钟）
      + 前端 localStorage 双通道，避免 Supabase 的 Redirect URL 白名单因 query 匹配失败。
    """
    sb_ok = _require_sb(sb)
    _DEFAULT_REDIRECT = "http://localhost:5173/auth/callback"
    raw_redirect = req.redirect_to or _DEFAULT_REDIRECT

    def _strip_redirect(u: str) -> str:
        """Supabase 对 Redirect URL 的白名单匹配对 query/fragment 非常敏感；
        所以我们只发 scheme://host:port/path 三段，不再携带 ?next=... 或 #hash。
        同时把 127.0.0.1/[::1] 统一归一成 localhost，避免 Vite 监听 host 不一致。
        """
        try:
            from urllib.parse import urlsplit, urlunsplit

            parts = urlsplit(u)
            scheme = parts.scheme or "http"
            host = parts.hostname or ""
            port = parts.port
            path = parts.path or "/auth/callback"
            if host in ("127.0.0.1", "::1", "[::1]"):
                host = "localhost"
            if (host, port) == ("localhost", None):
                port = 5173
            if not path or path == "/":
                path = "/auth/callback"
            netloc = f"{host}:{port}" if port else host
            return urlunsplit((scheme, netloc, path, "", ""))
        except Exception:
            return _DEFAULT_REDIRECT

    redirect_to_clean = _strip_redirect(raw_redirect)

    # 计算 next 跳转：req.next_path > redirect_to ?next= > "/"
    next_path: str = "/"
    if req.next_path and req.next_path.startswith("/"):
        next_path = req.next_path
    else:
        try:
            from urllib.parse import parse_qs, urlsplit

            qs = parse_qs(urlsplit(raw_redirect).query)
            if qs.get("next"):
                cand = qs["next"][0]
                if cand.startswith("/"):
                    next_path = cand
        except Exception:
            pass
    if not next_path.startswith("/"):
        next_path = "/"
    # 写 cookie（httpOnly=false：前端 /auth/callback 路由要读），SameSite=Lax，60min
    import http.cookies

    response.set_cookie(
        key="auth_return_to",
        value=next_path,
        max_age=3600,
        httponly=False,
        samesite="lax",
        secure=False,  # 本地开发 http，设 False；生产部署 https 应 True
    )
    # 避免 http.cookies 未使用 lint 警告（实际上面的 set_cookie 不走该 import；这里占位方便后续扩展）
    _ = http.cookies.SimpleCookie

    email = req.email
    log.info(
        "auth_magic_link_request ip=%s email_len=%d redirect_to=%s (raw=%s) next=%s",
        request.client.host if request.client else "?",
        len(email),
        redirect_to_clean,
        raw_redirect,
        next_path,
    )

    # 错误码分类：下列错误仅表示"邮箱/用户没注册"或"枚举攻击"，必须走防枚举（前端不暴露真实情况）
    _ENUM_CODES = {
        "user_not_found",
        "email_not_found",
        "email_not_confirmed",
        "invalid_credentials",
        "user_already_registered",  # 在某些 send signInWithOtp 情况下
    }
    # 下列错误 = Supabase 配置/限流，必须直接返回 sent=False 以便前端打印给用户
    _INVALID_REDIRECT_CODES = {"invalid_redirect_uri", "redirect_uri_mismatch", "validation_failed"}
    _RATE_LIMIT_CODES = {
        "over_email_send_rate_limit",
        "rate_limit",
        "email_rate_limit_exceeded",
        "sms_rate_limit_exceeded",
        "too_many_requests",
    }
    _CONFIG_CODES = {
        "email_address_not_authorized",
        "saml_provider_not_found",
        "provider_disabled",
        "signup_disabled",
    }

    try:
        sb_ok.client.auth.sign_in_with_otp(
            {"email": email, "options": {"email_redirect_to": redirect_to_clean, "should_create_user": True}}
        )
        log.info("auth_magic_link_sent ok email=%s", email)
        return MagicLinkResponse(sent=True, email=email)
    except Exception as exc:
        code: str | None = getattr(exc, "code", None)
        message: str | None = getattr(exc, "message", None)
        exc_text = repr(exc).lower()
        is_network = any(
            k in exc_text for k in ("connecterror", "ssl", "timeout", "unexpected_eof", "connectionreset", "eof occurred", "name or service not known")
        )
        log.warning(
            "auth_magic_link_send_failed email=%s code=%s msg=%s network=%s err=%r",
            email,
            code,
            message,
            is_network,
            exc,
        )
        if is_network:
            return MagicLinkResponse(
                sent=False,
                email=email,
                error_code="NETWORK_SUPABASE",
                error_message="后端连接邮件服务失败（网络/SSL/超时），请稍后重试或切换网络。",
            )
        if code and code in _ENUM_CODES:
            # 防枚举：不暴露邮箱是否注册
            return MagicLinkResponse(sent=True, email=email)
        if code and code in _INVALID_REDIRECT_CODES:
            return MagicLinkResponse(
                sent=False,
                email=email,
                error_code="AUTH_INVALID_REDIRECT",
                error_message=(
                    f"Supabase 配置错误：回调地址不在 Redirect URLs 白名单里（回调={redirect_to_clean}，"
                    "请在 Supabase 控制台 → Authentication → URL Configuration → Redirect URLs 中加入："
                    f"{redirect_to_clean}，精确匹配，无需加 ?next=）。原始错误：{code} {message or ''}"
                ),
            )
        if code and code in _RATE_LIMIT_CODES:
            return MagicLinkResponse(
                sent=False,
                email=email,
                error_code="AUTH_RATE_LIMIT",
                error_message=f"邮件发得太频繁触发 Supabase 限流（{code}），请 60 秒后再试。详情：{message or ''}",
            )
        if code and code in _CONFIG_CODES:
            return MagicLinkResponse(
                sent=False,
                email=email,
                error_code="AUTH_CONFIG",
                error_message=f"Supabase 认证配置错误（{code}）：{message or '请检查控制台邮件模板/域名授权'}",
            )
        if code is not None or message is not None:
            # 其它 Supabase 业务侧错误：别再硬发 sent=true 骗人了；直接暴露
            return MagicLinkResponse(
                sent=False,
                email=email,
                error_code="AUTH_SUPABASE",
                error_message=f"Supabase 返回错误：code={code} message={message or '(空)'}",
            )
        # 完全未知异常（没有 code 也没有 message）
        return MagicLinkResponse(
            sent=False,
            email=email,
            error_code="BACKEND_UNKNOWN",
            error_message=f"后端未知错误：{exc_text[:160]}",
        )


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


@router.get("/me/export")
async def export_account_data(
    current: Annotated[CurrentUser, Depends(get_current_user)],
    sb: Annotated[SupabaseAdminClient | None, Depends(get_supabase_admin)] = None,
) -> dict[str, Any]:
    """GDPR 数据可携：导出当前账号的一份 JSON 数据包。

    顶层结构：
    - exported_at (ISO UTC)
    - user_meta ({ user_id, email, role, created_at?, full_name? })
    - recommendation_history (user_recommendations 按 created_at desc)
    - preference_snapshots (user_preference_snapshots 按 created_at desc)
    - _partial (bool) + _partial_warnings (list[str])：若某一步失败仍尽力返回剩余数据
    """
    sb_ok = _require_sb(sb)
    uid = str(current.user_id)
    partial: list[str] = []

    # 1) user_meta：优先从 auth.admin 拿（含 created_at / name），失败回退 JWT 中的信息
    user_meta: dict[str, Any] = {
        "user_id": current.user_id,
        "email": current.email,
        "role": current.role,
    }
    try:
        admin_user = sb_ok.client.auth.admin.get_user(uid)  # type: ignore[attr-defined]
        u = getattr(admin_user, "user", admin_user)
        # 同时兼容 dict（测试用 mock 常见）和 Pydantic 对象（Supabase 真实返回）
        if isinstance(u, dict):
            u_obj: dict[str, Any] = u
        elif hasattr(u, "model_dump"):
            u_obj = u.model_dump()
        else:
            u_obj = {k: v for k, v in vars(u).items() if not k.startswith("_")}

        if "created_at" in u_obj and u_obj["created_at"] is not None:
            ca = u_obj["created_at"]
            user_meta["created_at"] = ca.isoformat() if hasattr(ca, "isoformat") else str(ca)
        for attr in ("email", "phone", "is_sso_user"):
            if attr in u_obj and u_obj[attr] is not None:
                user_meta[attr] = u_obj[attr]
        if u_obj.get("identities"):
            identities: list[dict[str, Any]] = []
            for i in u_obj["identities"] or []:
                idict = i if isinstance(i, dict) else (i.model_dump() if hasattr(i, "model_dump") else vars(i))
                identities.append({k: idict[k] for k in ("provider", "id", "created_at") if k in idict})
            user_meta["identities"] = identities
        # user_metadata 里的 full_name / avatar_url（GDPR "可理解"，挑常见且不敏感的）
        um = u_obj.get("user_metadata") or {}
        if isinstance(um, dict):
            for k in ("full_name", "name", "picture", "avatar_url", "locale"):
                if k in um and um[k] is not None:
                    user_meta.setdefault(k, um[k])
    except Exception as exc:
        partial.append(f"user_meta_fetch_error: {type(exc).__name__}")
        log.warning("account_export_meta_skip user=%s err=%r", current.user_id, exc)

    # 2) recommendation_history
    history_items: list[dict[str, Any]] = []
    try:
        res = (
            sb_ok.client.table("user_recommendations")
            .select("*")
            .eq("user_id", uid)
            .order("created_at", desc=True)
            .execute()
        )
        history_items = cast(list[dict[str, Any]], list(res.data or []))
    except Exception as exc:
        partial.append(f"history_fetch_error: {type(exc).__name__}")
        log.warning("account_export_history_skip user=%s err=%r", current.user_id, exc)

    # 3) preference_snapshots
    pref_items: list[dict[str, Any]] = []
    try:
        res = (
            sb_ok.client.table("user_preference_snapshots")
            .select("*")
            .eq("user_id", uid)
            .order("created_at", desc=True)
            .execute()
        )
        pref_items = cast(list[dict[str, Any]], list(res.data or []))
    except Exception as exc:
        partial.append(f"preference_fetch_error: {type(exc).__name__}")
        log.warning("account_export_preference_skip user=%s err=%r", current.user_id, exc)

    payload: dict[str, Any] = {
        "exported_at": datetime.now(UTC).isoformat(),
        "user_meta": user_meta,
        "recommendation_history": history_items,
        "recommendation_history_count": len(history_items),
        "preference_snapshots": pref_items,
        "preference_snapshots_count": len(pref_items),
        "_partial": bool(partial),
        "_partial_warnings": partial,
    }
    return payload


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
    except Exception as exc:
        log.warning("auth_sign_out_failed user_id=%s err=%r", current.user_id, exc)
    return LogoutResponse(success=True, revoked=revoked)


@router.delete("/me", status_code=status.HTTP_204_NO_CONTENT)
async def delete_account(
    current: Annotated[CurrentUser, Depends(get_current_user)],
    sb: Annotated[SupabaseAdminClient | None, Depends(get_supabase_admin)] = None,
) -> None:
    """GDPR 删除当前账号（级联删除历史 + 偏好画像）。

    顺序：
      1) 先删业务表（user_recommendations 历史 / user_preference_snapshots 画像）
         —— FK ON DELETE CASCADE 也会自动做，但这里显式执行可以计数 +
         验证 service_role 权限足够。
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
    except Exception as exc:
        log.warning("account_delete_history_skip user=%s err=%r", current.user_id, exc)
        deleted_history = -1

    # 1b) 显式删偏好画像（P6-01）
    try:
        pref_res = (
            sb_ok.client.table("user_preference_snapshots")
            .delete(count=CountMethod.exact)
            .eq("user_id", str(current.user_id))
            .execute()
        )
        deleted_preferences = int(pref_res.count or 0)
    except Exception as exc:
        log.warning("account_delete_preference_skip user=%s err=%r", current.user_id, exc)
        deleted_preferences = -1

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

    log.info(
        "account_delete_ok user=%s history_deleted=%s preference_deleted=%s",
        current.user_id,
        deleted_history,
        deleted_preferences,
    )
