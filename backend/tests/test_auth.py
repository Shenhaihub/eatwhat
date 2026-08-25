"""P4-02 Auth API 契约测试。

策略（不依赖真实 Supabase 外部资源，避免 flaky）：
- 通过 dependency_overrides 注入 mock SupabaseAdminClient 与假 Settings。
- 对 get_current_user 的 JWT 校验：替换 auth 模块内的 _fetch_jwk_for_header 与 pyjwt.decode，
  模拟 JWKS 命中/缺失、JWT 过期/aud/iss 错误等分支。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from app.api.v1 import auth as auth_api  # noqa: F401  确保模块内的 Depends cache 被导入
from app.core.config import Settings
from app.core.supabase_client import SupabaseAdminClient, get_supabase_admin
from app.main import create_app


# ---------- 构造假 JWT 工具 ----------
def _make_test_jwt(
    *,
    sub: str = "123e4567-e89b-12d3-a456-426614174000",
    email: str = "alice@example.com",
    role: str = "authenticated",
    exp_offset_seconds: int = 3600,
    aud: str = "authenticated",
    iss: str = "https://example.supabase.co/auth/v1",
) -> str:
    """生成一条假 JWT（header kid=testkid；签名校验在测试里被 mock 掉）。"""
    import base64
    import json

    header = {"alg": "RS256", "typ": "JWT", "kid": "testkid"}
    payload = {
        "sub": sub,
        "email": email,
        "role": role,
        "aud": aud,
        "iss": iss,
        "iat": int(datetime.now(tz=UTC).timestamp()),
        "exp": int((datetime.now(tz=UTC) + timedelta(seconds=exp_offset_seconds)).timestamp()),
    }

    def _b64url(data: dict[str, Any]) -> str:
        raw = json.dumps(data, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    # 第三段 signature 是假值没关系；decode 会被 mock
    return f"{_b64url(header)}.{_b64url(payload)}.FAKESIG"


# ---------- mock 基础依赖 ----------
@dataclass(slots=True)
class _FakeUser:
    id: str
    email: str | None
    role: str | None = None
    created_at: datetime | None = None


@dataclass(slots=True)
class _FakeSession:
    access_token: str
    expires_in: int | None = 3600


@dataclass(slots=True)
class _FakeVerifyResp:
    session: _FakeSession | None
    user: _FakeUser | None


def _build_app_with_overrides(
    *,
    sb: SupabaseAdminClient | None = None,
    settings: Settings | None = None,
) -> TestClient:
    """创建带 override 的 TestClient。"""
    from app.core.config import get_settings

    base_settings = settings or Settings(
        _env_file=None,
        app_env="test",
        app_mode="mock",
        supabase_url="https://example.supabase.co",
        supabase_service_role_key="eyJ_fake_service_role",
        supabase_anon_key="eyJ_fake_anon",
    )
    base_sb = sb or SupabaseAdminClient(client=AsyncMock(), settings=base_settings)  # type: ignore[arg-type]
    app = create_app(base_settings)
    app.dependency_overrides[get_supabase_admin] = lambda: (yield base_sb).__anext__()  # type: ignore[attr-defined]
    app.dependency_overrides[get_settings] = lambda: base_settings

    return TestClient(app)


# 由于 get_supabase_admin 是 AsyncIterator，上面的 lambda 写法有坑；
# 改用更稳妥的 override 方式：提供一个同步返回 fake_sb 的 Depends。
@pytest.fixture
def make_auth_client(monkeypatch: pytest.MonkeyPatch):
    def _make(
        *,
        fake_sb: SupabaseAdminClient | None = None,
        fake_settings: Settings | None = None,
    ) -> TestClient:
        from collections.abc import AsyncIterator

        from app.core.config import get_settings
        from app.core.supabase_client import get_supabase_admin

        base_settings = fake_settings or Settings(
            _env_file=None,
            app_env="test",
            app_mode="mock",
            supabase_url="https://example.supabase.co",
            supabase_service_role_key="eyJ_fake_service_role",
            supabase_anon_key="eyJ_fake_anon",
        )

        if fake_sb is None:
            fake_sb = SupabaseAdminClient(client=AsyncMock(), settings=base_settings)  # type: ignore[arg-type]

        async def _override_sb() -> AsyncIterator[SupabaseAdminClient]:
            yield fake_sb

        def _override_settings() -> Settings:
            return base_settings

        # monkeypatch 替换模块级全局依赖，避免 JWKS 真实网络请求
        monkeypatch.setattr(
            "app.api.v1.auth._fetch_jwk_for_header",
            lambda _kid, _s: {
                "kty": "RSA",
                "kid": "testkid",
                "n": "tZ8VKQ",  # 随便填；_public_key_from_jwk 也被 patch
                "e": "AQAB",
                "use": "sig",
                "alg": "RS256",
            },
        )
        monkeypatch.setattr(
            "app.api.v1.auth._public_key_from_jwk",
            lambda _jwk: object(),  # 只要非空即可；pyjwt.decode 被 patch
        )

        app = create_app(base_settings)
        # override 同步/异步 Depends
        app.dependency_overrides[get_supabase_admin] = _override_sb
        app.dependency_overrides[get_settings] = _override_settings
        return TestClient(app)

    return _make


# ============================================================
#  /magic-link
# ============================================================
class TestMagicLink:
    def test_send_success_returns_200_sent_true(self, monkeypatch) -> None:
        """发送成功 → 200 sent=true；无论邮箱是否存在（防枚举）。"""
        from collections.abc import AsyncIterator
        from unittest.mock import Mock

        from app.api.v1 import auth as a_mod
        from app.core.config import Settings, get_settings
        from app.core.supabase_client import get_supabase_admin

        captured: dict[str, Any] = {}
        mp = pytest.MonkeyPatch()
        try:
            async def _sb_factory() -> AsyncIterator[SupabaseAdminClient]:
                mock_auth = Mock()

                def _sign_otp(args: dict[str, Any]) -> object:
                    captured.update(args)
                    return Mock()

                mock_auth.sign_in_with_otp = _sign_otp
                mock_client = Mock()
                mock_client.auth = mock_auth
                s = Settings(
                    _env_file=None,
                    app_env="test",
                    supabase_url="https://example.supabase.co",
                    supabase_service_role_key="eyJ_fake",
                )
                yield SupabaseAdminClient(client=mock_client, settings=s)

            base_s = Settings(
                _env_file=None,
                app_env="test",
                supabase_url="https://example.supabase.co",
                supabase_service_role_key="eyJ_fake",
            )
            app = create_app(base_s)
            app.dependency_overrides[get_supabase_admin] = _sb_factory
            app.dependency_overrides[get_settings] = lambda: base_s
            mp.setattr(a_mod, "_fetch_jwk_for_header", lambda _k, _s: None)
            mp.setattr(a_mod, "_public_key_from_jwk", lambda _j: object())

            tc = TestClient(app)
            resp = tc.post(
                "/api/v1/auth/magic-link",
                json={"email": "alice@example.com", "redirect_to": "http://127.0.0.1:5173/auth/callback"},
            )
        finally:
            mp.undo()

        assert resp.status_code == 200
        body = resp.json()
        assert body["sent"] is True
        assert body["email"] == "alice@example.com"
        # 确认不回显 token 等敏感字段
        assert "access_token" not in body
        assert "token" not in body
        # 确认 sign_in_with_otp 被调用且 should_create_user=True
        assert captured.get("options", {}).get("should_create_user") is True
        assert captured.get("email") == "alice@example.com"
        assert captured.get("options", {}).get("email_redirect_to") == "http://localhost:5173/auth/callback"

    def test_auth_api_error_returns_sent_true_anti_enumeration(
        self, monkeypatch
    ) -> None:
        """Supabase 业务层 AuthApiError（带 code/message）→ sent=true，防邮箱枚举。"""
        from collections.abc import AsyncIterator
        from unittest.mock import Mock

        from app.api.v1 import auth as a_mod
        from app.core.config import get_settings
        from app.core.supabase_client import get_supabase_admin

        mp = pytest.MonkeyPatch()
        try:
            async def _sb_factory() -> AsyncIterator[SupabaseAdminClient]:
                mock_auth = Mock()

                class _FakeAuthErr(Exception):
                    code = "email_rate_limit_exceeded"
                    message = "Daily rate limit exceeded"

                mock_auth.sign_in_with_otp = Mock(side_effect=_FakeAuthErr())
                mock_client = Mock()
                mock_client.auth = mock_auth
                s = Settings(_env_file=None, app_env="test", supabase_url="https://x.sb.co", supabase_service_role_key="eyJ_X")
                yield SupabaseAdminClient(client=mock_client, settings=s)

            base_s = Settings(_env_file=None, app_env="test", supabase_url="https://x.sb.co", supabase_service_role_key="eyJ_X")
            app = create_app(base_s)
            app.dependency_overrides[get_supabase_admin] = _sb_factory
            app.dependency_overrides[get_settings] = lambda: base_s

            async def _jwks_ignore(_k, _s):
                return None

            mp.setattr(a_mod, "_fetch_jwk_for_header", _jwks_ignore)
            mp.setattr(a_mod, "_public_key_from_jwk", lambda _j: object())
            tc = TestClient(app)
            resp = tc.post("/api/v1/auth/magic-link", json={"email": "bob@example.com"})
        finally:
            mp.undo()

        assert resp.status_code == 200
        body = resp.json()
        # P7 之后：为了让用户明确知道"收不到邮件是因为限流/配置问题"，不再对所有 AuthApiError 伪装 sent=True（防枚举）
        # - 对 email_rate_limit_exceeded / invalid_redirect 这类"明确告诉用户"更有价值的错误：sent=false + 明确 error_code
        # - 防枚举仍然对 user_not_found / invalid_credentials 等 _ENUM_CODES 生效
        assert body["sent"] is False
        assert body["email"] == "bob@example.com"
        # 错误码对前端透明（防枚举的同时仍暴露给用户是限流问题）
        assert body.get("error_code") == "AUTH_RATE_LIMIT"
        assert body.get("error_message") and "rate_limit" in body["error_message"].lower()

    def test_network_error_returns_sent_false_triggers_frontend_fallback(
        self, monkeypatch
    ) -> None:
        """后端连不上 Supabase（SSL/ConnectError）→ sent=false + NETWORK_SUPABASE，让前端直连。"""
        from collections.abc import AsyncIterator
        from unittest.mock import Mock

        from app.api.v1 import auth as a_mod
        from app.core.config import get_settings
        from app.core.supabase_client import get_supabase_admin

        mp = pytest.MonkeyPatch()
        try:
            async def _sb_factory() -> AsyncIterator[SupabaseAdminClient]:
                mock_auth = Mock()
                # 复现本次遇到的真实 SSL EOF 错误
                mock_auth.sign_in_with_otp = Mock(
                    side_effect=Exception(
                        "ConnectError('[SSL: UNEXPECTED_EOF_WHILE_READING] EOF occurred in violation of protocol (_ssl.c:1032)')"
                    )
                )
                mock_client = Mock()
                mock_client.auth = mock_auth
                s = Settings(_env_file=None, app_env="test", supabase_url="https://x.sb.co", supabase_service_role_key="eyJ_X")
                yield SupabaseAdminClient(client=mock_client, settings=s)

            base_s = Settings(_env_file=None, app_env="test", supabase_url="https://x.sb.co", supabase_service_role_key="eyJ_X")
            app = create_app(base_s)
            app.dependency_overrides[get_supabase_admin] = _sb_factory
            app.dependency_overrides[get_settings] = lambda: base_s

            async def _jwks_ignore2(_k, _s):
                return None

            mp.setattr(a_mod, "_fetch_jwk_for_header", _jwks_ignore2)
            mp.setattr(a_mod, "_public_key_from_jwk", lambda _j: object())
            tc = TestClient(app)
            resp = tc.post("/api/v1/auth/magic-link", json={"email": "bob@example.com"})
        finally:
            mp.undo()

        assert resp.status_code == 200
        body = resp.json()
        assert body["sent"] is False
        assert body["error_code"] == "NETWORK_SUPABASE"

    def test_unknown_backend_error_returns_sent_false(
        self, monkeypatch
    ) -> None:
        """RuntimeError 等非网络非业务未知异常 → sent=false，前端可告警/fallback。"""
        from collections.abc import AsyncIterator
        from unittest.mock import Mock

        from app.api.v1 import auth as a_mod
        from app.core.config import get_settings
        from app.core.supabase_client import get_supabase_admin

        mp = pytest.MonkeyPatch()
        try:
            async def _sb_factory() -> AsyncIterator[SupabaseAdminClient]:
                mock_auth = Mock()
                mock_auth.sign_in_with_otp = Mock(side_effect=RuntimeError("boom"))
                mock_client = Mock()
                mock_client.auth = mock_auth
                s = Settings(_env_file=None, app_env="test", supabase_url="https://x.sb.co", supabase_service_role_key="eyJ_X")
                yield SupabaseAdminClient(client=mock_client, settings=s)

            base_s = Settings(_env_file=None, app_env="test", supabase_url="https://x.sb.co", supabase_service_role_key="eyJ_X")
            app = create_app(base_s)
            app.dependency_overrides[get_supabase_admin] = _sb_factory
            app.dependency_overrides[get_settings] = lambda: base_s

            async def _jwks_ignore3(_k, _s):
                return None

            mp.setattr(a_mod, "_fetch_jwk_for_header", _jwks_ignore3)
            mp.setattr(a_mod, "_public_key_from_jwk", lambda _j: object())
            tc = TestClient(app)
            resp = tc.post("/api/v1/auth/magic-link", json={"email": "bob@example.com"})
        finally:
            mp.undo()

        assert resp.status_code == 200
        body = resp.json()
        assert body["sent"] is False
        assert body["error_code"] == "BACKEND_UNKNOWN"

    def test_invalid_email_returns_422_validation_error(self, make_auth_client) -> None:
        tc = make_auth_client()
        resp = tc.post("/api/v1/auth/magic-link", json={"email": "not-an-email"})
        assert resp.status_code == 422
        # 错误结构符合统一契约
        body = resp.json()
        assert "error" in body
        assert body["error"]["code"] == "VALIDATION_ERROR"

    def test_redirect_to_not_http_scheme_returns_422(self, make_auth_client) -> None:
        tc = make_auth_client()
        resp = tc.post(
            "/api/v1/auth/magic-link",
            json={"email": "a@b.com", "redirect_to": "javascript:alert(1)"},
        )
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


# ============================================================
#  /verify
# ============================================================
class TestVerify:
    def test_verify_ok_returns_session_and_user(self, monkeypatch) -> None:
        from collections.abc import AsyncIterator
        from unittest.mock import Mock

        from app.api.v1 import auth as a_mod
        from app.core.config import get_settings
        from app.core.supabase_client import get_supabase_admin

        mp = pytest.MonkeyPatch()
        try:
            async def _sb_factory() -> AsyncIterator[SupabaseAdminClient]:
                mock_auth = Mock()
                def _verify(args):
                    fake_session = _FakeSession(access_token="ACCESS_abc", expires_in=3600)
                    fake_user = _FakeUser(
                        id="123e4567-e89b-12d3-a456-426614174000",
                        email="alice@example.com",
                        role="authenticated",
                        created_at=datetime(2024, 1, 1, tzinfo=UTC),
                    )
                    return _FakeVerifyResp(session=fake_session, user=fake_user)
                mock_auth.verify_otp = _verify
                mock_client = Mock()
                mock_client.auth = mock_auth
                s = Settings(_env_file=None, app_env="test", supabase_url="https://x.sb.co", supabase_service_role_key="eyJ_X")
                yield SupabaseAdminClient(client=mock_client, settings=s)

            base_s = Settings(_env_file=None, app_env="test", supabase_url="https://x.sb.co", supabase_service_role_key="eyJ_X")
            app = create_app(base_s)
            app.dependency_overrides[get_supabase_admin] = _sb_factory
            app.dependency_overrides[get_settings] = lambda: base_s

            async def _jwks_hit2(_k, _s):
                return None

            mp.setattr(a_mod, "_fetch_jwk_for_header", _jwks_hit2)
            mp.setattr(a_mod, "_public_key_from_jwk", lambda _j: object())
            tc = TestClient(app)
            resp = tc.post(
                "/api/v1/auth/verify",
                json={"email": "alice@example.com", "token": "OTP_abc123", "type": "magiclink"},
            )
        finally:
            mp.undo()

        assert resp.status_code == 200
        body = resp.json()
        assert body["access_token"] == "ACCESS_abc"
        assert body["token_type"] == "bearer"
        assert body["expires_in"] == 3600
        assert body["user"]["user_id"] == "123e4567-e89b-12d3-a456-426614174000"
        assert body["user"]["email"] == "alice@example.com"
        assert body["user"]["role"] == "authenticated"
        assert body["user"]["created_at"] == "2024-01-01 00:00:00+00:00"

    def test_verify_otp_invalid_returns_401(self, monkeypatch) -> None:
        """模拟 Supabase 返回 AuthApiError → 401，统一错误结构。"""
        from collections.abc import AsyncIterator
        from unittest.mock import Mock

        from app.api.v1 import auth as a_mod
        from app.core.config import get_settings
        from app.core.supabase_client import get_supabase_admin

        class _FakeAuthApiError(Exception):
            def __init__(self, code: str, message: str) -> None:
                super().__init__(message)
                self.code = code
                self.message = message

        # gotrue.errors.AuthApiError 可能没装；用 monkeypatch 在模块里伪造名字
        import sys
        import types
        fake_gotrue_errors = types.ModuleType("gotrue.errors")
        fake_gotrue_errors.AuthApiError = _FakeAuthApiError  # type: ignore[attr-defined]
        sys.modules["gotrue.errors"] = fake_gotrue_errors

        mp = pytest.MonkeyPatch()
        try:
            async def _sb_factory() -> AsyncIterator[SupabaseAdminClient]:
                mock_auth = Mock()
                mock_auth.verify_otp = Mock(side_effect=_FakeAuthApiError(code="OTP_EXPIRED", message="链接已过期"))
                mock_client = Mock()
                mock_client.auth = mock_auth
                s = Settings(_env_file=None, app_env="test", supabase_url="https://x.sb.co", supabase_service_role_key="eyJ_X")
                yield SupabaseAdminClient(client=mock_client, settings=s)

            base_s = Settings(_env_file=None, app_env="test", supabase_url="https://x.sb.co", supabase_service_role_key="eyJ_X")
            app = create_app(base_s)
            app.dependency_overrides[get_supabase_admin] = _sb_factory
            app.dependency_overrides[get_settings] = lambda: base_s

            async def _jwks_none(_k, _s):
                return None

            mp.setattr(a_mod, "_fetch_jwk_for_header", _jwks_none)
            mp.setattr(a_mod, "_public_key_from_jwk", lambda _j: object())
            tc = TestClient(app)
            resp = tc.post(
                "/api/v1/auth/verify",
                json={"email": "a@b.com", "token": "BAD", "type": "magiclink"},
            )
        finally:
            mp.undo()

        assert resp.status_code == 401
        body = resp.json()
        assert "error" in body
        assert "OTP_EXPIRED" in body["error"]["message"]
        assert "链接已过期" in body["error"]["message"]


# ============================================================
#  /me（依赖 get_current_user Depends）
# ============================================================
class TestGetMeAndCurrentUser:
    def test_ok_returns_auth_user(self, monkeypatch) -> None:
        """注入 mock decode 返回有效 claims → 200 返回 user_id/email/role。"""
        from app.api.v1 import auth as a_mod
        from app.core.config import Settings
        from app.main import create_app

        mp = pytest.MonkeyPatch()
        try:
            async def _jwks_hit_ok(_k, _s):
                return {"kid": "testkid", "kty": "RSA"}

            mp.setattr(a_mod, "_fetch_jwk_for_header", _jwks_hit_ok)
            mp.setattr(a_mod, "_public_key_from_jwk", lambda _j: object())

            def _fake_decode(token, *args, **kwargs):
                # 解析第二段 base64 拿到 sub/email
                import base64
                import json

                payload = token.split(".")[1]
                payload += "=" * (-len(payload) % 4)
                return json.loads(base64.urlsafe_b64decode(payload))

            mp.setattr(a_mod.pyjwt, "decode", _fake_decode)  # type: ignore[attr-defined]

            base_s = Settings(
                _env_file=None,
                app_env="test",
                supabase_url="https://example.supabase.co",
                supabase_jwt_audience="authenticated",
            )
            app = create_app(base_s)
            tc = TestClient(app)

            token = _make_test_jwt(
                sub="123e4567-e89b-12d3-a456-426614174aaa",
                email="me@example.com",
                role="authenticated",
                aud="authenticated",
                iss="https://example.supabase.co/auth/v1",
            )
            resp = tc.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
        finally:
            mp.undo()

        assert resp.status_code == 200
        body = resp.json()
        assert body["user_id"] == "123e4567-e89b-12d3-a456-426614174aaa"
        assert body["email"] == "me@example.com"
        assert body["role"] == "authenticated"
        # created_at 默认 None
        assert body.get("created_at") is None

    def test_missing_auth_header_returns_401(self, make_auth_client) -> None:
        tc = make_auth_client()
        resp = tc.get("/api/v1/auth/me")
        assert resp.status_code == 401
        assert resp.json()["error"]["message"] == "缺少 Bearer Token"

    def test_bearer_prefix_only_no_token_returns_401(self, make_auth_client) -> None:
        tc = make_auth_client()
        resp = tc.get("/api/v1/auth/me", headers={"Authorization": "Bearer   "})
        assert resp.status_code == 401
        assert resp.json()["error"]["message"] == "Token 为空"

    def test_token_malformed_no_kid_returns_401(self, monkeypatch) -> None:
        from app.core.config import Settings
        from app.main import create_app

        mp = pytest.MonkeyPatch()
        try:
            # JWT header 缺 kid
            import base64
            import json
            header = {"alg": "RS256", "typ": "JWT"}  # no kid
            payload = {"sub": "x", "email": "x@y", "aud": "a"}
            def _b64url(d): return base64.urlsafe_b64encode(json.dumps(d, separators=(",", ":")).encode()).decode().rstrip("=")
            bad_token = f"{_b64url(header)}.{_b64url(payload)}.SIG"

            base_s = Settings(_env_file=None, app_env="test", supabase_url="https://e.co")
            app = create_app(base_s)
            tc = TestClient(app)
            resp = tc.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {bad_token}"})
        finally:
            mp.undo()

        assert resp.status_code == 401
        assert resp.json()["error"]["message"] == "JWT 缺少 kid 头"

    def test_jwks_fetch_kid_not_found_returns_503(self, monkeypatch) -> None:
        """_fetch_jwk_for_header 返回 None → 503（依赖外部服务不可用）。"""
        from app.api.v1 import auth as a_mod
        from app.core.config import Settings
        from app.main import create_app

        mp = pytest.MonkeyPatch()
        try:
            async def _jwks_miss(_k, _s):
                return None

            mp.setattr(a_mod, "_fetch_jwk_for_header", _jwks_miss)
            base_s = Settings(_env_file=None, app_env="test", supabase_url="https://e.co")
            app = create_app(base_s)
            tc = TestClient(app)
            token = _make_test_jwt()
            resp = tc.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
        finally:
            mp.undo()

        assert resp.status_code == 503
        assert "JWKS" in resp.json()["error"]["message"]

    def test_jwt_expired_returns_401(self, monkeypatch) -> None:
        from app.api.v1 import auth as a_mod
        from app.core.config import Settings
        from app.main import create_app

        mp = pytest.MonkeyPatch()
        try:
            async def _jwks_hit5(_k, _s):
                return {"kid": "testkid"}

            mp.setattr(a_mod, "_fetch_jwk_for_header", _jwks_hit5)
            mp.setattr(a_mod, "_public_key_from_jwk", lambda _j: object())

            def _fake_decode(*args, **kwargs):
                raise a_mod.pyjwt.ExpiredSignatureError("exp")  # type: ignore[attr-defined]

            mp.setattr(a_mod.pyjwt, "decode", _fake_decode)  # type: ignore[attr-defined]

            base_s = Settings(_env_file=None, app_env="test", supabase_url="https://e.co")
            app = create_app(base_s)
            tc = TestClient(app)
            token = _make_test_jwt(exp_offset_seconds=-10)
            resp = tc.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
        finally:
            mp.undo()

        assert resp.status_code == 401
        assert "过期" in resp.json()["error"]["message"]

    def test_jwt_wrong_audience_returns_401(self, monkeypatch) -> None:
        from app.api.v1 import auth as a_mod
        from app.core.config import Settings
        from app.main import create_app

        mp = pytest.MonkeyPatch()
        try:
            async def _jwks_hit6(_k, _s):
                return {"kid": "testkid"}

            mp.setattr(a_mod, "_fetch_jwk_for_header", _jwks_hit6)
            mp.setattr(a_mod, "_public_key_from_jwk", lambda _j: object())

            def _fake_decode(*args, **kwargs):
                raise a_mod.pyjwt.InvalidAudienceError("aud")  # type: ignore[attr-defined]

            mp.setattr(a_mod.pyjwt, "decode", _fake_decode)  # type: ignore[attr-defined]

            base_s = Settings(_env_file=None, app_env="test", supabase_url="https://e.co", supabase_jwt_audience="authenticated")
            app = create_app(base_s)
            tc = TestClient(app)
            token = _make_test_jwt(aud="WRONG_AUD")
            resp = tc.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
        finally:
            mp.undo()

        assert resp.status_code == 401
        assert "aud 不匹配" in resp.json()["error"]["message"]

    def test_jwt_no_sub_returns_401(self, monkeypatch) -> None:
        """decode 成功但缺少 sub → 401。"""
        from app.api.v1 import auth as a_mod
        from app.core.config import Settings
        from app.main import create_app

        mp = pytest.MonkeyPatch()
        try:
            async def _jwks_hit_nosub(_k, _s):
                return {"kid": "testkid"}

            mp.setattr(a_mod, "_fetch_jwk_for_header", _jwks_hit_nosub)
            mp.setattr(a_mod, "_public_key_from_jwk", lambda _j: object())

            def _fake_decode(token, *args, **kwargs):
                import base64
                import json

                payload = token.split(".")[1]
                payload += "=" * (-len(payload) % 4)
                claims = json.loads(base64.urlsafe_b64decode(payload))
                claims.pop("sub", None)  # 故意删 sub
                return claims

            mp.setattr(a_mod.pyjwt, "decode", _fake_decode)  # type: ignore[attr-defined]

            base_s = Settings(_env_file=None, app_env="test", supabase_url="https://e.co")
            app = create_app(base_s)
            tc = TestClient(app)
            token = _make_test_jwt()
            resp = tc.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
        finally:
            mp.undo()

        assert resp.status_code == 401
        assert "缺少 sub/email" in resp.json()["error"]["message"]


# ============================================================
#  /logout
# ============================================================
class TestLogout:
    def test_logout_returns_200_success(self, monkeypatch) -> None:
        from collections.abc import AsyncIterator
        from unittest.mock import Mock

        from app.api.v1 import auth as a_mod
        from app.api.v1.auth import CurrentUser
        from app.core.config import get_settings
        from app.core.supabase_client import get_supabase_admin

        mp = pytest.MonkeyPatch()
        try:
            # 1) mock get_current_user：直接注入一个 CurrentUser（避免 JWT 校验的繁琐 mock）
            # 2) mock sign_out：记录调用
            captured_sign_out_called = False

            async def _sb_factory() -> AsyncIterator[SupabaseAdminClient]:
                nonlocal captured_sign_out_called
                mock_auth = Mock()

                def _sign_out() -> None:
                    nonlocal captured_sign_out_called
                    captured_sign_out_called = True

                mock_auth.sign_out = _sign_out
                mock_client = Mock()
                mock_client.auth = mock_auth
                s = Settings(_env_file=None, app_env="test", supabase_url="https://e.co", supabase_service_role_key="eyJ_X")
                yield SupabaseAdminClient(client=mock_client, settings=s)

            # override get_current_user Depends
            from app.api.v1.auth import get_current_user

            async def _fake_current_user() -> CurrentUser:
                return CurrentUser(
                    user_id="usr-1",
                    email="a@b.com",
                    role="authenticated",
                    claims={"sub": "usr-1", "email": "a@b.com", "role": "authenticated"},
                )

            base_s = Settings(_env_file=None, app_env="test", supabase_url="https://e.co")
            app = create_app(base_s)
            app.dependency_overrides[get_supabase_admin] = _sb_factory
            app.dependency_overrides[get_settings] = lambda: base_s
            app.dependency_overrides[get_current_user] = _fake_current_user
            mp.setattr(a_mod, "_fetch_jwk_for_header", lambda _k, _s: None)
            mp.setattr(a_mod, "_public_key_from_jwk", lambda _j: object())
            tc = TestClient(app)
            resp = tc.post("/api/v1/auth/logout", headers={"Authorization": "Bearer FAKE"})
        finally:
            mp.undo()

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        # revoked=True 表示我们确实尝试了 sign_out
        assert body["revoked"] is True
        assert captured_sign_out_called is True
