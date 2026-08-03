"""EatWhat 后端入口。

P1 骨架阶段仅提供健康检查；业务模块按 P2 起逐步接入。
"""

from fastapi import FastAPI

app = FastAPI(title="EatWhat API", version="0.1.0")


@app.get("/health/live")
def health_live() -> dict[str, str]:
    return {"status": "ok"}
