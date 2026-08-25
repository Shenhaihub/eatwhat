# EatWhat 部署运维手册 v1.0

> 日期：2026-08-24
> 状态：正式交付
> 关联：P7 部署与运维

---

## 1. 部署架构概览

```
┌──────────────────────────────────────────────────────────┐
│                    宿主机 / 云服务器                       │
│                                                          │
│  ┌─────────────┐     ┌─────────────┐     ┌────────────┐ │
│  │  Frontend   │     │  Backend    │     │  Redis     │ │
│  │  Nginx:80   │────▶│  FastAPI    │────▶│  :6379     │ │
│  │  SPA + /api │     │  :8000      │     │  (可选)    │ │
│  └─────────────┘     └─────────────┘     └────────────┘ │
│         :8080              :8000             :6379      │
│                                                          │
│  外部服务：Supabase (Auth + DB) / DeepSeek API / 高德 POI │
└──────────────────────────────────────────────────────────┘
```

---

## 2. 环境要求

| 组件 | 最低版本 | 推荐 |
|------|----------|------|
| Python | 3.13+ | 3.13-slim |
| Node.js | 20+ | 24-bookworm-slim |
| Docker | 24+ | 最新稳定版 |
| Docker Compose | v2+ | 最新稳定版 |
| Redis | 7+ | 7-alpine（可选） |

---

## 3. 快速启动（Docker Compose）

### 3.1 Mock 模式（开发/演示）

无需任何外部服务，开箱即用：

```bash
# 1. 进入项目根目录
cd d:\A622\项目\AgentWork\project0717

# 2. 构建镜像（首次约 3-5 分钟）
docker compose build

# 3. 启动服务
docker compose up -d

# 4. 查看日志
docker compose logs -f

# 5. 访问
# 前端：http://localhost:8080
# 后端 API 文档：http://localhost:8000/docs
# 健康检查：http://localhost:8000/health/live
```

### 3.2 Live 模式（生产）

需要配置外部服务密钥：

```bash
# 1. 复制环境变量模板
cp backend/.env.example backend/.env

# 2. 编辑 .env，填写以下必需项：
#    - APP_ENV=production
#    - APP_MODE=live
#    - SUPABASE_URL / SUPABASE_ANON_KEY / SUPABASE_SERVICE_ROLE_KEY
#    - AI_PROVIDER=deepseek / AI_API_KEY=ENC:... / EW_AI_KEY_PASSPHRASE
#    - POI_PROVIDER=live / AMAP_API_KEY
#    - FRONTEND_ORIGINS=https://your-domain.com

# 3. 加密 AI API Key（参考 .env 第 3 节）
cd backend && uv run python scripts/encrypt_ai_key.py -a

# 4. 启动（含 Redis 限流）
docker compose --profile ai-cluster up -d
```

### 3.3 停止与清理

```bash
# 停止服务（保留数据卷）
docker compose down

# 停止并清除 Redis 数据卷
docker compose down -v

# 完全重建（清除所有缓存）
docker compose build --no-cache
docker compose up -d
```

---

## 4. 环境变量说明

### 4.1 核心变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `APP_ENV` | `development` | 运行环境：development / test / production |
| `APP_MODE` | `mock` | mock=纯本地；live=启用真实第三方服务 |
| `FRONTEND_ORIGINS` | `http://localhost:5173,...` | CORS 允许的前端 Origin（逗号分隔） |

### 4.2 Supabase

| 变量 | 必填(live) | 说明 |
|------|-----------|------|
| `SUPABASE_URL` | ✅ | Supabase 项目 URL |
| `SUPABASE_ANON_KEY` | ✅ | anon public key |
| `SUPABASE_SERVICE_ROLE_KEY` | ✅ | service_role key（仅后端） |
| `SUPABASE_JWKS_URL` | 自动推断 | JWT 验证用 JWKS URL |
| `DATABASE_URL` | 可选 | 直连 PostgreSQL 串 |

### 4.3 AI Provider

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `AI_PROVIDER` | `mock` | mock / deepseek / auto |
| `AI_API_KEY` | 空 | ENC: 开头的加密密文 |
| `EW_AI_KEY_PASSPHRASE` | 空 | 解密口令（≥12 字符） |
| `AI_MODEL` | `deepseek-v4-flash` | 模型名 |
| `AI_DAILY_USER_LIMIT` | `50` | 单用户每日 AI 调用上限 |
| `AI_GLOBAL_DAILY_LIMIT` | `5000` | 全服每日 AI 调用上限 |

### 4.4 POI

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `POI_PROVIDER` | `mock` | mock / live / auto |
| `AMAP_API_KEY` | 空 | 高德 Web 服务 API Key |
| `POI_CACHE_TTL_SECONDS` | `1200` | 缓存 TTL（秒） |

### 4.5 Redis（可选）

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `REDIS_URL` | 空 | redis://host:port/db；空=降级 TTLCache |

---

## 5. 健康检查与监控

### 5.1 健康检查端点

| 端点 | 用途 | 检查内容 |
|------|------|----------|
| `GET /health/live` | 存活探针 | 进程是否存活 |
| `GET /health/ready` | 就绪探针 | 配置 + DB 连通性 |
| `GET /api/v1/system/ai-stats` | AI 观测 | AI 调用统计（最近 N 条） |

### 5.2 Docker 健康检查

- **Backend**：每 20s 检查 `/health/live`，超时 3s，3 次失败重启
- **Frontend**：每 30s 检查 `/index.html`，超时 3s，3 次失败重启
- **Redis**：每 15s 执行 `redis-cli ping`

### 5.3 日志

- **格式**：`时间 | LEVEL | logger | 消息`
- **脱敏**：自动脱敏邮箱、经纬度、Bearer Token、API Key
- **AI 观测**：`backend/.local/logs/ai_call_meta.jsonl`（JSONL 格式，含 session_id、prompt 长度、outcome 等）
- **Docker 日志**：json-file 驱动，单文件 10MB，最多 3 个

### 5.4 AI 调用观测

访问 `GET /api/v1/system/ai-stats?limit=500` 获取：
- 最近 N 条 AI 调用的聚合统计
- 按阶段（follow_up / final）分组的调用次数
- 偏好画像覆盖率
- 失败率与失败原因分布
- 最近 5 条样本记录（user_id/session_id 已脱敏）

---

## 6. 本地开发

### 6.1 一键启动（Windows）

```bash
# 使用加固后的启动脚本
d:\A622\项目\AgentWork\project0717\start-dev.bat
```

### 6.2 手动启动

```bash
# 后端
cd backend
uv run uvicorn app.main:app --reload --port 8000

# 前端
cd frontend
npm run dev  # 默认 :5173
```

### 6.3 运行测试

```bash
# 后端全量测试
cd backend
uv run pytest -q

# 前端构建检查
cd frontend
npm run build
```

---

## 7. 安全清单

| 项目 | 状态 | 说明 |
|------|------|------|
| API Key 加密 | ✅ | Fernet 对称加密，不明文存储 |
| CORS 白名单 | ✅ | 仅允许配置的 Origin |
| JWT 验证 | ✅ | Supabase JWKS + iss/aud 校验 |
| RLS 防线 | ✅ | Supabase Row Level Security |
| 日志脱敏 | ✅ | 邮箱/经纬度/Token 自动替换 |
| 非 root 容器 | ✅ | appuser 运行 |
| Redis 限流 | ✅ | 单用户/全站日限额 |
| 输入校验 | ✅ | Pydantic extra="forbid" |

---

## 8. 故障排查

| 症状 | 可能原因 | 解决方案 |
|------|----------|----------|
| 前端白屏 | 前端构建失败 | `docker compose logs frontend` |
| API 401 | Supabase JWT 过期 | 重新登录获取新 token |
| AI 推荐 0/3 | AI_PROVIDER=mock | 切换为 deepseek |
| AI 结果不可信 | food_code 不在字典 | 检查食物字典版本 |
| Trending 榜空 | 无历史数据 | 正常降级为 Seed 数据 |
| Redis 连接失败 | REDIS_URL 错误 | 自动降级 TTLCache，不影响服务 |

---

## 9. 版本发布流程

1. 确认所有测试通过：`uv run pytest -q`
2. 前端构建成功：`npm run build`
3. 更新版本号（`pyproject.toml` + `package.json`）
4. 构建 Docker 镜像：`docker compose build`
5. 滚动更新：`docker compose up -d`
6. 验证健康检查：`curl http://localhost:8000/health/ready`
7. 验证前端访问：`curl http://localhost:8080/`

---

## 10. 参考

- [Dockerfile (Backend)](../backend/Dockerfile)
- [Dockerfile (Frontend)](../frontend/Dockerfile)
- [docker-compose.yml](../docker-compose.yml)
- [.env.example](../backend/.env.example)
- [05_EatWhat_系统架构设计.md](./05_EatWhat_系统架构设计.md)
