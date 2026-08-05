"""v1 API 子路由打包占位。

P2-04 之前：只挂载 questionnaire（/next）；
P2-04 起：同时挂载 recommendations（POST /recommendations）。
"""

from fastapi import APIRouter

from app.api.v1.questionnaire import router as questionnaire_router
from app.api.v1.recommendations import router as recommendations_router

router = APIRouter()
router.include_router(questionnaire_router)
router.include_router(recommendations_router)

__all__ = ["questionnaire_router", "recommendations_router", "router"]
