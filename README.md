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

## ☁️ 公有云部署（公开站点）

> 项目默认以 Mock 模式运行（零密钥、零成本）。要发布成公开站点，需接入真实第三方 key + 一个公网托管。以下对比两种最省钱的路径。

### 方案对比

| 维度 | A. Fly.io 托管（推荐，先验证） | B. 国内云服务器 + Docker Compose |
|---|---|---|
| 前置 | Fly 账号 + 信用卡 + flyctl | 服务器 + 域名 + **ICP 备案**（1-3 周） |
| 上线速度 | 当天 | 备案后 1 天 |
| 成本 | 约 **$5-8/月（≈¥38-60）** | 首年 ¥99/年，续费 ¥300-600/年 |
| HTTPS | 自动 | 需配 certbot/Caddy |
| 运维 | 托管 | 自行监控/备份 |
| 大陆可达性 | 需实测（新加坡/东京节点偏慢） | 最快最稳 |
| Supabase | 海外调用稳定，大陆用户登录可能慢 | 大陆连 Supabase 更不稳（需评估迁移） |

> **结论**：先选 **Fly.io（新加坡 `sin` / 东京 `nrt` 节点）** 免备案快速上线，用 fly.dev 子域名或自定义域名做小流量验证；若大陆用户登录太慢再迁国内服务器。

### Render 免费部署（零成本首选，用户已选定）

> 无需绑卡（或仅预授权 $1），**总成本 ¥0/月**。后端 Web Service 512MB/0.1CPU 闲置 15 分钟休眠、冷启动 30-60s（适合"先上线验证"）；前端静态站点免费且不数实例小时。

```powershell
# 0. 把仓库推上 GitHub 后（本项目已是 Public）
# 1. 登录 render.com → New → Blueprint → 连接本 GitHub 仓库，Render 读取 render.yaml 自动创建两个服务
#      · eatwhat-backend (Web Service, Docker, Free)
#      · eatwhat-frontend (Static Site, Free, SPA fallback)

# 2. 首次部署后，进入 eatwhat-backend → Environment 手动填敏感值（不入 Git）：
#      SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY / AI_API_KEY(ENC:...) /
#      EW_AI_KEY_PASSPHRASE / AMAP_API_KEY / REDIS_URL(可选)
#    然后 Manual Deploy 重建一次

# 3. 后端公网地址形如 https://eatwhat-backend.onrender.com
#    前端域名形如 https://eatwhat-frontend.onrender.com（已由 render.yaml 注入 VITE_API_BASE_URL 并配好 CORS）

# 4. Supabase 控制台 → Authentication → URL Configuration
#    Redirect URLs 加入 https://eatwhat-frontend.onrender.com/**（否则 Magic Link 登录回调失败）
```

**关键适配点（本项目已内置）**：
- 后端 `backend/Dockerfile` 已支持 `$PORT`（Render 注入的端口），本地无 `$PORT` 仍用 8000。
- 前端用 `VITE_API_BASE_URL` 直连后端公网（跨域），`render.yaml` 里已把 `FRONTEND_ORIGINS` + `VITE_API_BASE_URL` 配对成 `*.onrender.com` 域名。
- **免费层注意**：闲置 15 分钟会休眠，首次访问冷启动 30-60s；免费 Postgres 30 天过期（本项目用 Supabase，不受影响）。
- 想更稳定（不休眠）可将后端升到 Basic（$6/月）或改用 Fly.io 小实例。

### Fly.io 部署步骤（备选）

```powershell
# 0. 安装 flyctl 并登录
npm i -g @fly.io/ctl 或 curl -L https://fly.io/install.sh | sh
fly auth signup   # 或 fly auth login

# 1. 后端（先部署，因为前端要引用它的地址）
cd backend
fly launch --no-deploy        # 生成 fly.toml（按仓库内模板核对关键字段）
fly secrets set SUPABASE_URL=... SUPABASE_SERVICE_ROLE_KEY=... \
               AI_API_KEY=ENC:... EW_AI_KEY_PASSPHRASE=... AMAP_API_KEY=... \
               REDIS_URL=...
fly deploy
# 记下后端域名，例如 https://eatwhat-backend.fly.dev

# 2. 前端（构建时注入后端公网地址）
cd ../frontend
# 把 frontend/fly.toml 里 [build.args] VITE_API_BASE_URL 改成你的后端域名
fly launch --no-deploy
fly deploy
# 记下前端域名，例如 https://eatwhat-frontend.fly.dev

# 3. 把前端域名加进后端 CORS（Fly 后端是 secrets/env，二选一）
#    FRONTEND_ORIGINS=https://eatwhat-frontend.fly.dev,http://localhost:8080

# 4. Supabase 控制台 → Authentication → URL Configuration
#    Site URL / Redirect URLs 加入前端 fly.dev 域名，否则 Magic Link 登录回调失败
```

**关键适配点（务必阅读）**：
- **nginx 反代失效**：Fly 上 frontend 的 nginx 无法解析 docker-compose 的服务名 `backend:8000`。本仓库默认采用「前端构建期注入 `VITE_API_BASE_URL` 直连后端公网 + 后端 CORS 放行前端域名」，**无需改源码**。
- **CORS**：后端 `FRONTEND_ORIGINS` 必须包含前端 Fly 域名，且前端 fly.toml 的 `VITE_API_BASE_URL` 必须指向后端 `api/v1`。
- **Redis**：公开多地/多实例时强烈建议 `fly secrets set REDIS_URL=...`，否则全局 AI 日限流会退化为进程内计数。
- **云主机部署（备选，B 方案）**：直接把 `docker-compose.yml` 搬到云服务器，前端把 `backend:8000` 改为后端容器服务名即可；大陆节点约省 60% 成本但需备案。

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
