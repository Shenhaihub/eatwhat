# EatWhat — 今天吃什么决策助手

> 先定方向，再找附近。
> **响应式 Web：少量自适应问题（必要时 AI 追问）→ 锁定一种食物类型 → 附近真实商家。**

EatWhat 不做"餐厅列表展示"，而是聚焦在「今天吃什么」这个更上游、更让人纠结的决策上。

## ✨ 功能矩阵

| 模块 | 状态 | 说明 |
|---|---|---|
| 🏠 **首页 Hero + 活动横幅** | ✅ | 主 CTA「开始推荐」+ 社区入口；2 条活动横幅（打卡送 AI 额度 / 主题 PK），localStorage 记 7 天关闭 |
| 🧭 **自适应问卷（规则引擎）** | ✅ | 2~3 基础题 + 2~3 后端自适应题；七维归一化打分 Top5 |
| 🤖 **AI 增益（DeepSeek V4 Flash）** | ✅ | 可选开关，默认关闭=免费规则；开启需登录；最多 3 轮 AI 动态追问，失败静默回退规则引擎 |
| 🔁 **AI 日额度 & 回滚** | ✅ | 双维度日限流（用户/全局），进程内 TTLCache + 可选 Redis；**预占-回滚语义：成功才扣，失败全退** |
| 🎯 **推荐结果页** | ✅ | 1→3→5 渐进展示；来源 chip（AI / 规则 / 9 种细分兜底语义）；一键「查附近商家」 |
| 🏪 **附近商家（/nearby）** | ✅ | 支持 `?food_code=xxx` 直达；高德 POI / Mock 双模式；预算软偏好 |
| 👤 **账号 & 画像** | ✅ | Supabase Magic Link 无密码登录；偏好画像 + 推荐历史；GDPR 删除账号 |
| 🎪 **社区（/community）** | ✅ | 本周主题 PK（投票/进度条/锚点）；Feed 卡片（🔥最热 / ⏰最新 + 点赞）；今日 Top5 榜（金银铜徽章） |
| 📜 **历史 & 设置** | ✅ | /settings?tab=preference 锚点直达；偏好时间轴 |
| ♿ **可访问性** | ✅ | 语义化标签、ARIA 角色、键盘 Tab 导航、跳过链接、reduced-motion、320px+ 响应式 |
| 🧪 **测试** | ✅ | 后端 303 pytest + ruff + mypy；前端 29 vitest + tsc + oxlint |
| 🚢 **Docker 部署** | ✅ | 多阶段构建 backend/frontend + docker-compose + healthcheck；一行命令启动 |

## 🏗️ 架构总览

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                        浏览器（React 19 + Vite + TypeScript）                  │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐  │
│  │     Home     │  │  Recommend   │  │  Community   │  │  Nearby/Settings │  │
│  │+CampaignBanner│ │ (1→3→5 expand)│ │Theme+Feed+Top│ │  History/Login   │  │
│  └────────┬─────┘  └──────┬───────┘  └──────┬───────┘  └────────┬─────────┘  │
│           │               │                  │                   │            │
│           ▼               ▼                  ▼                   ▼            │
│          services/api/client.ts（统一错误体 + auth JWT + 相对路径 /api/v1）    │
└─────────────────────────────┬────────────────────────────────────────────────┘
                              │ /api/v1/*（nginx 反代）
                              ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│                     FastAPI（Python 3.13 · Uvicorn）                          │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐  │
│  │recommendations│ │  community   │ │auth/history  │ │location/system/AI│  │
│  │+session state│ │feed/trending │ │Magic Link    │ │POI/JWKS/AI stats │  │
│  │+AI follow_up │ │vote/like     │ │GDPR delete   │ │                  │  │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘  └────────┬─────────┘  │
│         │                 │                  │                   │            │
│         ▼                 ▼                  ▼                   ▼            │
│  ┌────────────────── Rule Engine（7 维打分 Top5）─────────────────────────┐   │
│  │    Session 状态机：follow_up × 3 → final，AI 答案回写七维字段            │   │
│  │    generation_mode=rule → 规则直出                                      │   │
│  │    generation_mode=ai   → ChatService(DeepSeek) 失败→规则兜底（9 种码）  │   │
│  └────────────────────────────────────────────────────────────────────────┘   │
│                                    │                                           │
│               ┌────────────────────┼─────────────────────┐                   │
│               ▼                    ▼                     ▼                   │
│      Supabase Postgres        Redis（可选）         高德 / DeepSeek          │
│      RLS 强制 auth.uid()     多 worker 限流        Provider 抽象可替换       │
└──────────────────────────────────────────────────────────────────────────────┘
```

## 📁 目录结构

```text
project0717/
├── backend/                    # FastAPI 后端
│   ├── app/
│   │   ├── api/v1/             # 路由（recommendations / community / auth / history / ...）
│   │   ├── core/               # config / Fernet 加密 / 日志脱敏 / middleware
│   │   ├── services/           # rule_engine / recommendation_session / ai/
│   │   ├── repositories/       # 食物字典加载
│   │   ├── schemas/            # Pydantic 契约
│   │   └── data/               # food_dictionary / question_bank / demo_locations
│   ├── scripts/encrypt_ai_key.py  # 🔐 AI Key Fernet 加密工具
│   ├── tests/                  # pytest（303 条）
│   ├── Dockerfile              # 多阶段构建（base→builder→runtime）
│   └── .env.example            # 后端环境变量模板
├── frontend/                   # React 19 + Vite + TypeScript
│   ├── src/
│   │   ├── pages/              # Home / Recommend / Community / Nearby / History / Settings / Login
│   │   ├── components/         # CampaignBanner / layout / profile
│   │   ├── context/AuthContext.tsx
│   │   ├── services/api/       # client.ts + types/*
│   │   ├── lib/                # foodNames / supabase 初始化
│   │   └── styles/             # design tokens / global CSS
│   ├── nginx.conf              # SPA fallback + /api 反代 + gzip
│   ├── Dockerfile              # 多阶段构建（node→nginx）
│   └── .env.example            # 前端开发环境变量模板
├── docs/                       # 25 份设计与契约文档（PRD、API、AI、ROADMAP…）
├── docker-compose.yml          # 前后端 + 可选 Redis 编排
├── .env.example                # 根环境变量模板
└── start-dev.bat               # Windows 一键启动（dev 模式）
```

## 🚀 快速开始

### 方式一：Docker（推荐，3 命令启动全栈）

前置：[Docker Desktop](https://www.docker.com/products/docker-desktop/) 已启动。

```powershell
# 1. 克隆项目
git clone <your-repo-url>
cd project0717

# 2. 启动（首次构建约 3-5 分钟，后续秒级启动）
docker compose up -d --build

# 3. 访问
# 前端：http://localhost:8080
# 后端健康检查：http://localhost:8000/health/live
# API 文档：http://localhost:8000/docs

# 停止
docker compose down
```

默认以 **Mock 模式**运行（不需要任何 API Key），规则引擎推荐 + 内存社区数据，开箱即用。

如需启用 Supabase 登录/历史/DeepSeek AI/高德 POI：
1. 复制 `backend/.env.example` 为 `backend/.env` 并填写对应 Key
2. AI Key 必须先用加密工具：`cd backend && uv run python scripts/encrypt_ai_key.py --auto-generate`
3. 重启：`docker compose up -d`

### 方式二：本地开发模式

前置：Node.js 24 LTS · Python 3.13 · [uv](https://docs.astral.sh/uv/)

**后端**：
```powershell
cd backend
copy .env.example .env      # 首次；不填任何 Key = 全 Mock
uv sync
uv run uvicorn app.main:app --reload --port 8000
# 验证：http://127.0.0.1:8000/health/live → {"status":"ok"}
```

**前端**：
```powershell
cd frontend
copy .env.example .env.local   # 首次
npm install
npm run dev                    # http://localhost:5173/
```

Vite dev server 已配置 `/api/*` 代理到 `http://127.0.0.1:8000`，无需跨域配置。

**Windows 一键启动**：双击 `start-dev.bat`。

## 🧪 质量门禁

```powershell
# 后端
cd backend
uv run ruff check .            # lint（0 错误）
uv run mypy app                # 类型检查（0 错误）
uv run pytest -q               # 303 条单测

# 前端
cd frontend
npm run lint                   # oxlint
npm run typecheck              # tsc -b
npm run test                   # vitest（29 条）
npm run build                  # vite build
```

## 📚 文档索引

| 文档 | 作用 |
|---|---|
| `docs/EatWhat_实施计划与状态.md` | 实时进度：47/50 完成，P8 发布阶段 |
| `docs/01_EatWhat_PRD_产品需求文档_v1.2_权威需求基线.md` | 产品权威基线 |
| `docs/05_EatWhat_系统架构设计.md` | 模块边界、数据流、认证、RLS |
| `docs/07_EatWhat_API接口设计.md` | REST 契约 |
| `docs/08_EatWhat_AI推荐系统设计.md` | generation_mode / final_reason / 9 种失败码 |
| `docs/09_EatWhat_隐私安全与免责声明.md` | GDPR 删除、坐标不写历史、AI 不编商家 |

## ⚠️ 已知限制（诚实声明）

- 预算档位是「食物方向的软偏好」，**不承诺附近商家价格准确**（高德 POI 价格字段本身不完整）。
- 社区 feed 为后端内存种子数据，重启会重置；发布动态功能在后续迭代。
- AI 仅推荐「食物类型」，**绝不编造不存在的商家**（POI 查询独立步骤）。
- Docker 部署提供 compose 骨架；生产环境的 Ingress / HTTPS 证书 / CI/CD 按实际环境补充。

## 🔒 安全红线

- **AI Key 明文禁止入库**：必须通过 `scripts/encrypt_ai_key.py` 生成 `ENC:` 密文
- **Supabase service_role key 仅后端使用**：绝不暴露到前端或日志
- **`.env` 文件全部在 `.gitignore` 中**：只有 `.env.example` 模板入库
- **CORS 白名单**：默认只允许 localhost；生产部署需配置 `FRONTEND_ORIGINS`
