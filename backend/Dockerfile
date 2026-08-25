# =============================================================
# EatWhat Backend · uv 多阶段生产构建
# （去掉 syntax=docker/dockerfile:1.7 头，国内网络拉 docker.io syntax 镜像经常超时；
#   本文件只用了 COPY/ENV/RUN/CMD/ARG/FROM，所有语法都是 BuildKit 默认支持的。）
#
# 阶段：
#   1. base     → 拉 python:3.13-slim + 装 uv（apt-get 最小化依赖：ca-certificates curl）
#   2. builder  → 拷贝 pyproject.toml + uv.lock → uv sync --frozen --no-dev（生产依赖）
#   3. runtime  → 仅复制 venv + app 源码；非 root 运行；监听 0.0.0.0:8000
#
# 推荐 compose/本地 build（在项目根或 backend/ 下都可以）：
#   docker build -t eatwhat-backend:latest .
# 或不切目录：
#   docker build -t eatwhat-backend:latest -f backend/Dockerfile backend
# =============================================================

ARG PYTHON_VERSION=3.13-slim-bookworm

# -------------------------------
# 阶段 1：base（共享 Python + uv）
# -------------------------------
FROM python:${PYTHON_VERSION} AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    # 新版 uv install.sh 会把可执行文件直接写到 $UV_INSTALL_DIR（例如 /opt/uv/uv），
    # 不再创建 bin/ 子目录；因此 PATH 放 $UV_INSTALL_DIR 本身（再加 /bin 作为兼容兜底）。
    UV_INSTALL_DIR="/opt/uv" \
    PATH="/opt/uv:/opt/uv/bin:${PATH}" \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
    && rm -rf /var/lib/apt/lists/* \
    && curl -LsSf https://astral.sh/uv/install.sh | sh \
    # install.sh 不会刷新当前 shell；source 它写的 env 文件（里面也会追加 PATH）
    && if [ -f /opt/uv/env ]; then . /opt/uv/env; fi \
    && command -v uv \
    && uv --version

WORKDIR /app

# -------------------------------
# 阶段 2：builder（安装生产依赖）
# -------------------------------
FROM base AS builder

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1

# 只拷贝 lock 文件，最大化利用 layer cache（pyproject/lock 不变就跳过 pip install 层）
COPY pyproject.toml uv.lock ./

# --frozen：严格按 uv.lock 装（锁文件变了会报错，避免生产和本地版本漂移）
# --no-dev：跳过 dev 依赖组（pytest/ruff/mypy/fakeredis…都不进镜像）
RUN uv sync --frozen --no-dev --no-install-project

# -------------------------------
# 阶段 3：runtime（生产镜像）
# -------------------------------
FROM base AS runtime

# 非 root 用户（最小权限）
RUN groupadd --system appuser \
    && useradd --system --gid appuser --create-home --home-dir /home/appuser appuser

# 从 builder 复制 venv
COPY --from=builder /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:${PATH}" \
    VIRTUAL_ENV="/app/.venv"

# 应用代码（排除 .dockerignore 里的 .venv / tests / __pycache__ / .env 等）
COPY . /app

# 数据文件目录 & 日志可写（AI 观测 jsonl 写 .local/logs）
RUN mkdir -p /app/.local/logs && chown -R appuser:appuser /app

USER appuser

# FastAPI + 健康检查都靠这个端口
EXPOSE 8000

# 默认生产启动：1 worker，host=0.0.0.0 才能被容器外访问；想多 worker 请用 --workers N 覆盖 CMD
# 注意：如果要用 --reload，请跑 dev 模式（不要用这个生产镜像）
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
