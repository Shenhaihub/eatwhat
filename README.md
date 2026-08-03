# EatWhat

> 先决定吃什么，再在附近找到它。

EatWhat 是一个响应式 Web 饮食选择辅助工具：先用少量自适应问题（必要时再结合 AI 追问）帮你确定一种想吃的**食物类型**，再通过地图服务把选择落到附近的真实商家。它解决的是"今天吃什么"的决策困难，而不是简单展示餐厅列表。

> **状态：工程骨架搭建中（P1）。** 产品与设计阶段（P0）已完成：权威 PRD v1.2、名词表、流程、信息架构、可点击原型与可用性最小验证均已收敛。正式前后端正在初始化，暂未提供可运行的在线演示。

## 核心功能（规划，按阶段实现）

- 首页三路分流：社区/活动明确食物直达附近商家；"开始推荐"进入混合自适应问卷。
- 混合自适应问卷：2–3 个基础题 + 后端规则选择 2–3 个自适应题，覆盖七个信息维度。
- 登录边界：未登录可用全部非 AI 功能；仅调用 AI 时要求登录并恢复问卷。
- 逐轮 AI 追问（最多 3 轮）→ 固定 5 个食物候选，按 1→3→5 渐进展示。
- 选定食物后查询附近商家；预算只影响食物方向，不承诺商家价格。
- 收尾（评价 / 短问卷 / 匿名分享）全部自愿可跳过。

## 技术栈

| 层 | 技术 |
|---|---|
| 前端 | React + TypeScript + Vite |
| 后端 | Python 3.13 + FastAPI（uv 管理） |
| 数据 | PostgreSQL / Supabase（后置） |
| 认证 | Supabase Auth（后置） |
| 地图 / AI | Provider 抽象，Mock/Live 双模式（后置） |

## 目录结构

```text
project0717/
├── backend/      # FastAPI 后端（建设中）
├── frontend/     # React/Vite 前端（建设中）
├── docs/         # 全部设计、契约与计划文档
├── prototype/    # P0 可点击原型（静态）
└── .env.example  # 环境变量示例（不含真实密钥）
```

## 本地运行

前置：Node.js 24 LTS、Python 3.13、[uv](https://docs.astral.sh/uv/)。无需任何第三方 Key 即可启动（默认 Mock，尚未接入外部服务）。

后端：

```powershell
cd backend
uv sync          # 首次安装依赖并生成/同步 .venv 与 uv.lock
uv run uvicorn app.main:app --reload
# 验证：http://127.0.0.1:8000/health/live -> {"status":"ok"}
```

前端：

```powershell
cd frontend
npm install
npm run dev
# 打开 http://localhost:5173/
```

运行测试与检查：

```powershell
cd backend && uv run pytest && uv run ruff check . && uv run mypy app
cd frontend && npm run build
```

> 当前仅提供健康检查与默认 Vite 页面；业务功能按实施计划 P1–P8 逐步实现。

## 文档

权威入口见 `docs/`：

- 产品基线：`01_EatWhat_PRD_产品需求文档_v1.2_权威需求基线.md`、`00_EatWhat_统一名词表与状态定义_v1.0.md`
- 技术契约勘误：`22_EatWhat_P0-07_技术文档收敛清单_v1.0.md`（16 条权威工程契约）
- 实时计划：`EatWhat_实施计划与状态.md`、`EatWhat_项目记忆.md`

## 已知限制（诚实声明）

- 目前没有任何正式前后端代码，以下均为计划而非已实现功能：登录、AI、地图、历史、社区、活动、配额、部署。
- 社区"大家今天吃什么"在没有真实数据时不展示虚构人数。
- 预算为食物方向软偏好，不保证附近商家价格。

## License

MIT（拟采用，尚未添加 LICENSE 文件）。
