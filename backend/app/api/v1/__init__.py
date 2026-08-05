"""问卷模块子路由的包占位。

暴露 `router` 供上层 `main.py` 方便 include；
具体 `/next` 路由实现见同目录 `questionnaire.py`。
"""

from app.api.v1.questionnaire import router as questionnaire_router

router = questionnaire_router

__all__ = ["questionnaire_router", "router"]
