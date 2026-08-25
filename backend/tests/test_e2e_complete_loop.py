"""P7-01 端到端完整闭环集成验收测试。

验收剧本：
  1. 登录用户 A，_make_e2e_session 调 session/start → 生成第 1 道追问题
  2. 依次回答第 1/2/3 道追问 → 自动 finalize → 返回正好 5 条候选
  3. 检查 user_recommendation_history 写入 ≥ 1 条
  4. 检查 user_preference_snapshots 写入 ≥ 1 条
  5. 再跑一次推荐（形成第 2 条快照，用于 P6-02/P6-04 冷启动验证）
  6. 调 DELETE /api/v1/auth/me（GDPR 删除账号）
  7. 最终两表 COUNT(*) = 0，数据完全擦除
"""
from __future__ import annotations

import base64
import copy
import json
import logging
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from postgrest.types import CountMethod

from app.core.config import Settings
from app.core.supabase_client import SupabaseAdminClient
from app.main import create_app

# ============================================================
# Mock Supabase：内存实现 user_recommendations + user_preference_snapshots
# 并实现 auth_admin（对 get_user_by_id / delete_user 的存在性管理）
# ============================================================

TABLE_HISTORY = "user_recommendations"
TABLE_PREFS = "user_preference_snapshots"


def _now_iso() -> str:
    return datetime.now(tz=UTC).isoformat()


class _MockTable:
    """模拟 `sb.table("xxx")` 链式：select/insert/delete/eq/order/limit/count/execute。"""

    def __init__(self, storage: dict[str, list[dict[str, Any]]], table: str, auth_state: _AuthState) -> None:
        self._storage = storage
        self._table = table
        self._auth = auth_state
        self._select_cols: list[str] | None = None
        self._filters: list[tuple[str, str, Any]] = []  # (col, op, value)
        self._orders: list[tuple[str, bool]] = []  # (col, desc)
        self._limit: int | None = None
        self._offset: int | None = None
        self._delete = False
        self._count: CountMethod | None = None
        self._insert_rows: list[dict[str, Any]] | None = None

    # ---------- chainable filters ----------
    def select(self, cols: str = "*") -> _MockTable:
        self._select_cols = [c.strip() for c in cols.split(",")] if cols != "*" else None
        return self

    def eq(self, col: str, val: Any) -> _MockTable:
        self._filters.append((col, "eq", val))
        return self

    def neq(self, col: str, val: Any) -> _MockTable:
        self._filters.append((col, "neq", val))
        return self

    def order(self, col: str, *, desc: bool = False) -> _MockTable:
        self._orders.append((col, desc))
        return self

    def limit(self, n: int) -> _MockTable:
        self._limit = n
        return self

    def range(self, offset: int, end: int) -> _MockTable:
        self._offset = offset
        self._limit = end - offset + 1
        return self

    def delete(self, count: CountMethod | None = None) -> _MockTable:
        self._delete = True
        self._count = count
        return self

    def insert(self, rows: Any) -> _MockTable:
        self._insert_rows = copy.deepcopy(rows if isinstance(rows, list) else [rows])
        return self

    # ---------- matcher ----------
    def _matches(self, row: dict[str, Any]) -> bool:
        for col, op, val in self._filters:
            rv = row.get(col)
            if op == "eq":
                if rv != val:
                    return False
            elif op == "neq":
                if rv == val:
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

    # ---------- terminal ----------
    def execute(self) -> Any:
        data: list[dict[str, Any]] = list(self._storage.setdefault(self._table, []))

        # GDPR 死账号过滤：表中 user_id 若已被删（逻辑删），查询时对其不可见；
        # 本测试账号不会被中途删，只需简单支持。
        # data = [r for r in data if str(r.get("user_id")) not in self._auth.deleted_user_ids]

        if self._insert_rows is not None:
            written: list[dict[str, Any]] = []
            for r in self._insert_rows:
                # 写前校验：auth.users 必须存活（GDPR 死 token 防护）
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

    def remove(self, user_id: str) -> None:
        self.active_user_ids.discard(user_id)
        self.users_by_id.pop(user_id, None)

    def exists(self, user_id: str) -> bool:
        return user_id in self.active_user_ids


class _MockPostgrestClient:
    def __init__(self, storage: dict[str, list[dict[str, Any]]], auth_state: _AuthState) -> None:
        self._storage = storage
        self._auth = auth_state

    def table(self, name: str) -> _MockTable:
        return _MockTable(self._storage, name, self._auth)


class _MockAuthAdmin:
    def __init__(self, auth_state: _AuthState) -> None:
        self._auth = auth_state

    def get_user(self, user_id: str) -> Any:
        """Supabase 真实 API：admin.get_user(id)。与 get_user_by_id 语义相同。"""
        if not self._auth.exists(user_id):
            raise Exception("auth user not found")
        return Mock(user=self._auth.users_by_id[user_id])

    def get_user_by_id(self, user_id: str) -> Any:
        if not self._auth.exists(user_id):
            raise Exception("auth user not found")
        return Mock(user=self._auth.users_by_id[user_id])

    def delete_user(self, user_id: str) -> Any:
        if not self._auth.exists(user_id):
            raise Exception("auth user not found")
        self._auth.remove(user_id)
        return Mock()


class _MockSupabaseAdminClient(SupabaseAdminClient):
    def __init__(self, storage: dict[str, list[dict[str, Any]]], auth_state: _AuthState, settings: Settings) -> None:
        self._storage = storage
        self._auth = auth_state
        self._pgclient = _MockPostgrestClient(storage, auth_state)
        self._auth_admin = _MockAuthAdmin(auth_state)
        self._settings = settings
        # 父类属性：client / settings；auth_admin 是只读 property，指向 self.client.auth.admin——
        # 所以我们造一个带 auth.admin 属性的 fake client
        class _FakeSupabaseClient:
            def __init__(self, client_like: Any, auth_admin: Any) -> None:
                object.__setattr__(self, "_client", client_like)
                object.__setattr__(self, "_auth_admin", auth_admin)

            def __getattr__(self, name: str) -> Any:
                # 只有 .table(...) 调用走 client 属性：
                return getattr(self._client, name)

            @property
            def auth(self) -> Any:
                class _:
                    admin = self._auth_admin
                return _()

        self.client = _FakeSupabaseClient(self._pgclient, self._auth_admin)  # type: ignore[assignment]
        self.settings = settings  # type: ignore[assignment]

    # ============================================================
    # 测试断言辅助：直接读内部存储
    # ============================================================
    def _count_history(self, user_id: str) -> int:
        return sum(1 for r in self._storage.get(TABLE_HISTORY, []) if str(r.get("user_id")) == user_id)

    def _count_preferences(self, user_id: str) -> int:
        return sum(1 for r in self._storage.get(TABLE_PREFS, []) if str(r.get("user_id")) == user_id)


# ============================================================
# JWT mock（测试里不验签，只需要 payload 有 sub/email 即可）
# ============================================================

def _make_token(user_id: str, email: str = "e2e@example.com") -> str:
    """造一条 JWT；签名部分被 mock 掉。

    header: kid=testkid, alg=RS256
    payload: sub=user_id, email=email, iss=..., exp=9999999999
    """
    header = {"alg": "RS256", "typ": "JWT", "kid": "testkid"}
    payload = {
        "sub": user_id,
        "email": email,
        "iss": "https://example.supabase.co/auth/v1",
        "exp": 9999999999,
        "role": "authenticated",
    }

    def _b64url(data: dict[str, Any]) -> str:
        raw = json.dumps(data, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    h = _b64url(header)
    p = _b64url(payload)
    sig = base64.urlsafe_b64encode(b"FAKESIG").rstrip(b"=").decode()
    return f"{h}.{p}.{sig}"


# ============================================================
# TestClient 构建
# ============================================================

SETTINGS_OVERRIDES = {
    "_env_file": None,
    "app_env": "test",
    "app_mode": "mock",
    "poi_provider": "mock",
    "ai_provider": "mock",
    "ai_api_key": "",
    "ew_ai_key_passphrase": "",
    "ew_ai_salt": "",
    "mock_ai_mode": "normal",
    "supabase_url": "https://example.supabase.co",
    "supabase_service_role_key": "eyJhbGciOi.eyJ9.FAKE",
    "supabase_anon_key": "eyJhbGciOi.eyJ9.FAKE_ANON",
}


def _build_e2e_app(monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, _MockSupabaseAdminClient]:
    from app.core.config import get_settings
    from app.core.supabase_client import get_supabase_admin

    settings = Settings(**SETTINGS_OVERRIDES)
    storage: dict[str, list[dict[str, Any]]] = {}
    auth_state = _AuthState()
    sb = _MockSupabaseAdminClient(storage, auth_state, settings)

    async def _override_sb() -> AsyncIterator[SupabaseAdminClient]:
        yield sb  # type: ignore[return-value]

    async def _async_jwk(_kid: str, _s: Settings) -> dict[str, str]:
        return {"kty": "RSA", "kid": "testkid", "n": "tZ8VKQ", "e": "AQAB", "use": "sig", "alg": "RS256"}

    def _pub_from_jwk(_jwk: dict[str, str]) -> object:
        return object()

    monkeypatch.setattr("app.api.v1.auth._fetch_jwk_for_header", _async_jwk)
    monkeypatch.setattr("app.api.v1.auth._public_key_from_jwk", _pub_from_jwk)

    # 跳过真实 RSA 验签：直接从 JWT payload 段 decode 返回 claims
    def _fake_pyjwt_decode(token: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
        _, payload_b64, _ = token.split(".")
        payload_b64 += "=" * (-len(payload_b64) % 4)
        return json.loads(base64.urlsafe_b64decode(payload_b64))

    # 懒加载：import auth 模块后再 patch 它局部引用的 pyjwt
    from app.api.v1 import auth as auth_mod
    monkeypatch.setattr(auth_mod.pyjwt, "decode", _fake_pyjwt_decode)  # type: ignore[attr-defined]

    app = create_app(settings)
    app.dependency_overrides[get_supabase_admin] = _override_sb
    app.dependency_overrides[get_settings] = lambda: settings
    return TestClient(app), sb


# ============================================================
# 基础答案 payload（推荐问卷 v1.0）
# ============================================================

START_BODY_LUNCH = {
    "entry_intent": "ai_recommend",
    "questionnaire_version": "v1.0",
    "answers_by_question_id": {
        "meal_period": ["lunch"],
        "appetite": ["normal"],
        "q_budget": ["from_20_to_30"],
        "q_cuisine_sichuan": ["c_sichuan"],
        "q_taste_spicy": ["spicy"],
    },
}


def _answer_body(round_1based: int) -> dict[str, Any]:
    """返回对应轮次的默认模板题答案（value 固定，使回答合法）。"""
    from app.services.ai.mock_provider import FOLLOW_UP_TEMPLATES

    tpl = FOLLOW_UP_TEMPLATES[round_1based - 1]
    return {
        "question_id": tpl.question_id,
        "selected_option_value": tpl.options[0].value,
    }


# ============================================================
# 实际 E2E 测试
# ============================================================


def test_p7_01_e2e_complete_gdpr_loop(monkeypatch: pytest.MonkeyPatch, caplog) -> None:
    """完整闭环：start → 三轮回答 → final → 2 次推荐产生 history+preference → GDPR 删除 → 0 条。"""
    client, sb = _build_e2e_app(monkeypatch)
    user_id = "11111111-1111-1111-1111-111111111111"
    sb._auth.add(user_id)
    token = _make_token(user_id)
    auth = {"Authorization": f"Bearer {token}"}

    # ---------- 1. session/start ----------
    with caplog.at_level(logging.INFO, logger="app.api.v1.recommendations"):
        start_resp = client.post("/api/v1/recommendations/session/start", json=START_BODY_LUNCH, headers=auth)
    assert start_resp.status_code == 200, start_resp.text
    body = start_resp.json()
    assert body["stage"] == "follow_up"
    session_id: str = body["session_id"]
    assert len(session_id) >= 16
    q1 = body.get("question")
    assert q1 is not None, "start 必须返回第 1 道追问题"
    assert q1["question_id"]

    # 初始空
    assert sb._count_preferences(user_id) == 0
    assert sb._count_history(user_id) == 0

    # ---------- 2. answer 第 1 轮 ----------
    r1 = client.post(
        f"/api/v1/recommendations/session/{session_id}/answer",
        json=_answer_body(1),
        headers=auth,
    )
    assert r1.status_code == 200, r1.text
    j1 = r1.json()
    assert j1["stage"] == "follow_up"
    assert j1["question"] is not None

    # ---------- 3. answer 第 2 轮 ----------
    r2 = client.post(
        f"/api/v1/recommendations/session/{session_id}/answer",
        json=_answer_body(2),
        headers=auth,
    )
    assert r2.status_code == 200, r2.text
    j2 = r2.json()
    assert j2["stage"] == "follow_up"
    assert j2["question"] is not None

    # ---------- 4. answer 第 3 轮 → 自动进入 final ----------
    r3 = client.post(
        f"/api/v1/recommendations/session/{session_id}/answer",
        json=_answer_body(3),
        headers=auth,
    )
    assert r3.status_code == 200, r3.text
    j3 = r3.json()
    assert j3["stage"] == "final", f"第3轮后应自动final，实际 stage={j3['stage']}"
    items = j3.get("candidates") or []
    assert len(items) == 5, f"final 必须正好 5 条，实际 {len(items)}"

    # 5 条互不相同 food_code
    codes = [it["food_code"] for it in items]
    assert len(set(codes)) == 5

    # ---------- 5. history + preference 都被写入 ≥1 ----------
    n_hist_1 = sb._count_history(user_id)
    n_pref_1 = sb._count_preferences(user_id)
    assert n_hist_1 >= 1, f"history 至少 1 条，实际 {n_hist_1}"
    assert n_pref_1 >= 1, f"preferences 至少 1 条，实际 {n_pref_1}"

    # ---------- 6. 再做一次纯规则的 /recommendations（POST /api/v1/recommendations）----------
    r4 = client.post("/api/v1/recommendations", json=START_BODY_LUNCH, headers=auth)
    assert r4.status_code == 200, r4.text
    r4_body = r4.json()
    r4_items = r4_body.get("items") if isinstance(r4_body, dict) else r4_body
    assert isinstance(r4_items, list) and len(r4_items) == 5
    # 第 2 次命中 preference_merge（此时已有 1 条 pref snapshot），merged_pref_fields 应非空
    # （测试用 StartBody 很多字段都是 None；所以至少 tastes/avoidances/meal 某几个会被回填）
    merged_r4 = r4_body.get("merged_pref_fields") if isinstance(r4_body, dict) else []
    assert isinstance(merged_r4, list), f"merged_pref_fields 应为 list，实际 {type(merged_r4)}"
    # 注意：如果 snapshot 的 preferences 快照没真正改变 rule_answers（字段相同），可能空数组，
    # 这里不强断言 >0，只校验类型；后续若加针对性单测可更细。

    n_hist_2 = sb._count_history(user_id)
    n_pref_2 = sb._count_preferences(user_id)
    assert n_hist_2 >= n_hist_1 + 1, "第二次推荐应追加 history"
    assert n_pref_2 >= n_pref_1 + 1, "第二次推荐应追加 preference snapshot"

    # ---------- 6b. P7-06：GET /api/v1/auth/me/export（GDPR 可携导出）----------
    r_export = client.get("/api/v1/auth/me/export", headers=auth)
    assert r_export.status_code == 200, f"导出接口应为 200，实际 {r_export.status_code}:{r_export.text}"
    exp = r_export.json()
    assert exp.get("exported_at"), "顶层 exported_at 字段缺失"
    assert exp["user_meta"]["user_id"] == user_id, f"导出 user_id 不匹配 {exp['user_meta']}"
    assert exp["user_meta"]["email"] == "e2e@example.com"
    # history_count / pref_count 计数应与之前累计的相符（≥2 条 history，≥2 条 preference）
    assert exp["recommendation_history_count"] >= 2, f"history_count {exp['recommendation_history_count']} 应 ≥2"
    assert exp["preference_snapshots_count"] >= 2, f"pref_count {exp['preference_snapshots_count']} 应 ≥2"
    assert len(exp["recommendation_history"]) == exp["recommendation_history_count"]
    assert len(exp["preference_snapshots"]) == exp["preference_snapshots_count"]
    assert exp["_partial"] is False, f"应全部命中，partial_warnings={exp.get('_partial_warnings')}"

    # ---------- 7. GDPR：DELETE /api/v1/auth/me ----------
    r_del = client.delete("/api/v1/auth/me", headers=auth)
    assert r_del.status_code == 204, f"GDPR 删除接口应为 204，实际 {r_del.status_code}:{r_del.text}"

    # ---------- 8. 最终两表 0 条 ----------
    assert sb._count_history(user_id) == 0, "history 应为 0 条（GDPR CASCADE 不满足？）"
    assert sb._count_preferences(user_id) == 0, "preferences 应为 0 条（GDPR CASCADE 不满足？）"


# P8-01a 专项：冷启动画像合并 + merged_pref_fields 细粒度断言
# 思路：
#   a) 先在 Supabase 里塞 1 条精心构造的 preference snapshot（answers_snapshot 明确有 appetite=small / avoidances=[seafood]/
#      budget=under_20 / spicy=mild / taste=sweet）
#   b) 用 answers_by_question_id 几乎空白的 START_BODY（除 meal_period 外全空）POST /api/v1/recommendations
#   c) 断言 merged_pref_fields ≥ 4 条命中 filled_blank，每条 kind 正确，old/new_value 都有
#   d) 且最终 candidates 里"避开海鲜"的 reason_tags 存在（说明合并偏好真的生效了，不仅是 banner 用）
def test_p8_01a_preference_merge_detailed(monkeypatch: pytest.MonkeyPatch) -> None:
    """专项验证：preference snapshot → 新空白问卷 merged_pref_fields 字段细节 + 对最终 Top5 的影响。"""
    client, sb = _build_e2e_app(monkeypatch)
    user_id = "22222222-2222-2222-2222-222222222222"
    sb._auth.add(user_id)
    token = _make_token(user_id)
    auth = {"Authorization": f"Bearer {token}"}

    # (a) 手动写一条精心构造的 snapshot（直接是 QuestionnaireAnswers.model_dump() 的顶层字段，
    #     与真实写入时保持一致——不要 answers_snapshot/summary_json 套壳）。
    snapshot_a: dict[str, Any] = {
        "questionnaire_version": "v1.0",
        # 与 START_BODY_EMPTY 相同的字段 → 期待 kind=reused
        "meal_period": "dinner",
        # 单值回填字段 → 期待 kind=filled_blank（注意枚举值取自 app/schemas/enums.py）
        "appetite": "light",       # Appetite.LIGHT；原写 small 是非法，枚举只有 light/normal/hungry
        "budget": "under_20",      # BudgetTier.UNDER_20 合法
        "max_distance_m": 2500,
        # 列表字段：合并去重 → 期待 kind=filled_blank（因为 cur avoidances=[]）
        # Avoidance 枚举值：none/seafood/meat/vegetarian，没有 cilantro
        "avoidances": ["seafood", "meat"],
        # Taste 枚举值：any/light/spicy/sour/sweet/salty，light/sweet 合法
        "tastes": ["sweet", "light"],
        # ai_follow_up_answers：给 2 条预设（dict，合法）
        "ai_follow_up_answers": {
            "q_who_with": "family",
            "q_occasion": "reunion",
        },
    }
    snap_id = uuid.uuid4()
    sb._storage.setdefault(TABLE_PREFS, []).append({
        "id": str(snap_id),
        "user_id": user_id,
        "questionnaire_version": "v1.0",
        "dictionary_version": "v1.0",
        "source_session_id": "manual_p801a_seed",
        "source_history_id": None,
        # 真实 DB 列名是 snapshot_jsonb（见 app/api/v1/preferences.py::_row_to_response L177）
        "snapshot_jsonb": copy.deepcopy(snapshot_a),
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
    })
    assert sb._count_preferences(user_id) == 1

    # (b) 用"几乎空白"的 body 做 POST /recommendations（纯规则引擎路径）—— 只有 meal_period=dinner（与 snapshot 一致=reused），其余都空，期待被 snapshot 回填
    START_BODY_EMPTY: dict[str, Any] = {
        "entry_intent": "ai_recommend",
        "questionnaire_version": "v1.0",
        "answers_by_question_id": {
            # 仅填 meal_period 与 snapshot 完全一致 → reused
            "meal_period": ["dinner"],
            # 其他 appetite / budget / taste 等全部留空 → 期待 snapshot 命中 filled_blank
        },
    }
    r = client.post("/api/v1/recommendations", json=START_BODY_EMPTY, headers=auth)
    assert r.status_code == 200, r.text
    body = r.json()
    items = body.get("items") or []
    assert isinstance(items, list) and len(items) == 5, f"纯规则引擎路径应 5 候选，实际 {len(items)}"

    # (c) 断言 merged_pref_fields 细节
    merged = body.get("merged_pref_fields")
    assert isinstance(merged, list) and merged, f"空白 body 应命中多条 merged_pref_fields，实际={merged!r}"
    # 真实后端 diff 结构：
    #   单值字段 {field, kind:single, before, after, change:filled}
    #   列表字段 {field, kind:list, before, after, change:appended, added_items}
    #   ai_follow_up  {field:ai_follow_up_answers, kind:ai_follow_up, change:appended, added_keys, added_items}
    # 注意： meal_period 相同（dinner）= reused，不加入 diff（因为 b==a）。
    kinds = [f.get("kind") for f in merged]
    fields = [f.get("field") for f in merged]
    # 至少命中 1 条 single filled + 1 条 list appended + 1 条 ai_follow_up appended
    assert "single" in kinds, f"单值字段应命中 filled；实际 kinds={kinds}; fields={fields}"
    assert "list" in kinds, f"列表字段 avoidances/tastes 应命中 appended；kinds={kinds}; fields={fields}"
    assert "ai_follow_up" in kinds, f"ai_follow_up_answers 应命中 appended；kinds={kinds}; fields={fields}"
    # 结构校验：每一个条目都必须有 field + kind + before + after + change
    for f in merged:
        assert isinstance(f, dict), f"merged 条目应为 dict：{f!r}"
        assert isinstance(f.get("field"), str) and len(f["field"]) > 0, f"缺 field：{f!r}"
        assert f["kind"] in {"single", "list", "ai_follow_up"}, f"未知 kind：{f!r}"
        assert f.get("change") in {"filled", "appended"}, f"未知 change：{f!r}"
        if f["kind"] == "single":
            assert f.get("change") == "filled", f"single 仅 change=filled：{f!r}"
            # before 空 (None / [] / "")，after 非空
            b, a = f.get("before"), f.get("after")
            assert b in (None, [], ""), f"single filled 要求 before 空：{f!r}"
            assert a not in (None, [], ""), f"single filled 要求 after 非空：{f!r}"
        if f["kind"] == "list":
            assert f.get("change") == "appended", f"list 仅 change=appended：{f!r}"
            added = f.get("added_items")
            assert isinstance(added, list) and len(added) >= 1, f"list appended 缺 added_items：{f!r}"
        if f["kind"] == "ai_follow_up":
            assert f.get("change") == "appended", f"ai_follow_up 仅 change=appended：{f!r}"
            added_keys = f.get("added_keys")
            added_map = f.get("added_items")
            assert isinstance(added_keys, list) and len(added_keys) >= 1, f"缺 added_keys：{f!r}"
            assert isinstance(added_map, dict) and len(added_map) >= 1, f"缺 added_items map：{f!r}"
    # avoidances 应在 merged 中作为 list 出现（或 field 名）
    assert "avoidances" in fields, f"快照有 avoidances 且 cur avoidances=[]，应命中 appended；fields={fields}"
    assert "tastes" in fields, f"快照有 tastes 且 cur tastes=[]，应命中 appended；fields={fields}"
    # 单值 appetite / budget / max_distance_m 三个至少出现 2 个（cur 都是空 → filled）
    single_fill_count = sum(1 for f in merged if f["kind"] == "single" and f["field"] in {
        "appetite", "budget", "max_distance_m", "explicit_food_preference",
    })
    assert single_fill_count >= 2, f"单值字段应回填 ≥2；实际 {single_fill_count} 条：{merged!r}"

    # (d) 第二次 POST：用户明确带了 answers_by_question_id（body 中包含多个题的答案）。
    #     由于问卷 v1.0 的 question_id 实际命名与我们的短字段不完全一致，
    #     这里不严格断言哪些字段"没有 merged"，只验证我们能保证的性质：
    #       - avoidances/tastes 仍未在 body 中给出 → merged_pref_fields 中依然出现；
    #       - merged_pref_fields 整体结构与字段类型正确；
    #       - history + preference 快照计数递增。
    START_BODY_OVERWRITE: dict[str, Any] = {
        "entry_intent": "ai_recommend",
        "questionnaire_version": "v1.0",
        "answers_by_question_id": {
            "meal_period": ["dinner"],
            "appetite": ["hungry"],      # 与 snapshot appetite=light 意图相反（若 question id 匹配则生效）
            "q_budget": ["over_30"],     # snapshot=under_20 → 意图相反
            "q_taste_spicy": ["spicy"],
        },
    }
    r2 = client.post("/api/v1/recommendations", json=START_BODY_OVERWRITE, headers=auth)
    assert r2.status_code == 200, r2.text
    b2 = r2.json()
    m2 = b2.get("merged_pref_fields")
    assert isinstance(m2, list), f"merged_pref_fields 应为 list，实际 {type(m2)}"
    fields2 = [f.get("field") for f in m2]
    [f.get("kind") for f in m2]
    # 结构校验：每个 merged 条目形状都合法
    for f in m2:
        assert isinstance(f, dict), f"merged 条目应为 dict：{f!r}"
        assert f.get("field"), f"缺 field：{f!r}"
        assert f.get("kind") in {"single", "list", "ai_follow_up"}, f"未知 kind：{f!r}"
    # avoidances 快照有、body 没给出（即使有 translated 也应该是 []）→ merged 里应含 list appended
    assert "avoidances" in fields2, f"avoidances 仍未填，应出现在 merged；fields2={fields2}"
    avoids2 = next(f for f in m2 if f["field"] == "avoidances")
    assert avoids2["kind"] == "list" and avoids2["change"] == "appended"
    # tastes 同上
    assert "tastes" in fields2, f"tastes 仍未填，应出现在 merged；fields2={fields2}"
    tastes2 = next(f for f in m2 if f["field"] == "tastes")
    assert tastes2["kind"] == "list" and tastes2["change"] == "appended"
    # ai_follow_up_answers 未填（body 里没给 ai_follow_up）→ 应 merged
    assert "ai_follow_up_answers" in fields2, f"ai_follow_up_answers 应 merged；fields2={fields2}"

    # 写入历史 + 偏好快照计数增长（≥ 2 条）
    assert sb._count_history(user_id) >= 2
    assert sb._count_preferences(user_id) >= 2

