# EatWhat MVP 开发计划

> 2026-08-03 P0-07 勘误：本文 M0–M12 里程碑结构已被实施计划 P0–P8 取代；与权威口径冲突处以《22_EatWhat_P0-07_技术文档收敛清单_v1.0》为准（5 候选 1→3→5、/questionnaire/next、逐题追问、阈值配置化等）。  
>
> 文档状态：第四阶段正式交付物  
> 产品版本：v1.0 MVP  
> 文档日期：2026-07-21  
> 面向对象：首次完整开发前后端项目的初学者  
> 开发平台：Windows  
> 开发策略：先 Mock 闭环，后接真实服务；小步提交，逐阶段验收

---

# 1. 文档目的

本文档把已经完成的产品、流程、架构、数据库和 API 设计，转换为可执行的开发任务。

它重点回答：

1. 最终使用什么技术；
2. Windows 需要安装什么；
3. 仓库如何初始化；
4. 第一行代码从哪里开始；
5. 前端和后端按什么顺序开发；
6. Supabase、AI、高德什么时候接入；
7. 每个里程碑交付什么；
8. 每个任务如何交给 Codex；
9. 每一步如何验收；
10. 什么时候可以发布 v1.0.0。

---

# 2. 开发总原则

## 2.1 不一次开发全部功能

错误方式：

```text
先搭全部页面
+ 同时接Supabase
+ 同时建数据库
+ 同时接AI
+ 同时接高德
+ 同时部署
```

这样一旦出错，很难判断问题来自哪一层。

正确方式：

```text
先做一条最小可运行闭环
→ 再增加账户
→ 再增加数据库
→ 再增加定位和地图
→ 再增加AI
→ 再增加公共模块
→ 最后完善测试和部署
```

## 2.2 每个里程碑都必须可运行

任何阶段结束时，都必须能：

- 启动；
- 展示；
- 操作；
- 测试；
- 提交到 GitHub。

不允许长时间处于“写了很多代码但整体不能运行”的状态。

## 2.3 Mock 优先

开发初期默认：

```text
AI_PROVIDER=mock
POI_PROVIDER=mock
```

真实 Key 最后接入。

## 2.4 后端先定义合同，前端再消费

接口先通过：

- Pydantic Schema；
- OpenAPI；
- Mock响应；

明确数据结构，再开发页面。

## 2.5 一次只让 Codex完成一个清晰任务

推荐任务规模：

- 一个页面；
- 一个 API；
- 一个数据库迁移；
- 一个 Service；
- 一组测试。

不推荐一次要求：

> 把整个EatWhat项目全部写完。

---

# 3. 最终技术栈

## 3.1 前端

| 技术 | 选择 | 用途 |
|---|---|---|
| Node.js | 24 LTS | 前端运行环境 |
| npm | 随 Node 安装 | 包管理 |
| React | 19 最新安全补丁版本 | 页面框架 |
| TypeScript | 当前稳定版 | 类型安全 |
| Vite | 8.x | 构建和开发服务器 |
| React Router | 当前稳定版 | 页面路由 |
| TanStack Query | 当前稳定版 | 服务端数据状态 |
| Supabase JS | 当前稳定版 | 注册登录和会话 |
| React Hook Form | 当前稳定版 | 表单 |
| Zod | 当前稳定版 | 前端输入校验 |
| Vitest | 当前稳定版 | 单元测试 |
| React Testing Library | 当前稳定版 | 组件测试 |
| Playwright | 当前稳定版 | 端到端测试 |
| ESLint | 当前稳定版 | 代码检查 |
| Prettier | 当前稳定版 | 格式化 |

## 3.2 后端

| 技术 | 选择 | 用途 |
|---|---|---|
| Python | 3.13.x | 后端运行环境 |
| uv | 当前稳定版 | 环境、依赖和锁文件 |
| FastAPI | 当前稳定版 | Web API |
| Uvicorn | 当前稳定版 | ASGI服务 |
| Pydantic | v2系列 | 数据校验 |
| SQLAlchemy | v2系列 | ORM与数据库 |
| Alembic | 当前稳定版 | 数据库迁移 |
| psycopg | v3系列 | PostgreSQL驱动 |
| HTTPX | 当前稳定版 | AI和地图请求 |
| PyJWT | 当前稳定版 | JWT验证 |
| pytest | 当前稳定版 | 测试 |
| pytest-asyncio | 当前稳定版 | 异步测试 |
| Ruff | 当前稳定版 | 格式化和Lint |
| mypy | 当前稳定版 | 静态类型检查 |

## 3.3 平台与服务

| 服务 | 用途 |
|---|---|
| Supabase Auth | 邮箱密码账户 |
| Supabase PostgreSQL | 业务数据库 |
| 高德开放平台 | 地点和POI |
| 可替换AI Provider | 补问和食物推荐 |
| GitHub | 代码、Issue、PR、Actions |
| 前端托管平台 | 静态前端 |
| Python后端托管平台 | FastAPI |
| 错误监控（可选） | 生产异常 |

## 3.4 为什么使用 Python 3.13 而不是追最新预览版

开发计划优先：

- 生态兼容；
- 文档成熟；
- 依赖稳定；
- 部署平台支持。

项目创建时可重新检查 FastAPI、SQLAlchemy和部署平台对 Python 版本的支持，但不要使用 Beta 或 RC 版本。

## 3.5 为什么使用 Node 24 LTS

- 当前属于 LTS；
- 与当前 Vite 要求兼容；
- 比 Current 分支更适合长期项目；
- 安装和CI环境稳定。

---

# 4. Windows 开发工具清单

## 4.1 必须安装

- Git；
- GitHub账号；
- VS Code；
- Node.js 24 LTS；
- Python 3.13，或让 uv 管理Python；
- uv；
- Docker Desktop；
- 浏览器；
- GitHub Desktop（可选）。

## 4.2 VS Code扩展

建议：

- Python；
- Pylance；
- Ruff；
- ESLint；
- Prettier；
- Docker；
- GitLens（可选）；
- REST Client（可选）；
- PostgreSQL客户端（可选）。

## 4.3 Docker用途

主要用于：

- 本地Supabase；
- 后续容器化后端；
- 统一测试环境。

第一条垂直切片不强制立即启动本地Supabase。

---

# 5. 仓库形式

## 5.1 单仓库 Monorepo

建议：

```text
eatwhat/
├── frontend/
├── backend/
├── supabase/
├── docs/
├── scripts/
├── .github/
├── .env.example
├── docker-compose.yml
├── README.md
├── LICENSE
└── Makefile或Windows脚本
```

## 5.2 为什么单仓库

- 一个人维护更简单；
- 前后端版本同步；
- 一个PR可完成一条完整功能；
- GitHub展示更完整；
- CI集中管理。

## 5.3 文档目录

```text
docs/
├── 00_项目总体规划与进度跟踪.md
├── 01_PRD.md
├── 02_用户流程.md
├── 03_信息架构.md
├── 04_文字原型.md
├── 05_系统架构.md
├── 06_数据库设计.md
├── 07_API设计.md
├── 08_AI推荐设计.md
├── 09_隐私安全.md
├── 10_MVP开发计划.md
├── 11_测试与验收计划.md
├── 12_ROADMAP.md
└── 13_GitHub协作规范.md
```

---

# 6. 建议目录结构

## 6.1 前端

```text
frontend/
├── public/
├── src/
│   ├── app/
│   │   ├── router.tsx
│   │   ├── providers.tsx
│   │   └── App.tsx
│   ├── pages/
│   │   ├── auth/
│   │   ├── home/
│   │   ├── questionnaire/
│   │   ├── recommendation/
│   │   ├── location/
│   │   ├── restaurants/
│   │   ├── community/
│   │   ├── history/
│   │   └── settings/
│   ├── components/
│   ├── services/
│   ├── hooks/
│   ├── stores/
│   ├── schemas/
│   ├── types/
│   ├── utils/
│   ├── mocks/
│   └── test/
├── package.json
├── package-lock.json
├── vite.config.ts
├── tsconfig.json
└── .env.example
```

## 6.2 后端

```text
backend/
├── app/
│   ├── main.py
│   ├── api/v1/
│   ├── core/
│   ├── schemas/
│   ├── models/
│   ├── repositories/
│   ├── services/
│   ├── providers/
│   ├── db/
│   └── data/
├── tests/
├── alembic/
├── pyproject.toml
├── uv.lock
├── alembic.ini
└── .env.example
```

## 6.3 Supabase

```text
supabase/
├── config.toml
├── migrations/
├── seed.sql
└── tests/
```

即使 SQLAlchemy/Alembic 是主要迁移工具，也可保留Supabase项目配置。最终只选择一个“数据库结构真相来源”，不能同时手工维护两套冲突迁移。

建议本项目以：

```text
Alembic + SQLAlchemy Model
```

为主，Supabase目录保存本地开发配置和必要的Auth触发器SQL。

---

# 7. 环境变量设计

## 7.1 根目录 `.env.example`

只展示变量名和示例，不放真实Key。

## 7.2 前端

```env
VITE_API_BASE_URL=http://localhost:8000/api/v1
VITE_SUPABASE_URL=
VITE_SUPABASE_PUBLISHABLE_KEY=
VITE_APP_MODE=mock
```

只能放可公开的前端配置。

## 7.3 后端

```env
APP_ENV=development
APP_MODE=mock
DATABASE_URL=
SUPABASE_URL=
SUPABASE_JWKS_URL=
SUPABASE_JWT_ISSUER=
SUPABASE_JWT_AUDIENCE=
AI_PROVIDER=mock
AI_API_KEY=
AI_MODEL=
POI_PROVIDER=mock
AMAP_API_KEY=
AI_DAILY_USER_LIMIT=3
AI_GLOBAL_DAILY_LIMIT=100
POI_CACHE_TTL_SECONDS=1200
FRONTEND_ORIGINS=http://localhost:5173
```

## 7.4 禁止上传

```text
.env
.env.local
真实数据库连接串
AI Key
高德 Key
Supabase Secret Key
```

---

# 8. 开发里程碑总览

| 里程碑 | 名称 | 主要成果 |
|---|---|---|
| M0 | 仓库与环境 | 前后端能启动，CI能运行 |
| M1 | 第一条垂直切片 | 问卷→Mock推荐→结果 |
| M2 | 页面骨架 | 登录、首页、问卷、结果、商家等页面 |
| M3 | 数据库与历史 | 表、迁移、推荐历史 |
| M4 | Supabase Auth | 注册登录、Token、用户隔离 |
| M5 | 额度与幂等 | 每天3次，并发安全 |
| M6 | 地点与Mock POI | 三种地点方式、商家列表 |
| M7 | 高德Live | 真实地点和附近商家 |
| M8 | AI Live | 补问、三推荐、降级 |
| M9 | 公共模块 | 大家在吃什么、活动、共享 |
| M10 | 反馈与设置 | 满意度、历史管理、隐私 |
| M11 | 测试与加固 | 自动化测试、限流、安全 |
| M12 | 部署与发布 | 在线演示、README、v1.0.0 |

---

# 9. M0：仓库与开发环境

## 9.1 目标

建立可运行的空项目和统一规范。

## 9.2 任务

### 根目录

- 初始化Git；
- 创建README；
- 创建LICENSE；
- 创建目录；
- 创建`.gitignore`；
- 创建`.editorconfig`；
- 创建`.env.example`；
- 导入docs文档。

### 前端

- Vite React TypeScript；
- 安装React Router；
- 建立基础路由；
- 建立全局样式；
- 建立测试；
- 建立ESLint和Prettier。

### 后端

- `uv init`；
- 添加FastAPI和Uvicorn；
- 创建`GET /health/live`；
- 配置Ruff、mypy、pytest；
- 建立配置读取。

### CI

- 前端Lint和测试；
- 后端Lint和测试；
- 构建前端。

## 9.3 验收

```text
npm run dev
→ 前端正常打开

uv run uvicorn app.main:app --reload
→ 后端启动

GET /health/live
→ 200

GitHub Actions
→ 全绿
```

## 9.4 推荐提交

```text
chore: initialize EatWhat monorepo
```

---

# 10. M1：第一条垂直切片

## 10.1 目标

不接认证、数据库、AI和高德，先证明前后端闭环。

## 10.2 流程

```text
固定问卷页面
→ 提交FastAPI
→ 规则/Mock推荐
→ 返回三种食物
→ 前端显示首选
→ 展开另外两种
```

## 10.3 后端任务

- 问卷Schema；
- Mock推荐Provider；
- `POST /questionnaire/candidates`；
- 简化版`POST /recommendations/mock-generate`；
- 统一错误格式；
- 单元测试。

## 10.4 前端任务

- 问卷页面；
- 问卷进度；
- 数据提交；
- 加载状态；
- 推荐结果页面；
- 更多推荐；
- 错误提示。

## 10.5 暂不实现

- 登录；
- 数据库；
- 每日额度；
- 定位；
- 商家；
- 历史；
- AI；
- 公共统计。

## 10.6 验收

- 用户能完整答题；
- 后端收到结构化答案；
- 返回固定3项；
- 结果页可刷新前端状态；
- 错误时有提示；
- 自动测试通过。

---

# 11. M2：完整页面骨架

## 11.1 目标

将文字原型转换为可浏览页面，暂用Mock数据。

## 11.2 页面

- 登录；
- 注册；
- 首页；
- 问卷；
- 问卷摘要；
- AI补问；
- 推荐结果；
- 地点选择；
- 商家列表；
- 公共选择；
- 历史；
- 设置；
- 免责声明；
- 隐私说明；
- 404。

## 11.3 要求

- 响应式；
- 手机底部导航；
- 电脑顶部导航；
- 路由保护先用Mock会话；
- 加载、空数据和错误状态；
- 基础无障碍标签。

## 11.4 验收

- 所有页面可导航；
- 电脑和手机无明显横向滚动；
- 主流程可通过Mock完整演示；
- 没有死链接。

---

# 12. M3：数据库与推荐历史

## 12.1 目标

创建真实数据库结构，但认证可以先使用开发用户。

## 12.2 任务

- SQLAlchemy Models；
- Alembic初始迁移；
- profiles；
- food_categories；
- recommendation_sessions；
- recommendation_items；
- merchant_snapshots；
- feedback；
- shared_choices；
- usage日志；
- Seed食物分类；
- Repository；
- 数据库测试。

## 12.3 开发用户

在正式Auth前，可以使用：

- 测试Token；
- 开发环境固定用户；
- 测试数据库用户。

该逻辑必须只在development/test启用。

## 12.4 验收

- 全新数据库可由迁移创建；
- Seed可重复执行；
- 推荐记录可保存和读取；
- 用户隔离测试存在；
- 数据库重建不依赖Dashboard手工操作。

---

# 13. M4：Supabase Auth

## 13.1 目标

替换Mock会话，建立正式云端账户。

## 13.2 前端

- Supabase客户端；
- 注册；
- 自动登录；
- 登录；
- 会话恢复；
- 退出；
- Auth Guard；
- 演示账户入口。

## 13.3 后端

- Bearer Token解析；
- JWKS验证；
- current_user依赖；
- profile创建触发器；
- 用户资源权限。

## 13.4 验收

- 注册后自动登录；
- 刷新保持会话；
- 退出后无法访问受保护页；
- 错误Token返回401；
- A不能访问B历史；
-浏览器中无服务端Secret。

---

# 14. M5：每日额度与推荐状态机

## 14.1 目标

完成真正的每天3次AI额度基础设施，即使AI仍是Mock。

## 14.2 任务

- daily_ai_usage；
- used_count；
- reserved_count；
- Asia/Shanghai日期；
- idempotency_key；
- 推荐状态机；
- 超时预留释放；
- 首页剩余额度；
- 额度用完页面；
- 并发测试。

## 14.3 使用MockAI验证

MockAI支持：

- 成功；
- 延迟；
- 超时；
- 非法JSON；
- 服务异常。

## 14.4 验收

- 3次成功后第4次拒绝；
- AI失败不扣；
- 同幂等键不重复扣；
- 同时4个请求最多3个预留；
- 次日新记录恢复；
- 规则降级不扣。

---

# 15. M6：地点与Mock POI

## 15.1 目标

先完成地点和商家体验，不接真实高德。

## 15.2 前端

- 浏览器定位；
- 定位前说明；
- 定位拒绝；
- 手动地点搜索Mock；
- 演示地点；
- 会话位置复用；
- 商家列表；
- 无结果；
- 查看更多。

## 15.3 后端

- location_token；
- Mock地点；
- Mock反向地理编码；
- Mock商家；
- 食物与关键词映射；
- 商家快照。

## 15.4 验收

三种地点方式都可以返回商家。

精确位置：

- 不入数据库；
- 不进日志；
- 会话结束可清理。

---

# 16. M7：高德Live接入

## 16.1 目标

替换Mock POI为真实地点与周边搜索。

## 16.2 任务

- 创建高德Web服务应用；
- 配置服务端Key；
- 地点关键词搜索；
- 反向地理编码；
- 周边POI；
- 响应标准化；
- 地址和距离；
- 超时；
- 错误码；
- TTL缓存；
- 全局开关。

## 16.3 注意

- 前端不持有Web服务Key；
- 不能承诺评分、价格和营业；
- 实际调用限额上线前重新核查；
- URL和参数遵循当前高德官方文档。

## 16.4 验收

- 浏览器定位可查询附近；
- 手动地点可查询；
- 活动品牌可搜索；
- 无结果可扩大范围；
- Provider失败可回Mock；
- 重复查询命中缓存。

---

# 17. M8：AI Live接入

## 17.1 目标

替换MockAI，完成真实补问和最终推荐。

## 17.2 先完成的基础

- food_categories稳定；
- 硬过滤；
- 规则评分；
- 候选多样性；
- JSON Schema；
- Pydantic校验；
- 规则降级；
- Prompt版本。

## 17.3 Provider

- AIProvider协议；
- Live实现；
- 超时；
- 一次重试；
- Token日志；
- 成本估算；
- 全局关闭。

## 17.4 验收

- 补问0–2个；
- 结果正好3个；
- food_code均合法；
- 无重复；
- 不出现商家；
- 失败自动降级；
- 失败不扣次数；
- AI输入不含身份和精确位置。

---

# 18. M9：公共模块和活动

## 18.1 “大家都在吃什么”

- 匿名共享；
- 聚合统计；
- count >= 3；
- 同星期参考；
- 默认热门项；
- 首页3/5项；
- 完整公共页；
- 点击查商家。

## 18.2 活动

- activities.json；
- 星期筛选；
- 横幅；
- 品牌搜索；
- 免责声明。

## 18.3 验收

- 冷启动页面不空；
- 默认热门项无虚假人数；
- 真实人数达到阈值才出现；
- 前端看不到单条共享数据；
- 活动不调用AI、不消耗次数。

---

# 19. M10：反馈、设置和数据管理

## 19.1 反馈

- 有帮助/没帮助；
- 详细可选反馈；
- 更新同一条反馈；
- 输入转义和长度限制。

## 19.2 历史

- 列表；
- 详情；
- 筛选；
- 删除单条；
- 清空全部；
- 商家快照。

## 19.3 设置

- 账户信息；
- 清空历史；
- 隐私；
- 免责声明；
- 退出；
- v1.1功能说明。

## 19.4 验收

- 删除历史后个人记录消失；
- 匿名共享解除会话关联；
- 不能删除他人记录；
- 演示账户有防误操作限制。

---

# 20. M11：测试、安全与性能

## 20.1 自动化

- 后端单元测试；
- API集成测试；
- 前端组件测试；
- Playwright主流程；
- CI。

## 20.2 安全

- Secret扫描；
- 依赖扫描；
- CORS；
- CSP；
- XSS测试；
- Token测试；
- 资源越权测试；
- 限流；
- 日志脱敏。

## 20.3 性能

目标不是高并发，而是：

- 首页合理加载；
- POI缓存；
- AI超时可恢复；
- 数据库索引；
- 前端构建大小不过度膨胀。

## 20.4 验收

发布门槛全部通过，详见测试计划。

---

# 21. M12：部署与v1.0.0发布

## 21.1 前端

- 构建；
- 环境变量；
- 正式API地址；
- HTTPS；
- 域名（可选）。

## 21.2 后端

- Dockerfile；
- 健康检查；
- 生产启动命令；
- 数据库连接池；
- Secret；
- 日志；
- CORS。

## 21.3 Supabase

- Auth配置；
- Redirect URL；
- Migration；
- Seed；
- 演示账户；
- 备份确认。

## 21.4 GitHub展示

- README；
- 项目截图；
- 流程GIF；
- 架构图；
- 在线演示地址；
- 演示账户；
- 本地运行；
- Mock模式；
- 隐私说明；
- 已知限制；
- ROADMAP。

## 21.5 发布

- 创建Tag `v1.0.0`；
- GitHub Release；
- 发布说明；
- 已知问题；
- v1.1计划。

---

# 22. 推荐Sprint划分

用户是初学者，不按企业高压节奏安排。每个Sprint完成一个清晰成果。

## Sprint 0：项目骨架

对应 M0。

## Sprint 1：最小问卷推荐闭环

对应 M1。

## Sprint 2：全部页面Mock版

对应 M2。

## Sprint 3：数据库与历史

对应 M3。

## Sprint 4：认证和用户隔离

对应 M4。

## Sprint 5：额度与状态机

对应 M5。

## Sprint 6：地点和Mock商家

对应 M6。

## Sprint 7：真实高德

对应 M7。

## Sprint 8：真实AI

对应 M8。

## Sprint 9：公共、活动、反馈和设置

对应 M9、M10。

## Sprint 10：测试、部署和发布

对应 M11、M12。

Sprint不绑定固定天数。以验收结果为准，不为了赶时间跳过测试。

---

# 23. Codex任务模板

每次交给Codex时，使用固定格式：

```text
任务目标：
完成什么功能。

背景文档：
关联的docs文件和章节。

允许修改：
明确目录和文件。

禁止修改：
其他模块、业务规则、Secret。

接口合同：
输入、输出、错误码。

验收标准：
可操作和可测试条件。

测试要求：
需要新增哪些测试。

提交要求：
Commit类型和摘要。
```

## 23.1 示例：健康检查

```text
任务目标：
在backend创建FastAPI应用和GET /health/live。

允许修改：
backend/app/main.py
backend/tests/test_health.py
backend/pyproject.toml

禁止修改：
frontend和docs。

验收标准：
uv run pytest通过；
uv run uvicorn app.main:app --reload可启动；
GET /health/live返回200和{"status":"ok"}。
```

## 23.2 示例：不要这样提问

```text
帮我把后端写完。
```

问题：

- 范围不清楚；
- 容易擅自改架构；
- 难以审核；
- 难以回滚。

---

# 24. 每个任务的完成定义

任务只有同时满足以下条件才算完成：

- 代码可运行；
- 测试通过；
- Lint通过；
- 类型检查通过；
- 没有提交Secret；
- 没有破坏现有功能；
- 文档同步更新；
- Git提交信息清晰；
- 用户可以按照步骤复现。

---

# 25. 功能优先级

## P0：v1.0必须

- 项目骨架；
- 问卷；
- 自主选择；
- AI推荐；
- 地点和商家；
- 认证；
- 每日额度；
- 历史；
- Mock模式；
- 降级；
- 部署。

## P1：作品完整性

- 公共选择；
- 活动；
- 匿名共享；
- 满意度；
- 演示账户；
- Live模式。

## P2：可延后到v1.1

- 邮箱验证；
- 忘记密码；
- 重置密码；
- 注销账户；
- 数据导出；
- 取消共享；
- 管理后台。

---

# 26. 风险控制

| 风险 | 开发策略 |
|---|---|
| 第一次项目过大 | 里程碑拆分 |
| AI接口不稳定 | Mock和规则降级 |
| 高德Key不可用 | Mock POI |
| Supabase配置复杂 | 后置到M4 |
| 数据库设计错误 | Migration和测试 |
| 重复扣次数 | 幂等和预留 |
| Codex大范围改动 | 限定文件和验收 |
| 页面做得不好看 | 先可用，后视觉优化 |
| 部署失败 | 提前Docker化和健康检查 |
| Key泄露 | 后端调用和Secret扫描 |

---

# 27. 开发前仍需创建的账户

开始对应里程碑时再创建，不要一开始全部申请：

| 时机 | 账户 |
|---|---|
| M0 | GitHub |
| M4 | Supabase |
| M7 | 高德开放平台 |
| M8 | AI Provider |
| M12 | 前后端托管平台 |

---

# 28. 初始命令规划

以下只是开发阶段参考，正式执行时按当前工具文档核验。

## 28.1 前端

```powershell
npm create vite@latest frontend -- --template react-ts
cd frontend
npm install
npm run dev
```

## 28.2 后端

```powershell
mkdir backend
cd backend
uv init --python 3.13
uv add fastapi "uvicorn[standard]" pydantic-settings
uv add --dev pytest pytest-asyncio ruff mypy httpx
uv run uvicorn app.main:app --reload
```

## 28.3 Supabase本地环境

进入M4前再执行：

```powershell
npx supabase init
npx supabase start
```

本地Supabase需要Docker Desktop。

---

# 29. 发布门槛

v1.0.0发布前必须满足：

- P0全部完成；
- 主流程E2E通过；
- 额度并发测试通过；
- 历史越权测试通过；
- 精确位置不入库；
- 前端无Secret；
- Mock模式可独立运行；
- Live AI和Live POI可关闭；
- README可复现；
- 演示账户可用；
- 隐私和免责声明可访问；
- 线上HTTPS；
- GitHub Actions全绿；
- 已知问题有记录。

---

# 30. 下一步具体执行顺序

文档设计阶段结束后，不立即接真实服务。

正式编码第一批只做：

```text
任务1：创建GitHub仓库与目录
任务2：初始化React
任务3：初始化FastAPI
任务4：建立健康检查
任务5：建立CI
任务6：实现固定问卷Schema
任务7：实现Mock推荐接口
任务8：实现问卷页面
任务9：实现结果页面
任务10：完成第一次端到端联调
```

完成这些任务后，项目已经具备第一个可运行版本。

---

# 31. 本文档验收结论

本计划已经明确：

- 技术栈；
- Windows开发工具；
- 仓库结构；
- 环境变量；
- 12个里程碑；
- 10个Sprint；
- Codex任务模板；
- 开发顺序；
- v1.0发布门槛。

后续开发时，每完成一个里程碑，应同步更新：

- ROADMAP；
- 项目总进度；
- GitHub Milestone；
- README；
- 测试状态。
