"""P7-06 / P7-02 专项测试

覆盖：
- snapshot_version 字段（P7-06）读写贯通
- before= cursor 编码/解码（Base64URL ISO8601|UUID 格式）
- before= 多页分页正确性（created_at DESC + id DESC）、next_cursor 尾页判定
- 同 created_at 时间戳 tie-break 不漏不重
- offset 老模式仍然兼容
"""
from __future__ import annotations

import copy
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock
from typing import Any, Literal

import pytest
from fastapi.testclient import TestClient
from postgrest.types import CountMethod

from app.core.config import Settings
from app.core.supabase_client import SupabaseAdminClient
from app.main import create_app

TABLE_PREFS = "user_preference_snapshots"


def _now_iso() -> str:
    return datetime.now(tz=UTC).isoformat()


# -----------------------------
# 复用的 Mock Supabase（仅扩展 lt/le/gt/gte 支持 cursor 两段式查询）
# -----------------------------
class _MockTable:
    def __init__(
        self,
        storage: dict[str, list[dict[str, Any]]],
        table: str,
        auth_state: "_AuthState",
    ) -> None:
        self._storage = storage
        self._table = table
        self._auth = auth_state
        self._select_cols: list[str] | None = None
        self._filters: list[tuple[str, str, Any]] = []
        self._orders: list[tuple[str, bool]] = []
        self._limit: int | None = None
        self._offset: int | None = None
        self._delete = False
        self._count: CountMethod | None = None
        self._insert_rows: list[dict[str, Any]] | None = None

    def select(self, cols: str = "*", count: Any = None) -> "_MockTable":
        self._select_cols = [c.strip() for c in cols.split(",")] if cols != "*" else None
        if count is not None:
            self._count = count
        return self

    def eq(self, col: str, val: Any) -> "_MockTable":
        self._filters.append((col, "eq", val))
        return self

    def neq(self, col: str, val: Any) -> "_MockTable":
        self._filters.append((col, "neq", val))
        return self

    def lt(self, col: str, val: Any) -> "_MockTable":
        self._filters.append((col, "lt", val))
        return self

    def le(self, col: str, val: Any) -> "_MockTable":
        self._filters.append((col, "le", val))
        return self

    def gt(self, col: str, val: Any) -> "_MockTable":
        self._filters.append((col, "gt", val))
        return self

    def gte(self, col: str, val: Any) -> "_MockTable":
        self._filters.append((col, "gte", val))
        return self

    def order(self, col: str, *, desc: bool = False) -> "_MockTable":
        self._orders.append((col, desc))
        return self

    def limit(self, n: int) -> "_MockTable":
        self._limit = n
        return self

    def range(self, offset: int, end: int) -> "_MockTable":
        self._offset = offset
        self._limit = end - offset + 1
        return self

    def delete(self, count: CountMethod | None = None) -> "_MockTable":
        self._delete = True
        self._count = count
        return self

    def insert(self, rows: Any) -> "_MockTable":
        self._insert_rows = copy.deepcopy(rows if isinstance(rows, list) else [rows])
        return self

    def _cmp(self, rv: Any, op: str, val: Any) -> bool:
        # None 永远 lt 任何具体值；str 按字典序、数字按数值
        if rv is None and val is None:
            return op in ("eq", "le", "gte")
        if rv is None:
            return op in ("lt", "le", "neq")
        if val is None:
            return op in ("gt", "gte", "neq")
        try:
            if op == "lt":
                return rv < val
            if op == "le":
                return rv <= val
            if op == "gt":
                return rv > val
            if op == "gte":
                return rv >= val
        except TypeError:
            # 跨类型（None/字符串）不比较，按 False 安全处理
            return False
        raise AssertionError(f"unreachable op {op}")

    def _matches(self, row: dict[str, Any]) -> bool:
        for col, op, val in self._filters:
            rv = row.get(col)
            if op == "eq":
                if rv != val:
                    return False
            elif op == "neq":
                if rv == val:
                    return False
            elif op in ("lt", "le", "gt", "gte"):
                if not self._cmp(rv, op, val):
                    return False
            else:
                raise AssertionError(f"unsupported op {op} in test mock")
        return True

    def _project(self, row: dict[str, Any]) -> dict[str, Any]:
        if self._select_cols is None:
            return copy.deepcopy(row)
        return {k: row[k] for k in self._select_cols if k in row}

    def _sorted(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        for col, desc in reversed(self._orders):
            rows.sort(
                key=lambda r: (r.get(col) is None, r.get(col)),
                reverse=desc,
            )
        return rows

    def execute(self) -> Any:
        data: list[dict[str, Any]] = list(self._storage.setdefault(self._table, []))
        if self._insert_rows is not None:
            written: list[dict[str, Any]] = []
            for r in self._insert_rows:
                user_id = r.get("user_id")
                if user_id and str(user_id) not in self._auth.active_user_ids:
                    raise Exception("PGRST204: user not found in auth.users")
                new_row = dict(r)
                if "id" not in new_row:
                    new_row["id"] = str(uuid.uuid4())
                now = _now_iso()
                new_row.setdefault("created_at", now)
                new_row.setdefault("updated_at", now)
                data.append(new_row)
                written.append(self._project(new_row))
            self._storage[self._table] = data
            return Mock(data=written, count=len(written))

        filtered = [r for r in data if self._matches(r)]
        if self._delete:
            keep = [r for r in data if not self._matches(r)]
            removed = len(data) - len(keep)
            self._storage[self._table] = keep
            return Mock(data=[], count=removed)

        sorted_rows = self._sorted(filtered)
        if self._offset:
            sorted_rows = sorted_rows[self._offset :]
        if self._limit is not None:
            sorted_rows = sorted_rows[: self._limit]
        projected = [self._project(r) for r in sorted_rows]
        return Mock(data=projected, count=len(filtered))


class _AuthState:
    def __init__(self) -> None:
        self.active_user_ids: set[str] = set()
        self.users_by_id: dict[str, dict[str, Any]] = {}

    def add(self, user_id: str, email: str = "e2e@example.com") -> dict[str, Any]:
        user = {"id": user_id, "email": email, "created_at": _now_iso()}
        self.active_user_ids.add(user_id)
        self.users_by_id[user_id] = user
        return user


class _MockPostgrestClient:
    def __init__(self, storage: dict[str, list[dict[str, Any]]], auth_state: "_AuthState") -> None:
        self._storage = storage
        self._auth = auth_state

        # 挂载 mock 的 auth.admin（SupabaseAdminClient.auth_admin → self.client.auth.admin）
        self._mock_auth_admin = _MockAuthAdmin(auth_state)

    @property
    def auth(self) -> _MockAuth:
        return _MockAuth(self._mock_auth_admin)

    def table(self, name: str) -> _MockTable:
        return _MockTable(self._storage, name, self._auth)


class _MockAuth:
    def __init__(self, admin: Any) -> None:
        self.admin = admin


class _MockAuthAdmin:
    def __init__(self, auth_state: "_AuthState") -> None:
        self._auth = auth_state

    def get_user_by_id(self, user_id: str) -> Any:
        """GDPR 死账号检查：auth.exists=true 通过，false 抛错模拟 PostgREST404。"""
        user_id = str(user_id)
        if not self._auth.exists(user_id):
            raise Exception("PGRST204: user not found in auth.users")
        return Mock(user=self._auth.users_by_id.get(user_id, {"id": user_id}))


class _MockSupabaseAdminClient(SupabaseAdminClient):  # type: ignore[misc]
    def __init__(
        self,
        storage: dict[str, list[dict[str, Any]]],
        auth: _AuthState,
        settings: Settings,
    ) -> None:
        self._storage = storage
        self._auth = auth
        self.settings = settings
        self.client: _MockPostgrestClient = _MockPostgrestClient(storage, auth)  # type: ignore[assignment]
        # Mock auth.admin，模拟 Supabase admin 查询；不做 GDPR 死账号的真删逻辑（测试侧通过 AuthState 管理）
        self._auth_admin_mock: Any = Mock()

        # 默认返回 id 存在的用户
        def _get_user(user_id: str) -> Any:
            if str(user_id) in auth.active_user_ids:
                return Mock(user={"id": str(user_id)})
            raise Exception("PGRST204: user not found in auth.users")

        self._auth_admin_mock.get_user_by_id = _get_user

    @property
    def auth_admin(self) -> Any:  # type: ignore[override]
        return self._auth_admin_mock


def _make_app(monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, _AuthState, dict[str, list[dict[str, Any]]]]:
    import base64
    import json

    from app.core.config import get_settings
    from app.core.supabase_client import get_supabase_admin

    settings = Settings(
        _env_file=None,
        app_env="test",
        app_mode="mock",
        supabase_url="https://example.supabase.co",
        supabase_service_role_key="eyJ_fake_service_role",
        supabase_anon_key="eyJ_fake_anon",
    )
    storage: dict[str, list[dict[str, Any]]] = {}
    auth = _AuthState()
    sb = _MockSupabaseAdminClient(storage, auth, settings)

    async def _override_sb_async() -> AsyncIterator[SupabaseAdminClient]:
        yield sb  # type: ignore[misc]

    async def _async_jwk(_kid: str, _s: Settings) -> dict[str, str]:
        return {"kty": "RSA", "kid": "testkid", "n": "tZ8VKQ", "e": "AQAB", "use": "sig", "alg": "RS256"}

    def _pub_from_jwk(_jwk: dict[str, Any]) -> object:
        return object()

    # 跳过真实 RSA 验签：直接从 JWT payload 段 decode 返回 claims
    def _fake_pyjwt_decode(token: str, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        _, payload_b64, _ = token.split(".")
        payload_b64 += "=" * (-len(payload_b64) % 4)
        return json.loads(base64.urlsafe_b64decode(payload_b64))

    monkeypatch.setattr("app.api.v1.auth._fetch_jwk_for_header", _async_jwk)
    monkeypatch.setattr("app.api.v1.auth._public_key_from_jwk", _pub_from_jwk)

    # 懒加载：import auth 模块后再 patch 它局部引用的 pyjwt
    from app.api.v1 import auth as auth_mod

    monkeypatch.setattr(auth_mod.pyjwt, "decode", _fake_pyjwt_decode)  # type: ignore[attr-defined]

    app = create_app(settings)
    app.dependency_overrides[get_supabase_admin] = _override_sb_async
    app.dependency_overrides[get_settings] = lambda: settings
    return TestClient(app), auth, storage


def _make_jwt(user_id: str) -> str:
    """伪造 Supabase RS256 JWT（conftest 已有同名实现，这里用最小本地版避免 import cycle）。"""
    import base64
    import hashlib
    import hmac

    header = {"alg": "RS256", "typ": "JWT", "kid": "testkid"}
    payload = {
        "sub": user_id,
        "aud": "authenticated",
        "exp": int(datetime.now(tz=UTC).timestamp()) + 3600,
        "iat": int(datetime.now(tz=UTC).timestamp()),
        "email": "e2e@example.com",
        "role": "authenticated",
    }

    def b64url(d: str) -> str:
        return base64.urlsafe_b64encode(d.encode("utf-8")).decode("ascii").rstrip("=")

    import json

    to_sign = f"{b64url(json.dumps(header, separators=(',',':')))}.{b64url(json.dumps(payload, separators=(',',':')))}"
    # 用一个假的 SHA-256 签名（monkeypatch 已经绕过验签，只要结构合法）
    sig = hmac.new(b"test", to_sign.encode("utf-8"), hashlib.sha256).digest()
    sig_str = base64.urlsafe_b64encode(sig).decode("ascii").rstrip("=")
    return f"{to_sign}.{sig_str}"


# ============================================================
# 1. encode / decode cursor 往返
# ============================================================
class TestCursorCodec:
    def test_encode_decode_round_trip(self) -> None:
        from app.api.v1.preferences import _decode_cursor, _encode_cursor

        dt = datetime(2026, 8, 13, 12, 30, 45, 123456, tzinfo=UTC)
        uid = uuid.UUID("3fa85f64-5717-4562-b3fc-2c963f66afa6")
        cur = _encode_cursor(dt, uid)
        # 不含等号结尾
        assert not cur.endswith("=")
        assert len(cur) > 0
        # 往返一致
        got_dt, got_uid = _decode_cursor(cur)
        assert got_dt == dt
        assert got_uid == uid

    def test_decode_invalid_raises_400(self) -> None:
        from fastapi import HTTPException

        from app.api.v1.preferences import _decode_cursor

        bad_cases: list[tuple[str, str]] = [
            ("", "空字符串"),
            ("!!!not_base64!!!", "纯垃圾字符"),
            ("YWJjZA", "只有 payload，没有竖线分隔"),
            ("MjAyNi0wOC0xM1QxMjozMDo0NVo=|xxx", "明文拼接，非 base64"),
        ]
        for case, _label in bad_cases:
            with pytest.raises(HTTPException) as exc_info:
                _decode_cursor(case)
            assert exc_info.value.status_code == 400, f"{_label} 应返回 400"


# ============================================================
# 2. 快照版本号 P7-06
# ============================================================
class TestSnapshotVersion:
    def test_snapshot_version_defaults_and_persisted(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client, auth, storage = _make_app(monkeypatch)
        user_id = "11111111-1111-1111-1111-111111111111"
        auth.add(user_id)
        headers = {"Authorization": f"Bearer {_make_jwt(user_id)}"}

        # (a) 写一条没显式传 snapshot_version → 默认 v1.0
        resp1 = client.post(
            "/api/v1/preferences",
            json={
                "questionnaire_version": "v1.0",
                "dictionary_version": "v1.0",
                "snapshot": {"meal_period": "lunch"},
            },
            headers=headers,
        )
        assert resp1.status_code == 201, resp1.text

        # (b) 写一条显式 snapshot_version = "v1.1"（假设未来版本）
        resp2 = client.post(
            "/api/v1/preferences",
            json={
                "questionnaire_version": "v1.0",
                "dictionary_version": "v1.1",
                "snapshot_version": "v1.1",
                "snapshot": {"meal_period": "dinner"},
            },
            headers=headers,
        )
        assert resp2.status_code == 201, resp2.text

        # (c) list 读回来：第一条 v1.1（新），第二条 v1.0（旧）
        list_resp = client.get("/api/v1/preferences?limit=10", headers=headers)
        assert list_resp.status_code == 200, list_resp.text
        body = list_resp.json()
        items = body["items"]
        assert len(items) == 2
        # created_at desc → 后写的 v1.1 排首
        assert items[0]["snapshot_version"] == "v1.1"
        assert items[1]["snapshot_version"] == "v1.0"
        # 老数据兼容测试（直接写 storage，不带 snapshot_version 字段）→ _row_to_response 回退 v1.0
        storage.setdefault(TABLE_PREFS, []).append({
            "id": str(uuid.uuid4()),
            "user_id": user_id,
            "questionnaire_version": "v1.0",
            "dictionary_version": "v1.0",
            # snapshot_version 省略！
            "source_session_id": "manual_no_version",
            "source_history_id": None,
            "snapshot_jsonb": {"meal_period": "breakfast"},
            "created_at": _now_iso(),
            "updated_at": _now_iso(),
        })
        list_resp2 = client.get("/api/v1/preferences?limit=100", headers=headers)
        assert list_resp2.status_code == 200, list_resp2.text
        body2 = list_resp2.json()
        versions = [it["snapshot_version"] for it in body2["items"]]
        assert all(v and v.startswith("v1") for v in versions), f"versions={versions!r} 应全部填充 v1.x"
        # list 模式下 offset 字段是 int（offset 模式回传 offset）
        assert isinstance(body2["offset"], int)
        # P7-02：offset 模式也可返回 next_cursor；若 total 刚好取完，则是 None
        if len(body2["items"]) >= body2["total"]:
            assert body2["next_cursor"] is None


# ============================================================
# 3. cursor 分页：多页串联正确 + next_cursor 边界
# ============================================================
class TestCursorPagination:
    def test_before_pagination_through_12_rows(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client, auth, storage = _make_app(monkeypatch)
        user_id = "22222222-2222-2222-2222-222222222222"
        auth.add(user_id)

        # 手动灌 12 条快照，时间跨度 12h（13:00 → 00:00），每页 3 条 → 4 页
        base = datetime(2026, 8, 13, 13, 0, 0, tzinfo=UTC)
        expected_ids: list[str] = []
        rows: list[dict[str, Any]] = []
        for i in range(12):
            dt = base - timedelta(hours=i)
            sid = str(uuid.uuid4())
            expected_ids.append(sid)
            rows.append({
                "id": sid,
                "user_id": user_id,
                "questionnaire_version": "v1.0",
                "dictionary_version": "v1.0",
                "snapshot_version": "v1.0",
                "source_session_id": f"seed_{i}",
                "source_history_id": None,
                "snapshot_jsonb": {"meal_period": "dinner", "seq": i},
                "created_at": dt.isoformat(),
                "updated_at": dt.isoformat(),
            })
        storage[TABLE_PREFS] = rows

        headers = {"Authorization": f"Bearer {_make_jwt(user_id)}"}

        all_items: list[dict[str, Any]] = []
        next_cur: str | None = None
        pages = 0
        while True:
            pages += 1
            assert pages <= 10, "分页死循环"
            params: dict[str, Any] = {"limit": 3}
            if next_cur is not None:
                params["before"] = next_cur
            resp = client.get("/api/v1/preferences", params=params, headers=headers)
            assert resp.status_code == 200, resp.text
            body = resp.json()
            page_items = body["items"]
            assert 1 <= len(page_items) <= 3, f"每页 1~3 条，实际 {len(page_items)}"
            all_items.extend(page_items)
            # cursor 模式下 offset=null, page_cursor 回显
            assert body["offset"] is None if next_cur is not None else isinstance(body["offset"], int)
            if next_cur is not None:
                assert body["page_cursor"] == next_cur
            next_cur = body["next_cursor"]
            if next_cur is None:
                break

        # 4 页，12 条，无重复（id dedupe）
        assert pages == 4, f"实际 {pages} 页"
        assert len(all_items) == 12, f"实际 {len(all_items)} 条"
        got_ids = [it["id"] for it in all_items]
        assert len(set(got_ids)) == 12, "ID 重复：分页逻辑漏/重"
        # expected_ids 灌的顺序是 13:00 → 00:00，分页按 created_at DESC 应与该顺序一致
        assert got_ids == expected_ids

    def test_same_created_at_tiebreak(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """同 created_at 5 条，ID 从小到大灌，每页 2 条：分页结果应仍严格按 (created_at DESC, id DESC)。"""
        client, auth, storage = _make_app(monkeypatch)
        user_id = "33333333-3333-3333-3333-333333333333"
        auth.add(user_id)
        headers = {"Authorization": f"Bearer {_make_jwt(user_id)}"}

        same_time = datetime(2026, 8, 13, 9, 0, 0, tzinfo=UTC).isoformat()
        # 5 条 id 不递增：用 5 个 UUID 字符串，排序后比较 tie-break 是否正确
        ids_raw = [
            "f0000000-0000-0000-0000-00000000000f",
            "a0000000-0000-0000-0000-00000000000a",
            "e0000000-0000-0000-0000-00000000000e",
            "b0000000-0000-0000-0000-00000000000b",
            "d0000000-0000-0000-0000-00000000000d",
        ]
        rows = [
            {
                "id": s,
                "user_id": user_id,
                "questionnaire_version": "v1.0",
                "dictionary_version": "v1.0",
                "snapshot_version": "v1.0",
                "source_session_id": f"same_ts_{idx}",
                "source_history_id": None,
                "snapshot_jsonb": {"idx": idx},
                "created_at": same_time,
                "updated_at": same_time,
            }
            for idx, s in enumerate(ids_raw)
        ]
        storage[TABLE_PREFS] = rows

        # 期望：同 created_at DESC 都是相等 → 退而按 id DESC
        expected_id_order = sorted(ids_raw, reverse=True)

        all_items: list[dict[str, Any]] = []
        next_cur: str | None = None
        pages = 0
        while True:
            pages += 1
            assert pages <= 10
            params: dict[str, Any] = {"limit": 2}
            if next_cur is not None:
                params["before"] = next_cur
            resp = client.get("/api/v1/preferences", params=params, headers=headers)
            assert resp.status_code == 200, resp.text
            body = resp.json()
            all_items.extend(body["items"])
            next_cur = body["next_cursor"]
            if next_cur is None:
                break
        got_ids = [it["id"] for it in all_items]
        assert len(got_ids) == 5
        assert got_ids == expected_id_order, (
            f"同 created_at tie-break：期望 id DESC {expected_id_order}，实际 {got_ids}"
        )

    def test_before_invalid_cursor_400(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client, auth, _storage = _make_app(monkeypatch)
        user_id = "44444444-4444-4444-4444-444444444444"
        auth.add(user_id)
        headers = {"Authorization": f"Bearer {_make_jwt(user_id)}"}
        resp = client.get(
            "/api/v1/preferences",
            params={"limit": 2, "before": "not-a-valid-base64-cursor!!!"},
            headers=headers,
        )
        assert resp.status_code == 400, resp.text
        body = resp.json()
        # app.middleware 包成 {error: {code, message}}
        err_msg = body.get("error", {}).get("message", "") or body.get("detail", "")
        assert "cursor" in err_msg.lower(), f"err_msg={err_msg!r} 应包含 cursor"

    def test_offset_mode_still_works_backwards_compat(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """不传 before 时应回走 offset 老路径，offset 字段为整数。"""
        client, auth, storage = _make_app(monkeypatch)
        user_id = "55555555-5555-5555-5555-555555555555"
        auth.add(user_id)
        base = datetime(2026, 8, 13, 10, 0, 0, tzinfo=UTC)
        storage[TABLE_PREFS] = [
            {
                "id": str(uuid.uuid4()),
                "user_id": user_id,
                "questionnaire_version": "v1.0",
                "dictionary_version": "v1.0",
                "snapshot_version": "v1.0",
                "source_session_id": f"off{i}",
                "source_history_id": None,
                "snapshot_jsonb": {},
                "created_at": (base - timedelta(minutes=i)).isoformat(),
                "updated_at": (base - timedelta(minutes=i)).isoformat(),
            }
            for i in range(6)
        ]
        headers = {"Authorization": f"Bearer {_make_jwt(user_id)}"}
        resp = client.get(
            "/api/v1/preferences",
            params={"limit": 2, "offset": 2},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total"] == 6
        assert body["offset"] == 2  # 整数（非 cursor 模式）
        assert len(body["items"]) == 2
        # P7-02：offset 模式下如果还有更多，也返回 next_cursor（此处 total 6 > offset+len=4，应更多）
        assert isinstance(body["next_cursor"], str) and body["next_cursor"], (
            "有更多数据时 offset 模式也应产出 next_cursor 供前端无缝切 cursor"
        )
        # page_cursor 在非 cursor 模式下为 None
        assert body["page_cursor"] is None
