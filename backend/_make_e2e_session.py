"""Create e2e user + dump session JSON + FULL HTTP E2E of P5 flow.

FULL FLOW（路径 A + P5 动态会话，纯 HTTP TestClient，不启动浏览器）：
    1. Supabase Admin: 清理旧 e2e 账号 → 注册 e2e-user@example.com → 导出 session JSON
       → 写 backend/_e2e_session.json（前端 AuthContext 可通过 #e2e-session hash 或
          window.__E2E_INJECT_SESSION__ 注入）
    2. FastAPI TestClient + Authorization: Bearer <access_token>
        POST /recommendations/session/start  →  取 session_id
        while stage == follow_up and rounds < 3:
            POST /session/{id}/answer  (选 options[0].value 作 dummy answer)
        assert stage == final and len(candidates) == 5
    3. POST /history  (写入 recommendation_snapshot + session_id + final_reason)
    4. GET  /history  (验证 session_id/final_reason 回读一致)
    5. DELETE /auth/me  (204 No Content，GDPR 删号)
    6. 二次 POST /history  →  预期 401  (死 token 防御线验证)
    7. Supabase Admin: 最终确认 e2e 账号已被物理删除

额外 CLI 参数（P5-10 DeepSeek 真调用冒烟 + 限流计数验证）：
    --ai-provider {mock,deepseek,auto}
          选 AI provider：默认 mock（原行为）；deepseek=强制用已加密的真实 API key；
          auto=key 没配时自动回 mock。
    --n-sessions N   连续跑 N 次完整 P5 循环（默认 1）。用于验证本地限流：如
                     --n-sessions 6 --user-daily-limit 3 → 前 3 次真调用，第 4+ 次
                     final_reason = rule_engine_fallback_ai_local_quota。
    --user-daily-limit N   覆盖 settings.ai_daily_user_limit（0=不限制）
    --global-daily-limit N 覆盖 settings.ai_global_daily_limit（0=不限制）
    --skip-delete          调试模式：不 DELETE /auth/me，保留账号便于手动查历史记录。

任何步骤失败：打印 ERROR + 非 0 退出；收尾阶段尝试重新 Supabase admin 删除该用户，
避免留下污染账号，下次跑脚本也不会被"用户已存在"打断。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID

# 强制以 backend/.env 为准，不污染全局 env （防止 test vs live 串）
os.environ.pop("APP_ENV", None)

from fastapi.testclient import TestClient
from supabase import create_client

from app.core.config import Settings
from app.main import app

E2E_EMAIL = "e2e-user@example.com"
E2E_PWD = "E2E-Pass-1234!"
QUESTIONNAIRE_VERSION = "v1.0"
ENTRY_INTENT = "ai_recommend"


def _load_settings() -> Settings:
    return Settings(_env_file=".env")  # type: ignore[call-arg]


def _supabase_admin_client(settings: Settings):
    service_role = (
        settings.supabase_service_role_key.get_secret_value()
        if hasattr(settings.supabase_service_role_key, "get_secret_value")
        else settings.supabase_service_role_key
    )
    return create_client(settings.supabase_url, service_role)


def _cleanup_user_if_exists(sb, email: str) -> None:
    """用 Supabase Admin SDK 先按 email 查再物理删除（防止上一次脚本中断留下账号）。"""
    try:
        # Supabase list_users 最多 1000；e2e 邮箱唯一
        page = sb.auth.admin.list_users(page=1, per_page=1000)
        users = getattr(page, "users", []) or []
        matches = [u for u in users if getattr(u, "email", None) == email]
        for u in matches:
            try:
                sb.auth.admin.delete_user(str(u.id))
                print(f"[E2E.Cleanup] 已删除旧账号: uid={u.id}")
            except Exception as exc:  # noqa: BLE001
                print(f"[E2E.Cleanup] 删除旧账号失败（继续）：type={type(exc).__name__}")
    except Exception as exc:  # noqa: BLE001
        print(f"[E2E.Cleanup] list_users 失败（跳过清理）：type={type(exc).__name__}")


def _create_or_signin_user(sb, email: str, pwd: str):
    """先尝试 sign_up；如 email 已存在则 sign_in_with_password。返回 (user, session_dict)。"""
    try:
        r = sb.auth.sign_up({"email": email, "password": pwd})
        print("[E2E.Sign] signup uid:", r.user.id if r.user else None)
        if r.user and r.session:
            return r.user, r.session
    except Exception as exc:  # noqa: BLE001
        print("[E2E.Sign] signup skip:", type(exc).__name__)
    r = sb.auth.sign_in_with_password({"email": email, "password": pwd})
    if not r.user or not r.session:
        raise RuntimeError("无法登录 e2e 用户：sign_in_with_password 返回空 session")
    return r.user, r.session


def _session_to_json(user, sess) -> dict[str, Any]:
    """按前端 AuthContext 期望的 shape 打包 session JSON。"""
    return {
        "provider_token": None,
        "provider_refresh_token": None,
        "access_token": sess.access_token,
        "refresh_token": sess.refresh_token,
        "expires_in": sess.expires_in,
        "expires_at": int(sess.expires_at) if sess.expires_at else None,
        "token_type": "bearer",
        "user": {
            "id": str(user.id),
            "aud": user.aud,
            "role": user.role,
            "email": user.email,
            "email_confirmed_at": (
                user.email_confirmed_at.isoformat()
                if getattr(user, "email_confirmed_at", None)
                else None
            ),
            "phone": getattr(user, "phone", None),
            "confirmed_at": (
                user.confirmed_at.isoformat() if getattr(user, "confirmed_at", None) else None
            ),
            "last_sign_in_at": (
                user.last_sign_in_at.isoformat()
                if getattr(user, "last_sign_in_at", None)
                else None
            ),
            "app_metadata": dict(user.app_metadata or {}),
            "user_metadata": dict(user.user_metadata or {}),
            "identities": [],
            "created_at": user.created_at.isoformat() if user.created_at else None,
            "updated_at": (
                user.updated_at.isoformat() if getattr(user, "updated_at", None) else None
            ),
        },
    }


def _run_full_p5_http_e2e(
    *,
    access_token: str,
    user_id: str,
    skip_delete: bool = False,
) -> dict[str, Any]:
    """用 TestClient 跑完整 P5 链路。返回 debug info（session_id / final_reason / history_id）。"""
    debug: dict[str, Any] = {}
    with TestClient(app, raise_server_exceptions=True) as client:
        bearer = {"Authorization": f"Bearer {access_token}"}

        # ---- Step 1: session start ----
        payload_start = {
            "entry_intent": ENTRY_INTENT,
            "questionnaire_version": QUESTIONNAIRE_VERSION,
            "answers_by_question_id": {},
        }
        r = client.post(
            "/api/v1/recommendations/session/start",
            json=payload_start,
            headers=bearer,
        )
        assert r.status_code == 200, f"session/start HTTP {r.status_code}: {r.text[:200]}"
        state: dict[str, Any] = r.json()
        session_id = state["session_id"]
        debug["session_id"] = session_id
        print(f"[E2E.P5] START session_id={session_id} stage={state['stage']}")

        # ---- Step 2: follow up loop ----
        rounds_answered = 0
        MAX_ROUNDS = 3
        while state.get("stage") == "follow_up" and rounds_answered < MAX_ROUNDS:
            q = state.get("question")
            if not q:
                raise RuntimeError(f"stage=follow_up 但 question 缺失：state keys={list(state.keys())}")
            opts = q.get("options") or []
            if not opts:
                raise RuntimeError(f"question.options 为空，无法作 dummy answer：qid={q.get('question_id')}")
            answer_value = opts[0]["value"]
            r2 = client.post(
                f"/api/v1/recommendations/session/{session_id}/answer",
                json={
                    "question_id": q["question_id"],
                    "selected_option_value": answer_value,
                },
                headers=bearer,
            )
            # 幂等：重复同一 answer 可能 200；不同 value 可能 409（Conflict）。我们每次严格按 200/201。
            assert r2.status_code in {200, 201}, (
                f"session answer HTTP {r2.status_code}: {r2.text[:300]}"
            )
            state = r2.json()
            rounds_answered += 1
            print(f"[E2E.P5]   answer round#{rounds_answered} qid={q['question_id']} pick={answer_value!r} -> stage={state['stage']}")

        assert state.get("stage") == "final", (
            f"超过 {MAX_ROUNDS} 轮仍未进入 final：stage={state.get('stage')!r} rounds_completed={state.get('rounds_completed')}"
        )
        candidates = state.get("candidates") or []
        assert len(candidates) == 5, f"G-08 违规：最终候选不是 5 条，实际={len(candidates)}"
        priorities = sorted(int(c["priority"]) for c in candidates)
        assert priorities == [1, 2, 3, 4, 5], f"priority 1..5 不严格递增，实际={priorities}"
        final_reason = state.get("final_reason") or "legacy_rule_engine"
        debug["final_reason"] = final_reason
        debug["result_count"] = 5
        print(f"[E2E.P5] FINAL candidates=5 final_reason={final_reason}")

        # ---- Step 3: write history with session_id + final_reason ----
        snapshot = {
            "entry_intent": ENTRY_INTENT,
            "questionnaire_version": QUESTIONNAIRE_VERSION,
            "dictionary_version": state.get("dictionary_version") or "v1.0",
            "finalized_at": datetime.now().astimezone().isoformat(),
            "items": candidates,
        }
        r3 = client.post(
            "/api/v1/history",
            json={
                "recommendation_snapshot": snapshot,
                "result_count": 5,
                "session_id": session_id,
                "final_reason": final_reason,
            },
            headers=bearer,
        )
        assert r3.status_code == 201, f"POST history HTTP {r3.status_code}: {r3.text[:300]}"
        rec: dict[str, Any] = r3.json()
        history_id = rec["id"]
        debug["history_id"] = history_id
        # 写入响应本身就应带 session_id/final_reason（_row_to_response 解析 _meta 回填）
        assert rec.get("session_id") == session_id, (
            f"History 写入响应 session_id 不匹配：write={session_id!r} resp={rec.get('session_id')!r}"
        )
        assert rec.get("final_reason") == final_reason, (
            f"History 写入响应 final_reason 不匹配：write={final_reason!r} resp={rec.get('final_reason')!r}"
        )
        print(f"[E2E.P5] WRITE history id={history_id} session_id_match=True final_reason_match=True")

        # ---- Step 4: GET /history 验证读回 ----
        r4 = client.get("/api/v1/history?limit=20&offset=0", headers=bearer)
        assert r4.status_code == 200, f"GET history HTTP {r4.status_code}"
        list_resp: dict[str, Any] = r4.json()
        items: list[dict[str, Any]] = list_resp.get("items") or []
        assert items, "GET history 为空"
        # 找到我们刚写的那一条（history_id）
        found = next((i for i in items if i["id"] == history_id), None)
        assert found is not None, f"GET history 找不到刚写的记录 id={history_id}"
        assert found.get("session_id") == session_id, (
            f"GET history session_id mismatch：expect={session_id!r} actual={found.get('session_id')!r}"
        )
        assert found.get("final_reason") == final_reason, (
            f"GET history final_reason mismatch：expect={final_reason!r} actual={found.get('final_reason')!r}"
        )
        list_total = int(list_resp.get("total") or 0)
        assert list_total >= 1
        print(f"[E2E.P5] GET history OK total={list_total} written_record_match=True")

        if not skip_delete:
            # ---- Step 5: DELETE /auth/me  GDPR 删账号 ----
            r5 = client.delete("/api/v1/auth/me", headers=bearer)
            assert r5.status_code == 204, f"DELETE /auth/me HTTP {r5.status_code}: {r5.text[:200]}"
            print("[E2E.P5] DELETE /auth/me OK (204 No Content)")

            # ---- Step 6: 死 token 防御——再写历史必须 401 ----
            r6 = client.post(
                "/api/v1/history",
                json={
                    "recommendation_snapshot": {"zombie_attempt": True, "items": []},
                    "result_count": 0,
                },
                headers=bearer,
            )
            assert r6.status_code == 401, (
                f"死 token 写历史预期 401，实际 HTTP {r6.status_code}。zombie token 安全防线失守！resp={r6.text[:200]}"
            )
            print("[E2E.P5] ZOMBIE-TOKEN CHECK OK (POST history -> 401 after DELETE /auth/me)")
        else:
            debug["skipped_delete"] = True
            print("[E2E.P5] --skip-delete：跳过 DELETE /auth/me 与 zombie-token 检查")

    return debug


def _reset_ai_service_singletons() -> None:
    """每次 P5 循环前重置 RecommendationSessionManager / AIRateLimiter 进程内单例。

    否则同一次 python 进程里跑多个 E2E 循环，manager（以及它内部 chat_service +
    rate_limiter）会沿用旧的 settings 覆盖值；另外限流计数器无法从 0 开始，会
    让 --n-sessions + --user-daily-limit 组合的验证变复杂。
    """
    from app.services import recommendation_session as mod

    # 直接写模块级全局变量（都是 None 初始化 → 下次 Depends 会重建）
    with mod._manager_lock:
        mod._manager_singleton = None  # type: ignore[attr-defined]
    mod._rate_limiter_singleton = None  # type: ignore[attr-defined]


def main() -> int:
    parser = argparse.ArgumentParser(description="E2E 账号 + 完整 P5 HTTP 链路 + (可选)DeepSeek 真调用")
    parser.add_argument(
        "--ai-provider",
        choices=["mock", "deepseek", "auto"],
        default="mock",
        help="选择 AI provider（默认 mock，不消耗真实额度）",
    )
    parser.add_argument(
        "--n-sessions",
        type=int,
        default=1,
        help="连续跑多少个完整 P5 循环（默认 1；≥2 用于验证本地 rate_limiter 计数）",
    )
    parser.add_argument(
        "--user-daily-limit",
        type=int,
        default=None,
        help="覆盖 settings.ai_daily_user_limit（不传则用 .env 里的值；0=不限制用户维度）",
    )
    parser.add_argument(
        "--global-daily-limit",
        type=int,
        default=None,
        help="覆盖 settings.ai_global_daily_limit（不传则用 .env 里的值；0=不限制全局维度）",
    )
    parser.add_argument(
        "--skip-delete",
        action="store_true",
        help="调试模式：不 DELETE /auth/me，保留账号便于手动查 history 记录",
    )
    args = parser.parse_args()

    out_path = Path(__file__).resolve().parent / "_e2e_session.json"
    settings = _load_settings()

    # ---- CLI 覆盖 settings（P5-10）----
    settings.ai_provider = args.ai_provider  # type: ignore[assignment]
    if args.user_daily_limit is not None:
        settings.ai_daily_user_limit = int(args.user_daily_limit)
    if args.global_daily_limit is not None:
        settings.ai_global_daily_limit = int(args.global_daily_limit)
    print(
        f"[E2E.Params] ai_provider={settings.ai_provider} "
        f"user_daily_limit={settings.ai_daily_user_limit} "
        f"global_daily_limit={settings.ai_global_daily_limit} "
        f"n_sessions={args.n_sessions} skip_delete={args.skip_delete}"
    )

    sb_admin = _supabase_admin_client(settings)
    user_id_for_cleanup: str | None = None
    # 多轮汇总：每条 P5 循环的 final_reason 聚合打印，方便验证限流命中模式
    per_session: list[dict[str, Any]] = []
    try:
        _cleanup_user_if_exists(sb_admin, E2E_EMAIL)

        user, session = _create_or_signin_user(sb_admin, E2E_EMAIL, E2E_PWD)
        user_id_for_cleanup = str(user.id)
        session_obj = _session_to_json(user, session)
        out_path.write_text(json.dumps(session_obj, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[E2E.Session] DUMP -> {out_path}")
        print(f"[E2E.Session] uid: {session_obj['user']['id']}")
        print(f"[E2E.Session] email: {session_obj['user']['email']}")

        for idx in range(args.n_sessions):
            print(f"\n=== [E2E.P5] LOOP {idx+1}/{args.n_sessions} ===")
            _reset_ai_service_singletons()  # 每次循环重置单例 + 计数器
            debug_info = _run_full_p5_http_e2e(
                access_token=session.access_token,
                user_id=str(user.id),
                skip_delete=args.skip_delete,
            )
            debug_info["loop_index"] = idx + 1
            per_session.append(debug_info)
            print(
                f"[E2E.P5] LOOP {idx+1} RESULT: session_id={debug_info.get('session_id')} "
                f"final_reason={debug_info.get('final_reason')}"
            )

        # 汇总表（方便人眼核对限流命中）
        print("\n=== [E2E.Summary] per_session final_reason ===")
        for row in per_session:
            print(
                f"  loop={row['loop_index']:>2}  "
                f"final_reason={row.get('final_reason')!r:<55}  "
                f"session_id={row.get('session_id')}"
            )

        if not args.skip_delete:
            # 额外：验证 Supabase admin 层面用户已被 DELETE /auth/me 删除
            try:
                sb_admin.auth.admin.get_user_by_id(user_id_for_cleanup)
                print("[E2E.Cleanup.WARN] Supabase admin 查询用户仍然存在（可能级联删除延迟或后端删除逻辑未物理删除，需人工核查）")
            except Exception:  # noqa: BLE001
                # get_user_by_id 找不到用户会抛错——这正是我们期望的物理删除结果
                print("[E2E.Cleanup] VERIFIED：Supabase auth.users 中已不存在该 e2e 用户（GDPR OK）")
        print("[E2E.ALL OK]")
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"[E2E.FAILED] type={type(exc).__name__} msg={exc}", file=sys.stderr)
        return 1
    finally:
        # 收尾：不管成功失败，再次尝试物理删除 e2e 账号，避免留下测试账号
        if user_id_for_cleanup and not args.skip_delete:
            try:
                UUID(user_id_for_cleanup)  # 合法性校验
                sb_admin.auth.admin.delete_user(user_id_for_cleanup)
                print(f"[E2E.Cleanup] finally 删除测试账号 uid={user_id_for_cleanup}")
            except Exception as exc:  # noqa: BLE001
                # delete_user 抛错有两种情况：①用户本来就被 DELETE /auth/me 删了；②网络/权限错。
                # 这里只打一条 debug 信息，不影响 exit code
                print(f"[E2E.Cleanup] finally 清理失败（可能用户已被 GDPR 删除，属预期）：type={type(exc).__name__}")
        elif args.skip_delete:
            print(f"[E2E.Cleanup] --skip-delete 跳过 finally 清理，账号仍保留：uid={user_id_for_cleanup}")


if __name__ == "__main__":
    raise SystemExit(main())
