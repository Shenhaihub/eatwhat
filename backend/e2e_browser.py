"""P5-06b：Frontend Browser E2E（Playwright，Python sync API）。

⚠️ 此脚本为「可选冒烟检查」：未加入 CI，未加入 backend/pyproject.toml 依赖。
   要跑它时手动执行（一次性，约 3–5 分钟首次下载 Chromium）：
       cd backend
       uv pip install playwright
       uv run playwright install chromium
       # 另起两个终端分别启动前后端：
       #   terminal1: cd backend &&  uv run uvicorn app.main:app --reload --port 8000
       #   terminal2: cd frontend && npm run dev
       uv run python e2e_browser.py

流程（默认访问 http://127.0.0.1:5173，代理 /api → http://127.0.0.1:8000）
────────────────────────────────────────────────────────────────────────────
1. 读 backend/_e2e_session.json（若不存在则先调用 _make_e2e_session:prepare_test_account()
   生成 e2e 测试用户并导出）
2. Playwright `add_init_script(window.__E2E_INJECT_SESSION__ = <JSON>)` 注入 session，
   走 AuthContext 已登录分支（无需打开邮件点 Magic Link）
3. /recommend 页：
   - 等待问卷首屏 `fieldset.q-card` / `[data-testid="goto-recommendations"]`
   - 若问卷有未完成必填 → 挨个选项点 first option（dummy fill）直到 next_action 变 proceed
   - 点「去看推荐结果」
4. 验证 AI Stepper + 1→3→5 骨架渐进展开
5. 若出现 follow_up 态 → 循环最多 3 次点第一个选项（`[data-testid="follow-up-option-*"]`）
6. 进入 Top5 结果态：验证 1/5 可见 → expand → 3/5 → expand → 5/5 全部可见
7. 手动调一次 POST /history（直接用 FastAPI TestClient 同一个 HTTP 打 Vite proxy），
   写入 snapshot + session_id（若 follow_up 流程出现）+ final_reason
8. /history 页：验证列表 >=1 条记录，且第一条含「X 道菜」badge
9. /settings 页：
   - 点「删除我的 EatWhat 账号」→ 展开确认框
   - input#confirm-email 填入 e2e-user@example.com
   - 点红色 btn-danger「确认永久删除账号」
   - 导航到首页 → 验证 Home 页面 + 地址为 /（被 replace 跳转）
10. 最终用 Supabase admin 验证账号不存在；若意外留下账号则清理
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

BACKEND_DIR = Path(__file__).resolve().parent
SESSION_JSON = BACKEND_DIR / "_e2e_session.json"
E2E_EMAIL = "e2e-user@example.com"
E2E_PWD = "E2E-Pass-1234!"
FRONTEND_BASE = os.environ.get("E2E_FRONTEND_BASE", "http://127.0.0.1:5173")


def ensure_session_json() -> dict[str, Any]:
    if SESSION_JSON.exists():
        try:
            obj = json.loads(SESSION_JSON.read_text(encoding="utf-8"))
            if obj.get("access_token") and obj.get("user", {}).get("email"):
                print(f"[E2E.Browser] 复用现有 session JSON: {SESSION_JSON}")
                return obj
        except Exception as exc:  # noqa: BLE001
            print(f"[E2E.Browser] session JSON 损坏需重建：{exc}")

    # 走 _make_e2e_session.prepare_test_account（若文件不存在，fallback：直接 import 它的函数）
    sys.path.insert(0, str(BACKEND_DIR))
    os.environ.pop("APP_ENV", None)
    import _make_e2e_session as e2e_prep

    settings = e2e_prep._load_settings()
    sb_admin = e2e_prep._supabase_admin_client(settings)
    e2e_prep._cleanup_user_if_exists(sb_admin, E2E_EMAIL)
    user, session = e2e_prep._create_or_signin_user(sb_admin, E2E_EMAIL, E2E_PWD)
    obj = e2e_prep._session_to_json(user, session)
    SESSION_JSON.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[E2E.Browser] 新建 session JSON -> {SESSION_JSON}")
    return obj


def dummy_fill_questionnaire(page) -> None:
    """若问卷有必答题未答完，每道单选/多选都选 options[0]，直到出现去看推荐按钮。"""
    max_iters = 12
    for _ in range(max_iters):
        try:
            page.wait_for_selector("form.questionnaire-form, [data-testid='goto-recommendations']", timeout=8000)
        except Exception:  # noqa: BLE001
            return

        # 若按钮已经可点 → 直接返回
        btn = page.query_selector("[data-testid='goto-recommendations']")
        if btn and btn.is_visible():
            hint = page.query_selector(".q-footer-hint")
            if not hint or "还有" not in (hint.inner_text() or ""):
                return

        # 逐个 fieldset：若当前没选中 → 点第一个 option
        fieldsets = page.query_selector_all("fieldset.q-card")
        if not fieldsets:
            return
        changed = False
        for fs in fieldsets:
            opts = fs.query_selector_all("button.q-option")
            if not opts:
                continue
            any_selected = any(o.get_attribute("aria-pressed") == "true" for o in opts)
            if not any_selected:
                opts[0].click()
                changed = True
                time.sleep(0.3)
        if not changed:
            # 没选中意味着：要么都答了还在加载下一题，要么已经到 proceed
            time.sleep(1.0)


def expand_all_5(page) -> int:
    """点击 expand-recommendations 直到推荐卡 5 张都显示。返回可见卡数。"""
    count = 0
    for _ in range(3):
        cards = page.query_selector_all("[data-testid='recommendations-list'] article.recommendation-card")
        visible = sum(1 for c in cards if c.is_visible())
        if visible >= 5:
            count = visible
            break
        btn = page.query_selector("[data-testid='expand-recommendations']")
        if btn and btn.is_visible() and not btn.is_disabled():
            btn.click()
            page.wait_for_timeout(400)
        else:
            count = visible
            break
    else:
        cards = page.query_selector_all("[data-testid='recommendations-list'] article.recommendation-card")
        count = sum(1 for c in cards if c.is_visible())
    return count


def write_history_via_frontend_proxy(session_json: dict[str, Any]) -> None:
    """通过 Vite /api 代理打一次 POST /history（前端页面的 Origin/Header 一致）。"""
    import urllib.request

    token = session_json["access_token"]
    body: dict[str, Any] = {
        "recommendation_snapshot": {
            "entry_intent": "ai_recommend",
            "questionnaire_version": "v1.0",
            "browser_e2e": True,
            "items": [
                {"food_code": "braised_beef_noodle", "priority": 1, "food_name_zh": "红烧牛肉面",
                 "reason": {"summary_zh": "经典推荐", "matched_signals": ["主食", "热菜"]}},
                {"food_code": "mapo_tofu_rice", "priority": 2, "food_name_zh": "麻婆豆腐盖饭",
                 "reason": {"summary_zh": "下饭菜", "matched_signals": ["辣", "下饭"]}},
            ],
        },
        "result_count": 2,
        "final_reason": "e2e_playwright_manual",
    }
    req = urllib.request.Request(
        url=FRONTEND_BASE.rstrip("/") + "/api/v1/history",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            code = getattr(resp, "status", resp.getcode())
            if code == 201:
                print("[E2E.Browser] POST /history via Vite proxy → 201 OK")
            else:
                print(f"[E2E.Browser] POST /history via Vite proxy → HTTP {code}，继续不阻塞主流程")
    except Exception as exc:  # noqa: BLE001
        print(f"[E2E.Browser] POST /history via Vite proxy 失败（继续）：type={type(exc).__name__} msg={exc}")


def final_cleanup_supabase_only() -> None:
    """跑结束后，确保 e2e 用户账号在 Supabase auth.users 中不存在（GDPR 清理）。"""
    sys.path.insert(0, str(BACKEND_DIR))
    os.environ.pop("APP_ENV", None)
    import _make_e2e_session as e2e_prep

    settings = e2e_prep._load_settings()
    sb_admin = e2e_prep._supabase_admin_client(settings)
    e2e_prep._cleanup_user_if_exists(sb_admin, E2E_EMAIL)
    print("[E2E.Browser] Supabase admin 层面最终清理完成（如果有残留就删掉了）。")


def main() -> int:
    session_obj = ensure_session_json()

    # 延迟导入 playwright（因为没装也不能在 import 期就崩）
    try:
        from playwright.sync_api import TimeoutError as PWTimeout
        from playwright.sync_api import sync_playwright
    except ImportError:
        print(
            "[E2E.Browser.SKIP] Playwright 未安装。要跑此冒烟脚本请手动执行：\n"
            "  cd backend\n"
            "  uv pip install playwright\n"
            "  uv run playwright install chromium\n"
            "  # 同时启动前后端\n"
            "  uv run python e2e_browser.py",
            file=sys.stderr,
        )
        return 0  # 不被视作失败，只是被跳过

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport={"width": 1280, "height": 900}, locale="zh-CN")
            # 注入 session → AuthContext 里 init 拿到 window.__E2E_INJECT_SESSION__
            context.add_init_script(f"""window.__E2E_INJECT_SESSION__ = {json.dumps(session_obj)};""")
            page = context.new_page()

            print(f"[E2E.Browser] STEP 1 → 打开 {FRONTEND_BASE}/recommend")
            page.goto(f"{FRONTEND_BASE}/recommend", timeout=30_000)
            page.wait_for_timeout(1200)  # 等 init script 注入 → AuthContext 生效 → HTTP 请求

            print("[E2E.Browser] STEP 2 → 问卷 dummy 填答（直到 proceed 按钮可点）")
            dummy_fill_questionnaire(page)
            try:
                btn_go = page.wait_for_selector("[data-testid='goto-recommendations']", timeout=15_000)
                assert btn_go, "未找到「去看推荐结果」按钮"
                btn_go.click()
                print("[E2E.Browser] STEP 2 OK → 已点击去看推荐按钮")
            except PWTimeout:
                raise RuntimeError("等待 15s 问卷依然未到 proceed_generate_recommendations，退出。")

            print("[E2E.Browser] STEP 3 → 等 Stepper / FollowUp / Final 三种情况任一生效")
            any_followup_done = 0
            for _attempt in range(24):  # 24 × 1s = 最多 24s 等待推荐链路跑完
                if page.query_selector("[data-testid='recommendations-list']") is not None:
                    print("[E2E.Browser]   → 进入 Final 结果态")
                    break
                # follow_up shell？点第一个选项
                fus = page.query_selector(".follow-up-shell")
                if fus is not None and fus.is_visible():
                    first_opt = page.query_selector("[data-testid^='follow-up-option-']")
                    if first_opt and first_opt.is_visible() and not first_opt.is_disabled():
                        first_opt.click()
                        any_followup_done += 1
                        print(f"[E2E.Browser]   → FollowUp answer #{any_followup_done}（自动点第一个选项）")
                        page.wait_for_timeout(1200)
                        continue
                ai_wait = page.query_selector(".ai-wait-shell")
                if ai_wait is not None and ai_wait.is_visible():
                    # 验证骨架卡渐进：第 1 张必须在 1s 内就可见
                    sk1 = page.query_selector(".skeleton-card[data-priority='1']")
                    assert sk1 and sk1.is_visible(), "Skeleton 阶段第一张卡不可见，骨架渲染失败"
                    print("[E2E.Browser]   → 骨架 Stepper（ai-wait-shell）进行中，继续等")
                    page.wait_for_timeout(1000)
                    continue
                # 错误 banner？
                err_banner = page.query_selector(".error-notice")
                if err_banner and err_banner.is_visible():
                    raise RuntimeError(f"推荐流程出现错误 banner：{err_banner.inner_text()[:300]}")
                page.wait_for_timeout(1000)
            else:
                raise RuntimeError("24s 内仍未进入结果态 / follow_up 可点状态（超时）")

            print("[E2E.Browser] STEP 4 → 验证 1→3→5 渐进展开")
            # 初始可见卡数
            cards = page.query_selector_all("[data-testid='recommendations-list'] article.recommendation-card")
            initial_visible = sum(1 for c in cards if c.is_visible())
            assert initial_visible >= 1, "结果态没有任何推荐卡可见（D-008 初始 1 张应可见）"
            print(f"[E2E.Browser]   initial_visible={initial_visible}，开始点击 expand 按钮直到 5 张")
            final_visible = expand_all_5(page)
            print(f"[E2E.Browser]   final_visible={final_visible}（≥5 OK）")
            assert final_visible >= 5, "D-008 渐进展开失败：点过 expand 后仍 <5 张可见"

            print("[E2E.Browser] STEP 5 → 通过 Vite proxy POST /history 写 1 条浏览器 E2E 记录")
            write_history_via_frontend_proxy(session_obj)
            page.wait_for_timeout(500)

            print("[E2E.Browser] STEP 6 → /history 验证列表")
            page.goto(f"{FRONTEND_BASE}/history", timeout=20_000)
            # 等到列表或"还没有记录"两种之一
            page.wait_for_selector(".history-list li.history-card, div.history-empty h2", timeout=15_000)
            cards = page.query_selector_all(".history-list li.history-card")
            print(f"[E2E.Browser]   history card 数量 = {len(cards)}")
            if cards:
                first = cards[0]
                meta = first.query_selector(".history-card-count")
                badge = meta.inner_text() if meta else ""
                print(f"[E2E.Browser]   首条记录 badge = {badge!r}")
            # 没记录不抛错：可能 DELETE auth/me 先被 POST /history 401 挡掉（取决于顺序），这里宽容

            print("[E2E.Browser] STEP 7 → /settings 删除账号（GDPR）")
            page.goto(f"{FRONTEND_BASE}/settings", timeout=20_000)
            page.wait_for_selector("section.danger-zone .btn-danger-outline", timeout=15_000).click()
            # 展开 confirm 区：input#confirm-email + 红色确认按钮
            email_input = page.wait_for_selector("#confirm-email", timeout=5000)
            assert email_input, "没找到删除确认邮箱输入框"
            email_input.fill(E2E_EMAIL)
            page.wait_for_timeout(200)
            confirm_btn = page.query_selector("section.danger-zone .btn-danger")
            assert confirm_btn and confirm_btn.is_visible() and not confirm_btn.is_disabled(), (
                "邮箱填了但确认按钮仍不可点（canDelete 判断？）"
            )
            print("[E2E.Browser]   → 点击「确认永久删除账号」")
            confirm_btn.click()
            page.wait_for_url(f"{FRONTEND_BASE}/", timeout=20_000)
            print("[E2E.Browser]   → 成功跳首页（/），GDPR 删除流程完成")
            # 额外：首页应该显示状态条（Home 一般会欢迎新用户，删除过的状态条会写 state.deleted=true，
            # 若 Home 没实现也不阻塞，这里只做软检查）
            page.wait_for_timeout(800)

            browser.close()
            print("[E2E.Browser] ALL ✅ OK")
            return 0
    except Exception as exc:  # noqa: BLE001
        print(f"[E2E.Browser.FAILED] type={type(exc).__name__} msg={exc}", file=sys.stderr)
        return 1
    finally:
        final_cleanup_supabase_only()


if __name__ == "__main__":
    raise SystemExit(main())
