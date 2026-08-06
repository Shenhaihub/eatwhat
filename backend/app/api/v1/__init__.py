"""v1 API 子路由打包占位。

P2-04 之前：只挂载 questionnaire（/next）；
P2-04 起：同时挂载 recommendations（POST /recommendations）。
P3-01 起：挂载 locations（浏览器/手动/演示三种入口）。
P3-02 起：挂载 restaurants（POST /restaurants/search）。
"""

from fastapi import APIRouter

from app.api.v1.location import router as location_router
from app.api.v1.questionnaire import router as questionnaire_router
from app.api.v1.recommendations import router as recommendations_router
from app.api.v1.restaurants import router as restaurants_router

router = APIRouter()
router.include_router(questionnaire_router)
router.include_router(recommendations_router)
router.include_router(location_router)
router.include_router(restaurants_router)

__all__ = [
    "location_router",
    "questionnaire_router",
    "recommendations_router",
    "restaurants_router",
    "router",
]
