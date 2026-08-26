# =============================================================
# EatWhat Frontend · 多阶段构建
# （去掉 syntax=docker/dockerfile:1.7 头，国内网络拉 docker.io syntax 镜像经常超时；
#   本文件只用了 COPY/ENV/RUN/CMD/FROM/HEALTHCHECK，所有语法都是 BuildKit 默认支持的。）
#
# 阶段：
#   1. builder  → node:24-bookworm-slim，npm ci（严格锁）+ npm run build → dist/
#   2. runtime  → nginx:alpine-slim，拷 dist + nginx.conf，暴露 80
#
#   - SPA fallback：任何静态文件不存在都走 /index.html
#   - /api/* 反代到 compose 服务名 `backend:8000`（保留 /api 前缀，因为后端路由本身就是 /api/v1/*）
#   - 健康检查：curl /index.html
#
# 单独 build（不切目录，根 README Windows 两种跑法见 docker-compose.yml 注释）：
#   docker build -t eatwhat-frontend:latest -f frontend/Dockerfile frontend
# =============================================================

# -------------------------------
# 阶段 1：builder（构建产物）
# -------------------------------
FROM node:24-bookworm-slim AS builder

WORKDIR /app

# 先只拷 lock + manifest：依赖不变 → 整个 install 层复用（npm ci 严格按锁）
COPY package.json package-lock.json ./
RUN npm ci

# 再拷源码（代码变了只会重跑 build 层，不会重新 npm ci）
COPY index.html tsconfig*.json vite.config.ts .oxlintrc.json .prettierrc.json .prettierignore ./
COPY src ./src
COPY public ./public

# 建期注入（Fly.io 等远程部署用）：让前端 /api 直接打到后端公网地址，绕过本地 nginx 反代。
# 默认留空 → 前端走相对路径 /api/v1（本地 compose / dev 的 nginx 反代或 vite proxy 场景）。
# 用法：docker build --build-arg VITE_API_BASE_URL=https://xxx.fly.dev/api/v1 .
ARG VITE_API_BASE_URL=""
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL

# 生产构建：tsc -b + vite build → dist/
#   VITE_API_BASE_URL 等运行期 VITE_* 变量默认不用传（按相对路径），CI 要传就 docker build --build-arg
RUN npm run build

# -------------------------------
# 阶段 2：runtime（nginx 静态 + /api 反代）
# -------------------------------
FROM nginx:1.27-alpine-slim AS runtime

# SPA 默认配置 + /api 反代
COPY nginx.conf /etc/nginx/conf.d/default.conf

# 构建产物
COPY --from=builder /app/dist /usr/share/nginx/html

# 默认端口 & 健康检查
EXPOSE 80
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD wget -qO- http://127.0.0.1/index.html >/dev/null 2>&1 || exit 1

# nginx -g daemon off 是官方镜像默认 CMD，显式写一遍让阅读者一眼看到
CMD ["nginx", "-g", "daemon off;"]
