# EatWhat — 今天吃什么决策助手

> 先定方向，再找附近。
> **响应式 Web：少量自适应问题（必要时 AI 追问）→ 锁定一种食物类型 → 附近真实商家。**

EatWhat 不做"餐厅列表展示"，而是聚焦在「今天吃什么」这个更上游、更让人纠结的决策上。

## 1. 功能矩阵（已完成 A / B 两个冲刺）

| 模块 | 状态 | 说明 |
|---|---|---|
| 🏠 **首页 Hero + 活动横幅** | ✅ 已交付（B3-2） | 主 CTA「开始推荐」+ 到社区入口；首页顶部 2 条活动横幅（打卡送 AI 额度 / 主题 PK），localStorage 记 7 天关闭 |
| 🧭 **自适应问卷（规则引擎）** | ✅ 已交付（P2 / A） | 2~3 基础题 + 2~3 后端自适应题；七维（菜系/辣度/预算/人群/份量/场景/营养）归一化打分 Top5 |
| 🤖 **AI 增益（DeepSeek）** | ✅ 已交付（P5 / A） | 可选开关，默认关闭=免费规则；开启需登录；最多 3 轮 AI 追问，失败静默回退规则引擎 |
| 🔁 **AI 日额度 & 回滚** | ✅ 已交付（A） | 双维度日限流（用户/全局），进程内 TTLCache + 可选 Redis；**预占-回滚语义：成功才扣，失败全退** |
| 🎯 **推荐结果页** | ✅ 已交付（A） | 1→3→5 渐进展示；来源 chip（AI / 规则 / 规则兜底 9 种细分语义）；一键「查附近商家」 |
| 🏪 **附近商家（/nearby）** | ✅ 已交付（P4） | 支持 `?food_code=xxx` 直达；高德 POI / Mock 双模式；预算为软偏好，不承诺商家价格 |
| 👤 **账号 & 画像** | ✅ 已交付（P4） | Supabase Magic Link 无密码登录；偏好画像 + 推荐历史；GDPR 删除账号（含历史 + token 失效） |
| 🎪 **社区（/community）** | ✅ 已交付（B） | ① 本周主题 PK（投票/进度条/截止时间/锚点跳转）；② Feed 卡片（🔥最热 / ⏰最新 tab + 点赞乐观更新）；③ 今日推荐 Top5 榜（金银铜徽章 → 去吃 → /nearby）；④ 右下悬浮「分享今天吃了啥」FAB（P3 再做发布） |
| 📜 **历史 & 设置** | ✅ 已交付（A 优化） | /settings?tab=preference 锚点直达；「查看偏好时间轴」按钮已修复（React Router useNavigate + query params） |
| 🧪 **测试** | ✅ 已交付 | 后端 pytest 40+；前端 vitest 27 + typecheck + oxlint；路径 A 全链路 E2E 脚本 |
| 🚢 **部署骨架（Docker）** | 🚧 C2 | backend/frontend Dockerfile + docker-compose（本阶段交付） |
| 🧭 **E2E 冒烟闭环** | 🚧 C3 | 社区 + 横幅 + Top 榜三条关键路径（本阶段交付） |

## 2. 架构总览

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                           浏览器（React 19 + Vite 8）                         │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐  │
│  │     Home     │  │  Recommend   │  │  Community   │  │  Nearby/Settings │  │
│  │+CampaignBanner│ │ (1→3→5 expand)│ │Theme+Feed+Top│ │  History/Login   │  │
│  └────────┬─────┘  └──────┬───────┘  └──────┬───────┘  └────────┬─────────┘  │
│           │               │                  │                   │            │
│           ▼               ▼                  ▼                   ▼            │
│          services/api/client.ts（统一错误体 error.code/request_id + auth JWT）│
└─────────────────────────────┬────────────────────────────────────────────────┘
                              │ /api/v1/*
                              ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│                        FastAPI（Python 3.13 · Uvicorn）                        │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐  │
│  │recommendations│ │  community   │  │auth/history  │  │location/system/AI│  │
│  │+session start │ │feed/trending │ │Magic Link    │ │POI/JWKS/AI stats │  │
│  │+answer + AI  │ │theme/vote/like│ │GDPR delete   │ │                  │  │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘  └────────┬─────────┘  │
│         │                 │                  │                   │            │
│         ▼                 ▼                  ▼                   ▼            │
│  ┌───────────────── Rule Engine (7维 打分 Top5) ──────────────────────────┐  │
│  │                           ┌──────────────────┐                           │  │
│  │                           │ Recommendation   │  状态机：follow_up×3 →final │  │
│  │                           │ Session          │                           │  │
│  │                           └────────┬─────────┘                           │  │
│  │       generation_mode=rule 直接生成  │  generation_mode=ai 先尝试 ChatSvc │  │
│  │                                    ↓                                     │  │
│  │    ChatService(DeepSeek V4 Flash / Mock) + Rate Limiter(预占→回滚)      │  │
│  │    失败分类 9 种 → final_reason → 前端来源 chip                          │  │
│  └──────────────────────────────────────────────────────────────────────────┘  │
│                                    │                                           │
│               ┌────────────────────┼─────────────────────┐                   │
│               ▼                    ▼                     ▼                   │
│         Supabase Postgres      Redis（可选）         高德 / DeepSeek          │
│         RLS 强制 auth.uid()   多 worker AI 限流     Provider 抽象可替换      │
└──────────────────────────────────────────────────────────────────────────────┘
```

## 3. 目录结构

```text
project0717/
├── backend/                    # FastAPI 后端
│   ├── app/
│   │   ├── api/v1/             # 路由（recommendations / community / auth / history / ...）
│   │   ├── core/               # config / 加密 / 日志脱敏 / middleware
│   │   ├── services/           # rule_engine / recommendation_session / ai/
│   │   ├── repositories/       # 字典加载
│   │   ├── schemas/            # Pydantic 契约
│   │   └── data/               # food_dictionary / question_bank / demo_locations
│   ├── scripts/encrypt_ai_key.py  # 🔐 AI Key Fernet 加密工具
│   ├── tests/                  # pytest（40+）
│   ├── _make_e2e_session.py    # 路径 A HTTP E2E（不启动服务器）
│   └── e2e_browser.py          # Playwright UI E2E
├── frontend/                   # React 19 + Vite 8 + TypeScript 6
│   ├── src/
│   │   ├── pages/              # Home / Recommend / Community / Nearby / History / Settings / Login
│   │   ├── components/         # CampaignBanner / layout(Header+MobileNav+AppShell) / profile
│   │   ├── context/AuthContext.tsx  # Supabase Auth + 注入 JWT
│   │   ├── services/api/       # client.ts + types/* （后端契约镜像）
│   │   ├── lib/                # sourceBadge / supabase 初始化
│   │   └── styles/             # tokens / global / recommendations / nearby
│   └── tests 由 vitest 管：*.test.ts(x) 散布在源码旁
├── docs/                       # 24 份设计与契约文档（PRD v1.2、API、DB、AI、ROADMAP…）
├── prototype/                  # P0 可点击低保真原型（静态）
├── .env.example                # 环境变量模板（AI Key 只放 ENC: 密文示例，绝不明文）
├── start-dev.bat               # Windows 一键启动前后端（dev）
└── docker-compose.yml          # 🚧 C2：本阶段交付 → 前后端 + 可选 Redis
```

## 4. 本地启动（3 分钟跑通 MVP）

**前置**：Node.js 24 LTS · Python 3.13 · [uv](https://docs.astral.sh/uv/)（可选 Docker，见 C2）。

### Windows 一键启动

```powershell
# 在项目根目录直接双击或执行：
.\start-dev.bat
```

### 手动：后端（默认 Mock，不用任何 Key 即可）

```powershell
cd backend
copy .env.example .env      # 首次；不填任何 Key = 全 Mock
uv sync                     # 安装依赖（含 pytest 等 dev 包）
uv run uvicorn app.main:app --reload --port 8000
# 验证：
#   http://127.0.0.1:8000/health/live       →  {"status":"ok"}
#   http://127.0.0.1:8000/docs              →  Swagger UI（所有已注册接口）
#   http://127.0.0.1:8000/api/v1/community/feed?sort=hot   →  B 社区 mock 数据 10 条
```

### 手动：前端（Vite 反代 `/api` → 8000）

```powershell
cd frontend
npm install        # 或 npm ci （如果有锁文件）
npm run dev        # http://localhost:5173/
```

> Vite 的 `server.proxy` 默认已把 `/api/*` 转发到 `http://127.0.0.1:8000`，**不用跨域配置**。浏览器地址以 `http://localhost:5173/` 为规范写法；后端 CORS 同时允许 `localhost` 与 `127.0.0.1`。

### 打开 Supabase 认证 + History（可选）

1. 到 [Supabase Dashboard](https://supabase.com/dashboard) 创建免费项目。
2. 把下面 4 项填进 `backend/.env`：
   - `SUPABASE_URL`
   - `SUPABASE_ANON_KEY`
   - `SUPABASE_SERVICE_ROLE_KEY`
   - `SUPABASE_JWT_SECRET`
3. 前端 `frontend/.env.local` 里写：
   ```
   VITE_SUPABASE_URL=...
   VITE_SUPABASE_ANON_KEY=...
   ```
4. 重跑后端，`/health/ready` 会从 `not_configured` 变成 `ready`。

### 打开 DeepSeek AI 增益（可选，A 阶段）

**明文 Key 禁止直接写 `.env`。** 用加密工具：

```powershell
cd backend
uv run python scripts/encrypt_ai_key.py --auto-generate
# ↓ 粘贴两次明文 sk-xxxx，脚本输出：
# EW_AI_KEY_PASSPHRASE=...
# EW_AI_SALT=...
# AI_API_KEY=ENC:gAAAAA...
# AI_PROVIDER=deepseek
# AI_MODEL=deepseek-v4-flash
```

把 5 行全部复制进 `backend/.env`，重启后端即可。前端在「开始推荐」页勾上「使用 AI 优化推荐」（默认关），就会用 AI。

**失败扣额？不会。** AI 额度使用 **预占-回滚语义**：
- 预占：调用前先占 1 次额度；
- 成功：保持扣减（`final_reason=ai_gain`）；
- 任何失败（超时/鉴权/429/schema 校验失败…）：**立即把预占的 1 次加回去**，用户额度不受损。

## 5. 质量门禁（本地必跑）

```powershell
# 后端
cd backend
uv run pytest -q                                       # 单测（40+ 条，AI HTTP 走 MockTransport）
uv run python _make_e2e_session.py --skip-delete       # 路径 A E2E（不删除账号，留历史）

# 前端
cd frontend
npm run typecheck    # tsc -b（禁止 any 逃逸）
npm run lint         # oxlint 103 规则
npm run test         # vitest run（27 条）
npm run build        # vite build → dist/
```

## 6. 文档索引

| 文档 | 作用 |
|---|---|
| `docs/01_EatWhat_PRD_产品需求文档_v1.2_权威需求基线.md` | 产品权威基线 |
| `docs/00_EatWhat_统一名词表与状态定义_v1.0.md` | 所有术语/状态的统一叫法（前后端必读） |
| `docs/05_EatWhat_系统架构设计.md` | 模块边界、数据流、认证、RLS |
| `docs/07_EatWhat_API接口设计.md` | REST 契约（B 阶段 community 接口） |
| `docs/08_EatWhat_AI推荐系统设计.md` | generation_mode / final_reason / 9 种失败码 |
| `docs/09_EatWhat_隐私安全与免责声明.md` | GDPR 删除、POI 坐标不写历史、AI 绝不编商家 |
| `docs/12_EatWhat_ROADMAP.md` | P0–P8 阶段规划 |
| `backend/README.md` / `frontend/README.md` | 前后端各自的目录、命令、细节 |

## 7. 已知限制（诚实声明）

- 预算档位是「食物方向的软偏好」，**不承诺附近商家价格准确**（高德 POI 价格字段本身不完整）。
- 社区当前为内存 mock 数据，重启后端会重置；分享动态（FAB 弹层里的功能）在 P3。
- AI 仅推荐「食物类型」，**绝不编造不存在的商家**（POI 查询是独立步骤，可单独 Mock/Live）。
- 部署骨架本阶段（C2）提供 Dockerfile + compose，Ingress / HTTPS 证书 / CI 发布按你实际环境补。

## 8. 下一步

继续 C 阶段：**C1 → C2（Docker 化）→ C3（E2E 冒烟 3 条关键路径）**。
运行方式：在项目根目录 `docker compose up -d`（或 Windows 下 `-f` 绝对路径，见 `docker-compose.yml` 注释）。
