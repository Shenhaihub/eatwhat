# EatWhat Backend（FastAPI + Supabase + AI + B 阶段社区）

> 分层架构：
> **API Router → Service（规则引擎 / RecommendationSession 状态机 / MockAI / DeepSeek Live + 限流预占回滚）→ Supabase（RLS）**

## 0. 交付概览（已完成 P2–P5 路径 A + A/B 冲刺）

| 模块 | 文件 | 关键能力 |
|---|---|---|
| 推荐（单步 + 会话） | `api/v1/recommendations.py` | P2 单步 Top5；P5 session/start → answer（最多 3 轮）→ final 1→3→5 渐进 |
| AI 增益 & 回退 | `services/ai/service.py` + `recommendation_session.py` | A：`generation_mode` 显式（rule / ai）；ai 失败静默回退规则引擎，**9 种失败码细分** |
| AI 日额度（预占-回滚） | `services/ai/rate_limiter.py` | A：双维度（用户/全局）日限流；进程内 TTLCache + 可选 Redis；**预占→成功才扣、失败全退**（保证用户额度不被浪费） |
| 问卷字典 & 推进 | `api/v1/questionnaire.py` + `services/questionnaire_state.py` | 2~3 基础题 + 2~3 自适应题（后端选择下一题） |
| Auth + GDPR | `api/v1/auth.py` | Supabase Magic Link JWT；DELETE /auth/me 删除账号 + 立即吊销 token 再访 401 |
| 历史 + 画像 | `api/v1/history.py` + `api/v1/preferences.py` | RLS；session_id/final_reason 元信息；画像快照 |
| 附近商家（POI） | `api/v1/location.py` + `services/poi_provider.py` | 高德 Key / Mock 双模式；`?food_code=xxx` 直达 |
| 社区（B 阶段） | `api/v1/community.py` | feed / trending / theme + vote / feed + like；**匿名可读，写操作需登录** |
| AI 统计观测 | `api/v1/system.py` + `core/ai_stats.py` | `/system/ai-stats` 脱敏输出 sample_size / 画像使用率 / outcome 分布 |
| 配置 & 安全 | `core/config.py` + `core/encryption.py` | 明文 `sk-*` fail-fast 拦截；AI API Key Fernet + PBKDF2 加密 |
| 统一错误 & 观测 | `core/exceptions.py` + `core/middleware.py` + `core/logging.py` | `{ error: {code, message}, request_id }`；敏感字段统一 redact |

---

## 0. 技术栈速览

| 层 | 选型 | 说明 |
|---|---|---|
| Web 框架 | FastAPI 0.115 + Uvicorn | 原生 async；自动 OpenAPI (`/docs`) |
| 配置 | pydantic-settings `.env` 加载 | [config.py](app/core/config.py) 全部字段有 Literal/validator 强约束 |
| Auth | Supabase Magic Link + JWT 本地校验 | JWKS URL 走 OIDC 标准 `.well-known/jwks.json`，RSA/EC 双算法 |
| 数据层 | Supabase PostgREST SDK + service_role 私有操作 | `user_recommendations` 表 RLS 强制 `auth.uid()=user_id` |
| 规则引擎 | 自研七维匹配（菜系/辣度/预算/人群/份量/场景/营养）→ 归一化打分 Top5 | [rule_engine.py](app/services/rule_engine.py) |
| AI（P5 动态会话）| DeepSeek V4 Flash + MockAIProvider 双实现；**API Key 全链路 Fernet 加密** | [service.py](app/services/ai/service.py) 统一门面 |
| 限流 | 进程内 TTLCache（P5-07）/ 可选 Redis（P5-07B）；双维度日限额 + ContextVar 细分错误码 | [rate_limiter.py](app/services/ai/rate_limiter.py) |
| 日志/观测 | structlog 风格（标准 logging）+ 敏感值统一 redact；request_id 贯穿 | [logging.py](app/core/logging.py) |
| 测试 | pytest + httpx.MockTransport（AI）+ Playwright（UI E2E） | `backend/tests/` |

## 1. 环境准备

### 1.1 依赖安装

```bash
cd backend/
uv sync                 # 安装 pyproject.toml 里所有依赖（含 dev-dependencies：pytest/ruff/playwright 等）
```

### 1.2 环境变量（必做）

```bash
# 1) 复制模板
cp .env.example .env     # Windows:  copy .env.example .env

# 2) 逐项填写：Supabase 必填（History RLS、删除账号必须）
#    其他留空 = 默认走 mock（本地开发不用真钱）
```

`.env.example` 共 6 段：
1. `APP_ENV / APP_MODE`：开发用 `development + mock` 即可。
2. Supabase 6 项：SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY 是最小集，JWKS/JWT 一般可留空自动推断。
3. **AI 4+2 项**：见下方 §2。
4. POI：高德 Key 做餐馆定位；留空走假数据。
5. Redis：多 worker 严格限流用；留空降级 TTLCache。
6. CORS：`FRONTEND_ORIGINS` 默认填写 `http://localhost:5173,http://127.0.0.1:5173`；浏览器规范地址是 `http://localhost:5173/`，两种本机回环写法都可用。

**校验配置是否有效**（不启动服务器）：

```bash
uv run python -c "from app.core.config import Settings; s = Settings(); print('OK', s.app_env, s.ai_provider, 'user_limit=', s.ai_daily_user_limit)"
```

一旦 `AI_API_KEY` 疑似明文（`sk-` 开头），Settings 会在 **启动期抛 ValueError 拒绝启动**。

## 2. 🔐 AI Provider 配置（DeepSeek V4 Flash）

安全原则（P5-03B/P5-09）：**明文 API Key 绝不进 `.env`、绝不进 git、绝不进日志**。
流程：`明文 sk-... → 加密为 ENC:<Fernet> → 粘贴进 .env`，运行期用 `EW_AI_KEY_PASSPHRASE` 解密后**只保留在内存局部变量**。

### 2.1 推荐：一键加密（零配置口令）

```bash
cd backend/
uv run python scripts/encrypt_ai_key.py --auto-generate
# 按提示粘贴 2 次明文 sk-...；脚本会自动生成：
#   · 36 字符强随机 EW_AI_KEY_PASSPHRASE
#   · 16 字节 url-safe 随机 EW_AI_SALT（固定，可留空）
#   · AI_API_KEY=ENC:gAAAAA...  （Fernet 密文）
#   以及 AI_PROVIDER=deepseek / AI_MODEL=deepseek-v4-flash / 限流默认值
# 请把 stdout 的 5~7 行整块复制粘贴进 .env；并把口令+盐存到密码管理器。
```

**口令与盐只会输出这一次，丢了无法解密，请立刻保存。**

### 2.2 可选：自己提供口令

```bash
uv run python scripts/encrypt_ai_key.py
# 流程：输入口令（≥12 字符，两次确认）→ 输入明文 Key（两次确认）→ 输出 ENC:...
```

### 2.3 生产级部署建议（KMS / Secret Manager）

- **本地 dev / 单机部署**：本项目的 `.env` + Fernet 分层（对称加密 + PBKDF2 480,000 轮 SHA256）足够安全。
- **多租户/合规环境**：建议把 `EW_AI_KEY_PASSPHRASE` 与 `EW_AI_SALT` 放在 **云厂商 KMS/Secrets Manager**，启动时注入环境变量；不要写到机器磁盘。
- **CI/CD**：GitHub Actions 中把这两个变量设为 Environment Secrets（不要存在 repo）。

## 3. 开发日常命令

```bash
cd backend/

# Lint（生产门禁，所有 PR 必须通过）
uv run ruff check .            # 检查
uv run ruff check --fix .      # 自动修 import 顺序/简单问题

# 单测（本地，不打真实 Supabase/DeepSeek 网络）
uv run pytest -q                                  # 除需要真实凭据外的全部
uv run pytest tests/test_ai_rate_limiter.py -q    # 只跑 P5-07 限流（并发/换日/双维度交错 8 条）
uv run pytest tests/test_deepseek_provider.py -q  # 只跑 P5-03 DeepSeek HTTP（httpx.MockTransport 13 条）
uv run pytest tests/test_ai_encryption.py -q      # 加密/解密/解密失败模式

# P5-10 完整 HTTP E2E（TestClient，不需要启动服务器）
#   · 默认 mock provider：
uv run python _make_e2e_session.py
#   · 强制 DeepSeek 真调用 + 跑 6 次 user_limit=3（肉眼核对限流命中）：
uv run python _make_e2e_session.py --ai-provider deepseek --n-sessions 6 --user-daily-limit 3
#   · 调试模式（不删除账号，留 Supabase 历史记录方便查）：
uv run python _make_e2e_session.py --skip-delete

# Dev server（热重载）
uv run uvicorn app.main:app --reload --port 8000
# OpenAPI 文档：http://127.0.0.1:8000/docs
```

### E2E 执行的 7 条断言（路径 A）
1. Supabase admin 清理/创建 `e2e-user@example.com`；
2. `POST /recommendations/session/start` 200；
3. 最多 3 轮 follow-up answer，stage=final + 候选严格 5 条 + priority 1..5 递增；
4. `POST /history` 201 写入 `session_id + final_reason`；
5. `GET /history` 读回一致（RLS 只返回当前用户记录）；
6. `DELETE /auth/me` 204 GDPR 删号；
7. **死 token 防线**：删除后再 `POST /history` 必须 401，且 Supabase admin 查询用户不存在。

## 3.1 社区（B 阶段）—— 5 条接口速记

> 全部挂载在 `/api/v1/community/*`；**GET 匿名可读；POST（写操作）必须登录**。
> 内存存储 Mock MVP（重开会重置），P3 会落到 Supabase。

| 方法 | 路径 | 入参（Query/Body） | 典型响应 | 核心约束 |
|---|---|---|---|---|
| GET | `/feed` | `sort=hot \| latest`（默认 latest） | `{sort, items[]}`，item 里 `liked_by_me` 登录才按 user_id 填 | 排序：hot = `likes*2 + comments*3` 倒序；latest = `created_at` 倒序 |
| GET | `/trending` | 无 | `{items[5]}`；字段 `{rank, food_code, cuisine_tag, recommended_today}` | Top 5 固定返回；`recommended_today` 是 mock 稳定值 |
| GET | `/theme` | 无 | `{title, subtitle, ends_at, total_votes?, voted_key, options[{key,label,votes,percent}]}` | 登录态返回 `voted_key`（已投 key / 未投 = null）；匿名固定 null |
| POST | `/theme/vote` | `{ option_key: string }` | `{voted_key, options, duplicated}` | **幂等**：同 key 重复投 → duplicated=true；已投过**不同** key → 409 `ALREADY_VOTED_OTHER` |
| POST | `/feed/{id}/like` | Path `id`（URL-encoded） | `{liked: true, likes: N, duplicated}` | **幂等**：重复点 duplicated=true，likes 数字**不叠加** |

社区接口测试 `tests/test_api_community.py` 共 9 条（必读 + 必写覆盖）：

```powershell
uv run pytest tests/test_api_community.py -q
# 1. feed 默认 latest 10 条时间倒序
# 2. feed hot 按热度降序，登录后 liked_by_me 正确填充
# 3. trending 返回 5 条、rank 1..5 递增
# 4. theme 返回正确结构 & 匿名 voted_key=null
# 5. theme/vote 登录投票 → duplicated/正常双态
# 6. theme/vote 投不同选项 409 ALREADY_VOTED_OTHER
# 7. theme/vote 匿名 401（AUTH_REQUIRED）
# 8. feed/like 正常点赞 + 幂等 duplicated
# 9. feed/like 匿名 401
```

## 4. AI 生成模式 + 失败原因（A 阶段）—— `generation_mode` + `final_reason`

### 4.1 `generation_mode`（A 阶段核心开关）
前端 `/recommendations/session/start` 和 `POST /recommendations` 都传 `prefer_ai_gain: boolean`，后端派生：
- `false` → `generation_mode = "rule"`：**跳过一切 AI 调用**，直接走规则引擎产出 Top5。`final_reason` 恒为 `legacy_rule_engine`，前端显示中性蓝色「规则引擎」徽章，避免用户误解"AI 挂了"。
- `true` → `generation_mode = "ai"`：**先尝试调用 ChatService**；成功 = `ai_gain`；**任何失败** 按 §4.2 细分 → **静默回退规则引擎**。

### 4.2 AI 额度：预占 → 成功才扣、失败全退
（对应 `services/ai/rate_limiter.py` + `service.py`）

```
调用前  reserve(user_id=X, 1)            # 预占：counter += 1
          │ AI 成功 (final_reason=ai_gain)
          ├─ commit：预占保持（真正扣掉）✅
          │ AI 失败（超时/429/schema/鉴权…共 9 类）
          └─ rollback(user_id=X, 1)       # 加回去，用户额度无损 ↩️
```

双维度（用户 / 全局）并行限：任一先到上限 → 返回 `rule_engine_fallback_ai_local_quota`，并且同样走 rollback（不会因为"预占后超限"白扣）。
Redis 是可选的：**留空 REDIS_URL → 自动降级进程内 TTLCache**，单机 MVP 零配置即可。

### 4.3 9 种 `final_reason`（前端 badge 颜色对应）
> 推荐链路永远 fail-open：任何 AI 失败 → 静默切回规则引擎，用户无感。
> 失败原因作为元数据写入 History 表 `recommendation_snapshot._meta.final_reason`，前端显示为来源 chip（颜色 + 摘要）。

| final_reason key | 含义 | 前端 badge 文案 |
|---|---|---|
| `ai_gain` | AI 成功生成并校验通过（真·AI） | 🟢 "AI 生成" |
| `rule_engine_fallback_ai_local_quota` | **本机** user/global 日限额耗尽（P5-07） | 🟡 "规则引擎 · AI 日额度已用" |
| `rule_engine_fallback_ai_remote_quota` | DeepSeek 平台返回 429/503/529（平台限流） | 🟡 "规则引擎 · AI 平台限流" |
| `rule_engine_fallback_ai_unauthorized` | 401/403（API Key 过期/被吊销/未配） | 🟡 "规则引擎 · AI 鉴权失败" |
| `rule_engine_fallback_ai_timeout` | 8s 超时或 httpx.TimeoutException | 🟡 "规则引擎 · AI 响应超时" |
| `rule_engine_fallback_ai_schema` | AI 输出越界（Pydantic validate_json 失败） | 🟡 "规则引擎 · AI 结果不可用" |
| `rule_engine_fallback_ai_build_fail` | Provider 初始化失败（解密失败/密文错） | 🟡 "规则引擎 · AI 未配置" |
| `rule_engine_fallback_ai_fail` | 兜底其他异常 | 🟡 "规则引擎 · AI 回退" |
| `rule_engine_fallback_empty_ai` / `legacy_rule_engine` 等 | P2 老路径或未写元信息 | ⚪ "规则引擎" |

## 5. 目录结构速查

```
backend/
├─ _make_e2e_session.py          # P5-10 / 路径 A 全链路 E2E（TestClient，建议 CI 每周跑）
├─ e2e_browser.py                # Playwright 浏览器 E2E（UI 层）
├─ .env.example                  # 环境变量模板（本文件 §1.2 对应）
├─ pyproject.toml                # 依赖 + ruff/pytest 配置
├─ app/
│   ├─ main.py                   # FastAPI 实例 + 中间件（CORS/RequestID/Redact 日志）
│   ├─ api/v1/
│   │   ├─ auth.py               # Magic Link + JWT 校验 + GDPR DELETE /auth/me
│   │   ├─ history.py            # CRUD + session_id/final_reason 元信息解析
│   │   ├─ recommendations.py    # 推荐：P2 单步 + P5 session/start & answer（generation_mode 分流）
│   │   ├─ questionnaire.py      # 问卷 v1.0 字典 + 下一题推进
│   │   ├─ community.py          # B 阶段：feed / trending / theme + vote / feed + like
│   │   ├─ preferences.py        # 用户画像偏好快照（GET/PUT）
│   │   ├─ location.py           # 附近商家（高德 + Mock POI；?food_code=xxx 直达）
│   │   └─ system.py             # /health/{live,ready} + /system/ai-stats
│   ├─ core/
│   │   ├─ config.py             # Settings；AI_API_KEY 明文检测（fail-fast）
│   │   ├─ encryption.py         # Fernet + PBKDF2 对称加密；AI_API_KEY 解密
│   │   └─ logging.py            # 敏感值统一 Redact
│   ├─ repositories/             # 字典持久化（Supabase + 内存 LRU）
│   ├─ schemas/                  # Pydantic 入参/出参模型（前后端契约）
│   └─ services/
│       ├─ rule_engine.py        # 七维打分 Top5（真源，AI 失败兜底）
│       ├─ recommendation_session.py  # P5 会话状态机（最多 3 轮追问）
│       └─ ai/
│           ├─ base.py           # AIProvider 抽象 + ChatMessage 结构
│           ├─ mock_provider.py  # 本地 MockAIProvider（契约/超时/越界/慢模式）
│           ├─ deepseek_provider.py  # DeepSeek HTTP 客户端（OpenAI 兼容端点）
│           ├─ rate_limiter.py   # P5-07：TTLCache 双维度日限额
│           └─ service.py        # 统一门面：ChatService（ContextVar 细分失败码 + rate limiter）
├─ scripts/
│   └─ encrypt_ai_key.py         # AI API Key 加密工具（交互 + 一键生成双模式）
└─ tests/
    ├─ test_api_community.py     # B：社区 5 条接口 9 用例（feed sort/登录 liked_by_me/trending/theme vote 409/like 幂等…）
    ├─ test_ai_rate_limiter.py   # P5-07 / A：双维度 + 预占回滚（并发/换日/双维度交错 8+）
    ├─ test_recommendation_session.py  # A：generation_mode 分流（rule 直接出 / ai 回退不白扣）
    ├─ test_deepseek_provider.py # P5-03 13 条（httpx.MockTransport）
    ├─ test_ai_encryption.py     # P5-03B 加密/解密/密文错/无口令等
    └─ test_ai_provider.py       # ChatService 整合：Mock 正常/越界/超时回退
```

## 6. 下一步阅读指引

- 新手快速理解链路：先读 [recommendation_session.py](app/services/recommendation_session.py)（P5 状态机），再读 [recommendations.py#L420-L540](app/api/v1/recommendations.py#L420-L540)（session start 路由），最后读 [service.py](app/services/ai/service.py)（AI 门面 + fail-open）。
- 安全/合规重点：[config.py#L94-L108](app/core/config.py#L94-L108)（明文 AI key 拦截 fail-fast）、[encryption.py](app/core/encryption.py)（Fernet 加密）、[auth.py](app/api/v1/auth.py)（DELETE /auth/me GDPR 删除 + RLS 强约束）。
- 前端配套：`frontend/src/pages/Recommend.tsx` 结果态来源 badge + `frontend/src/pages/History.tsx` 卡片头 chip，共享实现 [frontend/src/lib/sourceBadge.ts](frontend/src/lib/sourceBadge.ts)。
