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
from app.schemas.enums import CuisineGroup, GenerationMode, SourceType, Taste
from app.services.ai.mock_provider import FOLLOW_UP_TEMPLATES
from app.services.ai.rate_limiter import AIRateLimiter, build_ai_rate_limiter
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

    # P5-07：绑定的登录用户 id。仅用于 AI 额度日限流（按 user 维度计数），不做 RLS。
    # 未登录匿名用户走推荐（未来 P3 扩展）时此字段为 None，此时只走全局维度限流。
    user_id: str | None = None
    # V1 固定 = "rule"（纯规则引擎，不走 AI 链路）；P5 接入后可能是 "ai"。
    # 用于 finalize 时决定：rule → 直接写 legacy_rule_engine；ai → 走 AI 增益。
    generation_mode: str = "rule"

    # AI 增益侧
    round_index_1based_next: int = 1  # 下一问是第几轮（1..3）
    follow_up_history: list[FollowUpAnswer] = field(default_factory=list)
    # P1 修复：持久化"实际展示给前端的题"，用于幂等校验。
    # - 之前 _question_for_round 用固定 FOLLOW_UP_TEMPLATES 当校验对照，
    #   但当 AI 生成动态题（ai_fu_001_ambiance 等）或 _pick_fallback_template
    #   跳过已覆盖维度时，实际展示的题和固定模板对不上，前端一提交就抛
    #   InvalidOptionValueError ("round mismatch")。
    # - 现在：start 返回给前端什么题，就存什么题；answer_and_advance
    #   判下一题是什么，也用同一份持久化列表取，保证 100% 一致。
    follow_up_questions: list[FollowUpQuestionOutput] = field(default_factory=list)

    # 最终结果（stage=final 后写入）
    final_items: list[RecommendationItem] | None = None
    final_reason: str | None = None  # "ai_gain" | "rule_engine_fallback_*"

    # P6-04：最近历史偏好画像摘要（自然语言），拼进 system prompt 喂给 DeepSeek
    # 失败/无历史时为空串 ""，调用者通过 with_preference_context() 注入
    preference_context: str = ""
    # P7-05：实际合并进入 preference_context 的快照条数（0 表示未合并/未命中/失败）
    preference_context_snapshot_count: int = 0

    # P7-07：P6-02 冷启动画像合并实际改变的 answers 字段（传给前端 banner 展示）
    # 每个元素形如 { "field": key, "kind": "single" | "list" | "ai_follow_up", "before": ..., "after": ... }
    merged_pref_fields: list[dict[str, Any]] = field(default_factory=list)

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
        user_id: str | None = None,
        questionnaire_answers_by_qid: dict[str, list[str]],
        questionnaire_version: str,
        dictionary_version: str,
        generation_mode: str = "rule",
        rule_answers: Any,
    ) -> RecommendationSession:
        now = time.monotonic()
        sid = secrets.token_urlsafe(16)  # ~21 字符，URL 安全
        session = RecommendationSession(
            session_id=sid,
            started_at=now,
            last_active_at=now,
            user_id=user_id,
            questionnaire_answers_by_qid=dict(questionnaire_answers_by_qid),
            questionnaire_version=questionnaire_version,
            dictionary_version=dictionary_version,
            generation_mode=generation_mode,
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
        # 选择合适的默认模板（跳过已被问卷覆盖的维度）
        default = self._pick_fallback_template(session, round_index_1based=1)
        if default is None:
            # 问卷已覆盖所有可追问维度，直接跳过追问
            return None
        ai_q = await self._chat_service.generate_follow_up(
            system_prompt=prompt_sys,
            user_prompt=prompt_user,
            round_index_1based=1,
            user_id=session.user_id,
        )
        outcome = "used" if ai_q is not None else "fallback_default_template"
        self._log_ai_call_meta(
            ai_stage="follow_up",
            session=session,
            round_index_1based=1,
            prompt_sys=prompt_sys,
            prompt_user=prompt_user,
            ai_outcome=outcome,
        )
        # AI 失败/越界/超时 → 静默用跳过已覆盖维度的默认题（fail-open）
        # 过滤规则：AI 返回到的题若是"重复维度/已答过"，丢弃改用默认题，避免用户被重复问。
        if ai_q is not None and self._question_is_redundant(session, ai_q):
            outcome = "fallback_default_template"  # 重新打点：实际用了回退题
            ai_q = None
        q = ai_q or default
        # P1 修复：把实际要展示给前端的题，持久化进 session 用于后续幂等校验
        if q is not None:
            session.follow_up_questions.append(q)
        return q

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
            # 2) 校验：与持久化列表中"当前该轮到的题"完全一致
            #    follow_up_questions 存的就是 start/advance 实际返回给前端的题，
            #    与展示端 1:1 对齐，不再用 FOLLOW_UP_TEMPLATES 做对照。
            idx = session.round_index_1based_next - 1
            if idx < 0 or idx >= len(session.follow_up_questions):
                # 之前就被判信息充分直接 final 了；重复调用抛已答过
                raise QuestionAlreadyAnsweredError(question_id)
            prev = session.follow_up_questions[idx]
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
            # 3b) 把 AI 追问答案回写到 rule_answers 七维字段（P7-03 bugfix：
            #     之前口味/菜系维度只存在 follow_up_history 中，偏好快照中 tastes/cuisine_preferences 恒为空）
            _apply_follow_up_answer_to_rule_answers(
                session.rule_answers, question_id, selected_option_value,
            )
            next_round = session.round_index_1based_next + 1
            session.round_index_1based_next = next_round
            session.last_active_at = time.monotonic()

        # 4) 如果上一题的 should_continue=False 或 next_round > 3 → 直接 final
        if (not prev.should_continue) or next_round > 3:
            return None
        # 5) 否则生成下一道 AI 追问（失败回退默认题，跳过已覆盖维度）
        prompt_sys, prompt_user = self._build_follow_up_prompts(session)
        default_next = self._pick_fallback_template(session, round_index_1based=next_round)
        ai_q = await self._chat_service.generate_follow_up(
            system_prompt=prompt_sys,
            user_prompt=prompt_user,
            round_index_1based=next_round,
            user_id=session.user_id,
        )
        outcome = "used" if ai_q is not None else (
            "fallback_default_template" if default_next is not None else "fallback_to_final"
        )
        # 过滤规则：AI 返回的题若是"重复维度/已答过"，丢弃改用默认题，避免 mock 轮次错位把同一题再抛给前端。
        if ai_q is not None and self._question_is_redundant(session, ai_q):
            outcome = "fallback_default_template" if default_next is not None else "fallback_to_final"
            ai_q = None
        self._log_ai_call_meta(
            ai_stage="follow_up",
            session=session,
            round_index_1based=next_round,
            prompt_sys=prompt_sys,
            prompt_user=prompt_user,
            ai_outcome=outcome,
        )
        next_q = ai_q or default_next
        # P1 修复：把下一题也持久化进 session，等前端来提交这题时用同一份对照校验
        if next_q is not None:
            with self._lock:
                session.follow_up_questions.append(next_q)
        return next_q

    # ============== 最终推荐生成：AI（增益）+ 规则引擎（兜底真源）==============
    def finalize_recommendation(
        self,
        *,
        session: RecommendationSession,
        repo: FoodDictionaryRepository,
        reason: str = "legacy_rule_engine",
    ) -> list[RecommendationItem]:
        """同步封装：纯规则引擎直接生成 5 条推荐（V1 generation_mode='rule' 默认路径）。

        参数 reason：决定写入 session.final_reason 的值，前端据此显示 badge 颜色/文案：
          - "legacy_rule_engine"  : V1 正常的纯规则路径（不是 AI 回退）→ 中性灰/蓝 badge
          - "rule_engine_fallback_empty_ai"  : 旧 AI 路径里"没产生任何输出"的回退
        """
        if session.final_items is not None:
            return list(session.final_items)
        items = generate_rule_recommendations(session.rule_answers, repo=repo)
        session.final_items = list(items)
        session.final_reason = reason
        return list(items)

    async def try_ai_finalize_recommendation(
        self,
        *,
        session: RecommendationSession,
        repo: FoodDictionaryRepository,
    ) -> list[RecommendationItem]:
        """先尝试 AI 增益生成 5 候选（food_code 全部落在字典内 + 合法 schema）。
        任何一步失败 → 回退规则引擎。结果写进 session.final_items 后幂等返回。

        P5-09：AI 失败时按细分 fail_code 写入 session.final_reason，便于前端
        source badge 显示更具体的原因（Unauthorized / Quota / Timeout / ...）。

        V1：session.generation_mode = 'rule' → 直接走纯规则引擎，不调用 AI 链路，
        避免前端显示"AI 结果不可用"这种容易让用户以为"系统坏了"的负面 badge。
        """
        if session.final_items is not None:
            return list(session.final_items)

        # V1 纯规则模式：跳过 AI 调用链，直接出规则结果
        if session.generation_mode == "rule":
            return self.finalize_recommendation(
                session=session,
                repo=repo,
                reason="legacy_rule_engine",
            )

        prompt_sys, prompt_user = self._build_final_prompts(session)
        ai_out: FinalRecommendationOutput | None = (
            await self._chat_service.generate_final_recommendation(
                system_prompt=prompt_sys,
                user_prompt=prompt_user,
                user_id=session.user_id,
            )
        )
        # P5-09：AI 细分失败码（成功 = None，失败 = build/local_quota/remote_quota/
        # unauthorized/timeout/schema/unknown）
        fail_code = self._chat_service.take_last_fail_code()

        if ai_out is not None:
            try:
                items = _ai_output_to_recommendation_items(ai_out, repo=repo)
                if len(items) == 5:
                    session.final_items = items
                    session.final_reason = "ai_gain"
                    self._log_ai_call_meta(
                        ai_stage="final",
                        session=session,
                        round_index_1based=None,
                        prompt_sys=prompt_sys,
                        prompt_user=prompt_user,
                        ai_outcome="used",
                        ai_fail_code=fail_code,
                        final_reason=session.final_reason,
                    )
                    return list(items)
            except Exception as exc:
                log.warning(
                    "ai_to_items_failed session=%s err_type=%s",
                    session.session_id,
                    type(exc).__name__,
                )

        # 兜底：规则引擎 + 按 fail_code 细分 final_reason
        items = generate_rule_recommendations(session.rule_answers, repo=repo)
        session.final_items = list(items)
        session.final_reason = _map_ai_fail_code_to_final_reason(fail_code)
        self._log_ai_call_meta(
            ai_stage="final",
            session=session,
            round_index_1based=None,
            prompt_sys=prompt_sys,
            prompt_user=prompt_user,
            ai_outcome="fallback_rule_engine",
            ai_fail_code=fail_code,
            final_reason=session.final_reason,
        )
        return list(items)

    # ============== 内部：Prompt 翻译（七维 + 已答追问题 → 自然语言摘要）==============
    def _build_follow_up_prompts(
        self, session: RecommendationSession
    ) -> tuple[str, str]:
        base = _describe_rule_answers_brief(session.rule_answers)
        history_desc = _describe_follow_up_history(session.follow_up_history)
        pref_blk = session.preference_context.strip()
        # 判断菜系是否已在问卷中收集，避免 AI 追问重复问菜系
        cuisine_already_covered = self._cuisine_dimension_covered(session)
        system = (
            "你是 EatWhat 的个性化追问助手。\n"
            "目标：用最多 3 轮单选追问，把用户偏好补全得更精准（口味/氛围/营养三维为主）。\n"
            "重要：基础问卷已覆盖用餐时段、饱腹程度、忌口、口味偏好、预算、明确想吃等维度，"
            "不要在追问中重复询问这些维度。\n"
            + (
                "用户的菜系偏好已在问卷中收集，不要再追问菜系相关问题。\n"
                if cuisine_already_covered
                else "菜系维度问卷未覆盖，可在追问中补充菜系偏好。\n"
            )
            + "【偏好优先级铁律】本次问卷与 AI 追问中用户给出的明确回答，永远优先于任何历史画像；"
            "用户每次使用都是一次全新决策，历史画像仅作为弱先验参考，绝不能因为'用户以前喜欢吃 X'就忽略其本次明确表达的需求。\n"
            "输出必须严格符合 FollowUpQuestionOutput JSON schema，不要加任何 Markdown 或注释。\n"
            "安全边界：question_id 格式必须为 ai_fu_00X_slug（X ∈ {1,2,3}），"
            "options 至少 2 条最多 6 条，value 唯一，title_zh/purpose_zh 中文。"
            + (
                ("\n\n[用户历史画像参考]：以下为此用户最近几次推荐生成的偏好快照，仅作为你出题时的弱先验；"
                 "若与本次问卷或追问冲突，以本次为准，且不要因为历史偏好而跳过用户本次可能感兴趣的维度。\n" + pref_blk)
                if pref_blk else ""
            )
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
        pref_blk = session.preference_context.strip()

        # 构建食物字典中的有效 food_code 列表，供 AI 选择
        from app.repositories.food_dictionary import get_food_dictionary_repository
        repo = get_food_dictionary_repository()
        enabled_items = repo.list_enabled()
        code_list = "\n".join(
            f"    - {it.food_code} ({it.display_name_zh})"
            for it in enabled_items
        )

        system = (
            "你是 EatWhat 的最终推荐生成助手。\n"
            "输出必须严格符合 FinalRecommendationOutput JSON schema。\n"
            "强约束 1：candidates 长度必须正好 5。\n"
            "强约束 2：food_code 只能从以下启用字典中选择，禁止使用列表外的编码：\n"
            f"{code_list}\n"
            "强约束 3：5 个 food_code 必须互不相同。\n"
            "强约束 4：reason_zh 必须是中文，每句 4-200 字，说明推荐理由。\n"
            "强约束 5：【偏好优先级铁律】本次问卷和 AI 追问中用户给出的明确回答，"
            "永远优先于任何历史画像——用户每次使用都是全新决策，"
            "若用户本次明确选择了想吃的食物（如麻辣烫、牛肉面），该食物必须排在第 1 位；"
            "历史偏好仅作为弱先验，绝不能压过本次的明确选择或当下心情（如口味、预算、氛围）。\n"
            "输出 JSON 即可，禁止任何额外文字、Markdown、代码块。"
            + (
                ("\n\n[用户历史画像参考]：以下为此用户最近几次推荐生成的偏好快照，仅作为推荐时的弱先验参考；"
                 "若与本次问卷或追问冲突，以本次为准。\n" + pref_blk)
                if pref_blk else ""
            )
        )
        user = (
            f"[基础问卷七维摘要]：{base}\n"
            f"[已答 AI 追问历史]：{history_desc or '无追问'}\n"
            "[任务]：基于以上信息给出最终 Top5 推荐，下标 0 = priority 1。"
        )
        return system, user

    # ============== P7-05：AI 调用可观测埋点 ==============
    def _log_ai_call_meta(
        self,
        *,
        ai_stage: str,  # "follow_up" | "final"
        session: RecommendationSession,
        round_index_1based: int | None,
        prompt_sys: str,
        prompt_user: str,
        ai_outcome: str,
        ai_fail_code: str | None = None,
        final_reason: str | None = None,
    ) -> None:
        pref_blk = session.preference_context.strip()
        pref_used = bool(pref_blk)
        pref_chars = len(pref_blk)
        pref_nlines = pref_blk.count("\n") + 1 if pref_blk else 0
        pref_count = session.preference_context_snapshot_count or 0
        extra: dict[str, Any] = {
            "ai_call_stage": ai_stage,
            "session_id": session.session_id,
            "user_id": session.user_id,
            "ai_round_1based": round_index_1based,
            "preference_context_used": pref_used,
            "preference_context_snapshot_count": pref_count,
            "preference_context_chars": pref_chars,
            "preference_context_lines": pref_nlines,
            "system_prompt_chars": len(prompt_sys),
            "user_prompt_chars": len(prompt_user),
            "total_prompt_chars": len(prompt_sys) + len(prompt_user),
            "ai_outcome": ai_outcome,
            "ai_fail_code": ai_fail_code,
            "final_reason": final_reason,
        }
        log.info(
            "ai_call stage=%s round=%s pref_used=%s pref_snaps=%d pref_chars=%d sys_chars=%d user_chars=%d outcome=%s fail_code=%s final_reason=%s",
            ai_stage,
            round_index_1based,
            pref_used,
            pref_count,
            pref_chars,
            len(prompt_sys),
            len(prompt_user),
            ai_outcome,
            ai_fail_code,
            final_reason,
            extra=extra,
        )

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

    def _cuisine_dimension_covered(self, session: RecommendationSession) -> bool:
        """菜系维度是否已覆盖。

        只要满足其一即视为"菜系已经不用再问"：
        1. 问卷里已收集 cuisine_preferences（q07 选过菜系）；
        2. 用户已指定明确想吃的具体食物（explicit_food_preference 非 undecided）——
           如选了 malatang / beef_noodles，已经隐含菜系方向，再问菜系就是重复。
        """
        if bool(getattr(session.rule_answers, "cuisine_preferences", None)):
            return True
        exp = getattr(session.rule_answers, "explicit_food_preference", None)
        if exp is not None:
            val = exp.value if hasattr(exp, "value") else exp
            if val not in ("undecided", "none", ""):
                return True
        return False

    def _pick_fallback_template(
        self, session: RecommendationSession, *, round_index_1based: int
    ) -> FollowUpQuestionOutput | None:
        """根据当前问卷已覆盖的维度 + 已答过的题，挑选合适的默认回退模板。

        - 跳过已被问卷覆盖的维度题（q07 cuisine_preferences / q04 tastes 等），
          避免 AI 失败时重复问用户已经回答过的问题；
        - 跳过已答过的 question_id（幂等），避免 mock/轮次错位时把同一道题再抛给前端。
        返回 None 表示"剩余模板全被覆盖/已答 → 直接 final"。
        """
        answered_qids = {a.question_id for a in session.follow_up_history}
        cuisine_covered = self._cuisine_dimension_covered(session)
        flavor_covered = bool(getattr(session.rule_answers, "tastes", None))

        # 模板与"是否应跳过"的映射（question_id 前缀 → 维度/已答）
        def _is_skippable(t: FollowUpQuestionOutput) -> bool:
            if t.question_id in answered_qids:
                return True
            if t.question_id == "ai_fu_001_cuisine" and cuisine_covered:
                return True
            return bool(t.question_id == "ai_fu_002_flavor" and flavor_covered)

        start_idx = max(round_index_1based - 1, 0)
        for idx in range(start_idx, len(FOLLOW_UP_TEMPLATES)):
            t = FOLLOW_UP_TEMPLATES[idx]
            if not _is_skippable(t):
                return t
        # 所有剩余模板都被覆盖/已答 → 直接跳过追问
        return None

    def _question_is_redundant(
        self, session: RecommendationSession, q: FollowUpQuestionOutput | None
    ) -> bool:
        """判断一道 AI 生成的追问是否"重复"：要么该维度问卷已覆盖，要么已答过。

        用于把 AI（尤其 mock 按固定模板轮询）返回的重复题过滤掉，
        回退到 _pick_fallback_template 的下一道可用题。
        """
        if q is None:
            return False
        answered_qids = {a.question_id for a in session.follow_up_history}
        if q.question_id in answered_qids:
            return True
        if q.question_id == "ai_fu_001_cuisine" and self._cuisine_dimension_covered(session):
            return True
        return bool(q.question_id == "ai_fu_002_flavor" and bool(getattr(session.rule_answers, "tastes", None)))

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
        ("cuisine_preferences", "菜系偏好"),
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


# AI 追问选项值 → 七维字段的映射表（纯数据，方便单测覆盖）
# ai_fu_002_flavor 选项 value → Taste 枚举值列表（支持一个选项映射到多个口味）
_FLAVOR_OPTION_TO_TASTES: dict[str, list[Taste]] = {
    "light": [Taste.LIGHT],
    "savory": [Taste.SALTY],          # "咸香浓郁" → 咸/香
    "sour_spicy": [Taste.SOUR, Taste.SPICY],  # "酸辣/开胃" → 酸+辣
    "sweet": [Taste.SWEET],
}

# ai_fu_001_cuisine 选项 value → CuisineGroup 枚举值列表
_CUISINE_OPTION_TO_GROUPS: dict[str, list[CuisineGroup]] = {
    "chinese_north": [CuisineGroup.CHINESE_STAPLE],
    "chinese_south": [CuisineGroup.CHINESE_STAPLE],
    "western": [CuisineGroup.WESTERN],
    "japanese_korean": [CuisineGroup.JAPANESE, CuisineGroup.KOREAN],
    # "只要辣（川菜/麻辣烫/烧烤）" 本质是口味偏好，映射到 tastes 而非菜系
    "spicy": [],  # 特殊处理：追加到 tastes
}


def _apply_follow_up_answer_to_rule_answers(
    rule_answers: Any,
    question_id: str,
    selected_option_value: str,
) -> None:
    """把一道 AI 追问题的答案回写到 rule_answers 对应字段。

    设计要点：
    - 幂等：重复调用同一 (question_id, value) 不会产生重复列表项。
    - 只追加不覆盖：追问题是"补全"语义，不清空问卷已有答案。
    - 容错：未知 question_id 或 option value 不抛错，仅存入 ai_follow_up_answers 原始记录。
    - ai_fu_003_vibe（用餐氛围）没有对应七维字段，存入 ai_follow_up_answers 供 AI prompt 参考。
    """
    # 始终存入 ai_follow_up_answers 原始字典（便于快照/审计/AI prompt 使用）
    fua = getattr(rule_answers, "ai_follow_up_answers", None)
    if isinstance(fua, dict):
        fua[question_id] = selected_option_value

    if question_id == "ai_fu_002_flavor":
        tastes = getattr(rule_answers, "tastes", None)
        if isinstance(tastes, list):
            mapped = _FLAVOR_OPTION_TO_TASTES.get(selected_option_value, [])
            existing = {str(t) for t in tastes}
            for t in mapped:
                if str(t) not in existing:
                    tastes.append(t)

    elif question_id == "ai_fu_001_cuisine":
        if selected_option_value == "spicy":
            # "只要辣"是口味诉求，追加到 tastes
            tastes = getattr(rule_answers, "tastes", None)
            if isinstance(tastes, list) and Taste.SPICY not in {str(t) for t in tastes}:
                tastes.append(Taste.SPICY)
        groups = getattr(rule_answers, "cuisine_preferences", None)
        mapped_groups = _CUISINE_OPTION_TO_GROUPS.get(selected_option_value, [])
        if isinstance(groups, list) and mapped_groups:
            existing = {str(g) for g in groups}
            for g in mapped_groups:
                if str(g) not in existing:
                    groups.append(g)

    # ai_fu_003_vibe（氛围）：无七维映射，已写入 ai_follow_up_answers，此处不再处理
    # AI 动态生成的其他 question_id：仅写入 ai_follow_up_answers，不做启发式映射


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


# ============== P5-09：AI 细分失败码 → session.final_reason 映射 ==============


def _map_ai_fail_code_to_final_reason(fail_code: str | None) -> str:
    """将 ChatService.take_last_fail_code() 细分值映射为 final_reason 落库值。

    约定 key 前缀 rule_engine_fallback_* 保持历史日志/看板兼容；新增
    local_quota/remote_quota/unauthorized/timeout/schema 五类细分级，
    前端 sourceBadge.describeFinalReason 依此显示不同文案/颜色。
    """
    mapping: dict[str, str] = {
        "build": "rule_engine_fallback_ai_build_fail",
        "local_quota": "rule_engine_fallback_ai_local_quota",
        "remote_quota": "rule_engine_fallback_ai_remote_quota",
        "unauthorized": "rule_engine_fallback_ai_unauthorized",
        "timeout": "rule_engine_fallback_ai_timeout",
        "schema": "rule_engine_fallback_ai_schema",
        "unknown": "rule_engine_fallback_ai_fail",  # 归类失败 → 旧默认值兼容
    }
    if fail_code is None:
        # ai_out is None 但 fail_code 也为 None 的情形（极罕见，说明未走分类）
        return "rule_engine_fallback_ai_fail"
    return mapping.get(fail_code, "rule_engine_fallback_ai_fail")


# ============== FastAPI 依赖（单例）==============

_manager_singleton: RecommendationSessionManager | None = None
_manager_lock = threading.Lock()


def get_recommendation_session_manager(settings: Settings) -> RecommendationSessionManager:
    """全局单例（进程内）。若 settings 切换则重建（测试场景）。"""
    global _manager_singleton
    with _manager_lock:
        if _manager_singleton is None:
            rate_limiter: AIRateLimiter = build_ai_rate_limiter(settings)
            chat_service = ChatService(settings=settings, rate_limiter=rate_limiter)
            _manager_singleton = RecommendationSessionManager(
                settings=settings,
                chat_service=chat_service,
            )
        return _manager_singleton
