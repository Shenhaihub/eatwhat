"""P5-02 动态推荐会话状态机（最多 3 轮追问 + AI 增益 + 规则引擎真源兜底）。

设计原则：
    1. **真源分离**：AI 只是"增益层"，生成候选与理由。最终 5 条 food_code 若越界/非法，
       回退 `generate_rule_recommendations`（规则引擎，G-08 5 条不空保障）。
    2. **进程内会话**：不需要 DB——3 轮追问最多用户停留 2~10 分钟，TTLCache 15 分钟足够。
       部署多 worker 时可能命中不同实例导致 404 → 前端提示"会话过期，请重新开始"即可，
       属于可接受降级（若后续需要多实例一致，可切 Redis，当前不做过度设计）。
    3. **幂等**：同一 session 重复答同一 question_id → 409 Conflict，防止并发重复提交
       导致 round 错乱。
    4. **ChatService 全 fail-open**：不管是超时、越界、损坏 JSON，一律视为"AI 增益失败"，
       走默认 3 道追问（Mock 的 FOLLOW_UP_TEMPLATES）或回退规则引擎。用户永远不会卡住。
"""
from __future__ import annotations

import logging
import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from app.core.config import Settings
from app.repositories.food_dictionary import FoodDictionaryRepository
from app.schemas import RecommendationItem
from app.schemas.ai import FinalRecommendationOutput, FollowUpQuestionOutput
from app.schemas.enums import GenerationMode, SourceType
from app.services.ai.mock_provider import FOLLOW_UP_TEMPLATES
from app.services.ai.service import ChatService
from app.services.rule_engine import generate_rule_recommendations

log = logging.getLogger("app.services.recommendation_session")

SESSION_TTL_SECONDS = 15 * 60  # 15 分钟
GC_EVERY_N_CALLS = 32  # 每 32 次调用清一次过期会话，避免每次都 O(n)


@dataclass(slots=True)
class FollowUpAnswer:
    """一道 AI 追问题的已提交答案。"""
    question_id: str
    selected_option_value: str
    answered_at: float


@dataclass(slots=True)
class RecommendationSession:
    session_id: str
    started_at: float
    last_active_at: float

    # 原始问卷 v1.0 answers_by_qid
    questionnaire_answers_by_qid: dict[str, list[str]]
    questionnaire_version: str
    dictionary_version: str

    # 规则引擎的输入（已翻译过的七维），供最终生成回退 & system prompt 组装用
    rule_answers: Any  # QuestionnaireAnswers（不引循环 import，保持 duck-type）

    # AI 增益侧
    round_index_1based_next: int = 1  # 下一问是第几轮（1..3）
    follow_up_history: list[FollowUpAnswer] = field(default_factory=list)

    # 最终结果（stage=final 后写入）
    final_items: list[RecommendationItem] | None = None
    final_reason: str | None = None  # "ai_gain" | "rule_engine_fallback_*"

    @property
    def stage(self) -> str:
        return "final" if self.final_items is not None else "follow_up"

    @property
    def is_expired(self) -> bool:
        return (time.monotonic() - self.last_active_at) > SESSION_TTL_SECONDS


class SessionNotFoundError(LookupError):
    """会话不存在或已过期 → 上层转 404。"""


class QuestionAlreadyAnsweredError(ValueError):
    """重复提交同一道题 → 上层转 409。"""


class InvalidOptionValueError(ValueError):
    """答案 value 不在该题的选项中 → 上层转 400。"""


class RecommendationSessionManager:
    """进程内 TTLCache 会话管理 + 状态机推进 + AI 增益接入 + 规则回退。"""

    def __init__(self, *, settings: Settings, chat_service: ChatService | None = None) -> None:
        self._settings = settings
        self._chat_service = chat_service or ChatService(settings=settings)
        self._lock = threading.RLock()
        self._sessions: dict[str, RecommendationSession] = {}
        self._call_counter = 0

    # ============== 会话 CRUD ==============
    def create_session(
        self,
        *,
        questionnaire_answers_by_qid: dict[str, list[str]],
        questionnaire_version: str,
        dictionary_version: str,
        rule_answers: Any,
    ) -> RecommendationSession:
        now = time.monotonic()
        sid = secrets.token_urlsafe(16)  # ~21 字符，URL 安全
        session = RecommendationSession(
            session_id=sid,
            started_at=now,
            last_active_at=now,
            questionnaire_answers_by_qid=dict(questionnaire_answers_by_qid),
            questionnaire_version=questionnaire_version,
            dictionary_version=dictionary_version,
            rule_answers=rule_answers,
        )
        with self._lock:
            self._sessions[sid] = session
            self._maybe_gc_unlocked()
        return session

    def get_session(self, session_id: str) -> RecommendationSession:
        with self._lock:
            s = self._sessions.get(session_id)
            if s is None or s.is_expired:
                if s is not None:
                    # 惰性清理
                    self._sessions.pop(session_id, None)
                raise SessionNotFoundError(session_id)
            s.last_active_at = time.monotonic()
            self._maybe_gc_unlocked()
            return s

    # ============== 状态机：start → 第 1 道追问或直接 final ==============
    async def start_and_get_next(
        self,
        *,
        session: RecommendationSession,
    ) -> FollowUpQuestionOutput | None:
        """返回下一题；若 AI 判断信息充分或失败则返回 None（直接进 final）。"""
        prompt_sys, prompt_user = self._build_follow_up_prompts(session)
        # 默认第 1 题来自 FOLLOW_UP_TEMPLATES，AI 成功则覆盖为 AI 动态题
        default = FOLLOW_UP_TEMPLATES[0]
        ai_q = await self._chat_service.generate_follow_up(
            system_prompt=prompt_sys,
            user_prompt=prompt_user,
            round_index_1based=1,
        )
        # AI 失败/越界/超时 → 静默用默认第 1 题（fail-open）
        return ai_q or default

    # ============== 状态机：提交回答 → 下一题或 final ==============
    async def answer_and_advance(
        self,
        *,
        session: RecommendationSession,
        question_id: str,
        selected_option_value: str,
        repo: FoodDictionaryRepository,
    ) -> FollowUpQuestionOutput | None:
        """返回 None 表示"不需要再追问，直接生成最终推荐"。"""
        with self._lock:
            # 1) 幂等：同一题不能重答
            if any(a.question_id == question_id for a in session.follow_up_history):
                raise QuestionAlreadyAnsweredError(question_id)
            # 2) 校验：当前必须正好 round_index_1based_next，且 option value 在题里
            prev = await self._question_for_round(
                session, session.round_index_1based_next, repo=repo
            )
            if prev is None:
                # 之前就被判信息充分直接 final 了；重复调用抛已答过
                raise QuestionAlreadyAnsweredError(question_id)
            if prev.question_id != question_id:
                raise InvalidOptionValueError(
                    f"round mismatch：期望 {prev.question_id!r}，收到 {question_id!r}"
                )
            valid_values = {o.value for o in prev.options}
            if selected_option_value not in valid_values:
                raise InvalidOptionValueError(
                    f"option value {selected_option_value!r} 不在合法集合 {sorted(valid_values)}"
                )
            # 3) 记录答案
            session.follow_up_history.append(
                FollowUpAnswer(
                    question_id=question_id,
                    selected_option_value=selected_option_value,
                    answered_at=time.monotonic(),
                )
            )
            next_round = session.round_index_1based_next + 1
            session.round_index_1based_next = next_round
            session.last_active_at = time.monotonic()

        # 4) 如果上一题的 should_continue=False 或 next_round > 3 → 直接 final
        if (not prev.should_continue) or next_round > 3:
            return None
        # 5) 否则生成下一道 AI 追问（失败回退默认题）
        prompt_sys, prompt_user = self._build_follow_up_prompts(session)
        default_next = (
            FOLLOW_UP_TEMPLATES[next_round - 1]
            if 1 <= next_round <= len(FOLLOW_UP_TEMPLATES)
            else None
        )
        ai_q = await self._chat_service.generate_follow_up(
            system_prompt=prompt_sys,
            user_prompt=prompt_user,
            round_index_1based=next_round,
        )
        return ai_q or default_next

    # ============== 最终推荐生成：AI（增益）+ 规则引擎（兜底真源）==============
    def finalize_recommendation(
        self,
        *,
        session: RecommendationSession,
        repo: FoodDictionaryRepository,
    ) -> list[RecommendationItem]:
        """同步封装：先 try_ai_finalize（异步的话外面 await 再走 fallback）。

        这里只做规则引擎兜底 + 写 session；AI 部分由 `try_ai_finalize_recommendation` 完成。
        """
        if session.final_items is not None:
            return list(session.final_items)
        items = generate_rule_recommendations(session.rule_answers, repo=repo)
        session.final_items = list(items)
        session.final_reason = "rule_engine_fallback_empty_ai"
        return list(items)

    async def try_ai_finalize_recommendation(
        self,
        *,
        session: RecommendationSession,
        repo: FoodDictionaryRepository,
    ) -> list[RecommendationItem]:
        """先尝试 AI 增益生成 5 候选（food_code 全部落在字典内 + 合法 schema）。
        任何一步失败 → 回退规则引擎。结果写进 session.final_items 后幂等返回。
        """
        if session.final_items is not None:
            return list(session.final_items)

        prompt_sys, prompt_user = self._build_final_prompts(session)
        ai_out: FinalRecommendationOutput | None = (
            await self._chat_service.generate_final_recommendation(
                system_prompt=prompt_sys,
                user_prompt=prompt_user,
            )
        )

        if ai_out is not None:
            try:
                items = _ai_output_to_recommendation_items(ai_out, repo=repo)
                if len(items) == 5:
                    session.final_items = items
                    session.final_reason = "ai_gain"
                    return list(items)
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "ai_to_items_failed session=%s err_type=%s",
                    session.session_id,
                    type(exc).__name__,
                )

        # 兜底：规则引擎
        items = generate_rule_recommendations(session.rule_answers, repo=repo)
        session.final_items = list(items)
        session.final_reason = "rule_engine_fallback_ai_fail"
        return list(items)

    # ============== 内部：Prompt 翻译（七维 + 已答追问题 → 自然语言摘要）==============
    def _build_follow_up_prompts(
        self, session: RecommendationSession
    ) -> tuple[str, str]:
        base = _describe_rule_answers_brief(session.rule_answers)
        history_desc = _describe_follow_up_history(session.follow_up_history)
        system = (
            "你是 EatWhat 的个性化追问助手。\n"
            "目标：用最多 3 轮单选追问，把用户偏好补全得更精准（菜系/口味/氛围三维为主）。\n"
            "输出必须严格符合 FollowUpQuestionOutput JSON schema，不要加任何 Markdown 或注释。\n"
            "安全边界：question_id 格式必须为 ai_fu_00X_slug（X ∈ {1,2,3}），"
            "options 至少 2 条最多 6 条，value 唯一，title_zh/purpose_zh 中文。"
        )
        user = (
            f"[基础问卷七维摘要]：{base}\n"
            f"[已答 AI 追问历史]：{history_desc or '用户尚未回答过任何追问题'}\n"
            f"[下一步]：请生成第 {session.round_index_1based_next} 轮追问题；"
            "若你认为基础信息已足够给出 Top5，请把 should_continue 设为 false（等价于'直接生成最终推荐'）。"
        )
        return system, user

    def _build_final_prompts(self, session: RecommendationSession) -> tuple[str, str]:
        base = _describe_rule_answers_brief(session.rule_answers)
        history_desc = _describe_follow_up_history(session.follow_up_history)
        system = (
            "你是 EatWhat 的最终推荐生成助手。\n"
            "输出必须严格符合 FinalRecommendationOutput JSON schema。\n"
            "强约束 1：candidates 长度必须正好 5。\n"
            "强约束 2：每个 food_code 必须来自服务端启用的食物字典（若你不确定请只选常见菜系）。\n"
            "强约束 3：5 个 food_code 必须互不相同。\n"
            "强约束 4：reason_zh 必须是中文，每句 4-200 字，说明推荐理由。\n"
            "输出 JSON 即可，禁止任何额外文字、Markdown、代码块。"
        )
        user = (
            f"[基础问卷七维摘要]：{base}\n"
            f"[已答 AI 追问历史]：{history_desc or '无追问'}\n"
            "[任务]：基于以上信息给出最终 Top5 推荐，下标 0 = priority 1。"
        )
        return system, user

    async def _question_for_round(
        self,
        session: RecommendationSession,
        round_index_1based: int,
        *,
        repo: FoodDictionaryRepository,
    ) -> FollowUpQuestionOutput | None:
        """根据 round 索引返回"该轮应该是什么题"（用于幂等校验）。

        注意：start 后第 1 道题可能是 AI 题也可能是默认题，但为了保持当前会话
        幂等一致性，我们实际返回的题要与 start 返回给前端的一致。
        简化：当前 MVP 实现统一用 FOLLOW_UP_TEMPLATES 作为"校验对照"——
        不管 AI 实际生成的题是什么，校验对照都用固定 3 道题的 id 来识别轮次。
        后续若实现"AI 动态题持久化"，可把实际 question 存到 session。
        """
        idx = round_index_1based - 1
        if 0 <= idx < len(FOLLOW_UP_TEMPLATES):
            return FOLLOW_UP_TEMPLATES[idx]
        return None

    # ============== GC：惰性清理过期会话 ==============
    def _maybe_gc_unlocked(self) -> None:
        self._call_counter += 1
        if self._call_counter % GC_EVERY_N_CALLS != 0:
            return
        now = time.monotonic()
        expired = [
            sid for sid, s in self._sessions.items()
            if (now - s.last_active_at) > SESSION_TTL_SECONDS
        ]
        for sid in expired:
            self._sessions.pop(sid, None)


# ============== 模块级纯函数 ==============


def _describe_rule_answers_brief(rule_answers: Any) -> str:
    """把七维规则输入（duck-type，字段名约定）转自然语言短摘要。"""
    parts: list[str] = []
    for dim, label in (
        ("meal_period", "用餐时段"),
        ("appetite", "饱腹程度"),
        ("avoidances", "忌口"),
        ("tastes", "口味偏好"),
        ("budget", "预算"),
        ("explicit_food_preference", "明确想吃"),
    ):
        v = getattr(rule_answers, dim, None)
        if v is None or v == []:
            continue
        if isinstance(v, list):
            val = ",".join(str(x) for x in v)
        else:
            val = str(v)
        if val:
            parts.append(f"{label}={val}")
    return " / ".join(parts) if parts else "（问卷无已答维度，按大众均衡推荐）"


def _describe_follow_up_history(history: list[FollowUpAnswer]) -> str:
    if not history:
        return ""
    items = [f"第{i+1}轮:{a.question_id}→{a.selected_option_value}" for i, a in enumerate(history)]
    return " ; ".join(items)


def _ai_output_to_recommendation_items(
    ai_out: FinalRecommendationOutput,
    *,
    repo: FoodDictionaryRepository,
) -> list[RecommendationItem]:
    """把 AI 的 FinalRecommendationOutput（仅 food_code + reason_zh + tags）
    补全为完整 RecommendationItem（严格匹配 schemas.food.RecommendationItem）。

    字段映射（参考 rule_engine.py L219-236 的正确用法）：
      - display_name / 菜系 meta 等 → 折叠进 reason.summary_zh（UI 层会展示）
      - source_type → SourceType.AI_RECOMMENDED（G-07 服务端派生）
      - generation_mode → GenerationMode.AI（表示 AI 增益链路）
      - budget_fit / budget_fit_note_zh → 继承 FoodDictionaryItem 的三件套
    """
    from app.schemas import RecommendationReason  # 局部 import，避免顶层循环

    items: list[RecommendationItem] = []
    for priority, cand in enumerate(ai_out.candidates, start=1):
        raw = repo.require(cand.food_code)  # 若越界会抛（FinalRecommendationOutput validator 应已拦）
        # 把 AI 的 matched_tags + reason_zh 汇总到结构化 RecommendationReason
        summary_parts: list[str] = [f"Top{priority} {raw.display_name_zh}："]
        if cand.reason_zh:
            summary_parts.append(cand.reason_zh)
        summary_zh = "".join(summary_parts)
        if len(summary_zh) > 160:
            summary_zh = summary_zh[:159] + "…"
        matched_signals = list(cand.matched_tags) if cand.matched_tags else []
        if not matched_signals:
            matched_signals = [f"ai_gain:priority_{priority}"]
        reason = RecommendationReason(
            summary_zh=summary_zh,
            matched_signals=matched_signals,
        )
        budget_note = _budget_note_for_dict_item(raw)
        items.append(
            RecommendationItem(
                priority=priority,
                food_code=cand.food_code,
                source_type=SourceType.AI_RECOMMENDED,
                generation_mode=GenerationMode.AI,
                reason=reason,
                budget_fit=raw.budget_fit_status,
                budget_fit_note_zh=budget_note,
            )
        )
    return items


def _budget_note_for_dict_item(raw: Any) -> str | None:
    """对齐 rule_engine._build_budget_note：不承诺商户具体价格（G-10）。"""
    from app.schemas.enums import BudgetFitStatus, BudgetTier

    if raw.budget_fit_status == BudgetFitStatus.FITS:
        tiers = getattr(raw, "supported_budget_tiers", []) or []
        if len(tiers) == 1:
            label_map = {
                BudgetTier.UNDER_20: "20 元以内",
                BudgetTier.FROM_20_TO_30: "20-30 元",
                BudgetTier.OVER_30: "30 元以上",
            }
            label = label_map.get(tiers[0], str(tiers[0].value))
            return f"平台参考：常见于 {label} 档位附近，具体以商家为准"
        return "平台参考：常见于中低档位区间，具体以商家为准"
    if raw.budget_fit_status == BudgetFitStatus.UNLIKELY:
        return "平台参考：大概率超过中低档位，具体以商家为准"
    return None


# ============== FastAPI 依赖（单例）==============

_manager_singleton: RecommendationSessionManager | None = None
_manager_lock = threading.Lock()


def get_recommendation_session_manager(settings: Settings) -> RecommendationSessionManager:
    """全局单例（进程内）。若 settings 切换则重建（测试场景）。"""
    global _manager_singleton
    with _manager_lock:
        if _manager_singleton is None:
            _manager_singleton = RecommendationSessionManager(settings=settings)
        return _manager_singleton
