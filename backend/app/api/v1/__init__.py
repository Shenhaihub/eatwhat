"""v1 API 子路由打包占位。

P2-04 之前：只挂载 questionnaire（/next）；
P2-04 起：同时挂载 recommendations（POST /recommendations）。
P3-01 起：挂载 locations（浏览器/手动/演示三种入口）。
P3-02 起：挂载 restaurants（POST /restaurants/search）。
P4-02 起：挂载 auth（Magic Link 登录/校验/注销）。
P4-03 起：挂载 history（推荐历史 CRUD）。
B 阶段 起：挂载 community（社区 feed / 榜单 / 主题投票 / 点赞）。
P6-04 起：挂载 feedback（反馈提交 / 内容举报）。
"""

from fastapi import APIRouter

from app.api.v1.auth import router as auth_router
from app.api.v1.community import router as community_router
from app.api.v1.feedback import router as feedback_router
from app.api.v1.history import router as history_router
from app.api.v1.location import router as location_router
from app.api.v1.preferences import router as preferences_router
from app.api.v1.questionnaire import router as questionnaire_router
from app.api.v1.recommendations import router as recommendations_router
from app.api.v1.restaurants import router as restaurants_router
from app.api.v1.system import router as system_v1_router

router = APIRouter()
router.include_router(questionnaire_router)
router.include_router(recommendations_router)
router.include_router(location_router)
router.include_router(restaurants_router)
router.include_router(auth_router)
router.include_router(history_router)
router.include_router(preferences_router)
router.include_router(community_router)
router.include_router(feedback_router)
router.include_router(system_v1_router)

__all__ = [
    "auth_router",
    "community_router",
    "feedback_router",
    "history_router",
    "location_router",
    "preferences_router",
    "questionnaire_router",
    "recommendations_router",
    "restaurants_router",
    "router",
    "system_v1_router",
]
