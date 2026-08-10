/**
 * P2-04：推荐流程问卷页 + 结果渲染
 * - 路由：/recommend
 * - 入口意图固定 ai_recommend（P2 只做推荐问卷）；其他四入口在 P3/P4 接入。
 * - 数据流（P2-04 新增段：进入结果态）：
 *     0) 问卷阶段：与 P2-03B 相同：POST /questionnaire/next → 渲染 next_questions
 *     1) 底部按钮：next_action = proceed_generate_recommendations → 显示"去看推荐结果"
 *     2) 用户点击按钮 → POST /recommendations
 *         （传 entry_intent=ai_recommend + questionnaire_version=v1.0 + answers_by_question_id）
 *     3) 返回正好 5 条 RecommendationItem → 按 priority 渲染卡片；
 *        主按钮变成"重新来一次" → 重置 answers + localStorage 草稿 + 回到问卷第 1 题态。
 *
 * - 草稿持久化：localStorage key=`eatwhat:questionnaire:draft:v1.0:ai_recommend`；
 *   进入结果态不清草稿（用户可回退修改答案）；点击"重新来一次"才清空草稿。
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { FormEvent } from 'react';

import { api, ApiError } from '../services/api/client';
import type {
  DimensionCoverage,
  FollowUpQuestionV1,
  NextAction,
  QuestionBankItem,
  QuestionnaireRecomputeResult,
  RecommendationItem,
  SessionStateResponseV1,
} from '../services/api/types';
import '../styles/recommendations.css';

const QUESTIONNAIRE_VERSION = 'v1.0';
const ENTRY_INTENT = 'ai_recommend' as const;
const DRAFT_KEY = `eatwhat:questionnaire:draft:${QUESTIONNAIRE_VERSION}:${ENTRY_INTENT}`;
const DEBOUNCE_MS = 200;

type Answers = Record<string, string[]>;

// ========== P5-02A：AI 生成四阶段 Stepper ==========
type AiStage = 1 | 2 | 3 | 4;

const AI_STEP_META: ReadonlyArray<{ id: AiStage; label: string }> = [
  { id: 1, label: '接收偏好' },
  { id: 2, label: '生成候选' },
  { id: 3, label: '匹配规则' },
  { id: 4, label: '排序优化' },
] as const;

// Stepper 推进节奏（毫秒）：进入 loading 态后按时间推进 aiStage，并
// 与 expandLevel（1→3→5）联动，给用户真实的"AI 一步步在做"感。
const STEPPER_PHASES: ReadonlyArray<{ at: number; stage: AiStage; expand?: 1 | 3 | 5 }> = [
  { at: 0, stage: 1 }, // 立即：阶段 1 标记已完成（偏好已传入），进入阶段 2
  { at: 600, stage: 2, expand: 1 },
  { at: 1400, stage: 3, expand: 3 }, // 进入阶段3时骨架从1张→3张
  { at: 2400, stage: 4, expand: 5 }, // 进入阶段4时骨架3张→5张（若还在加载则给足预览感）
] as const;

interface LoadState {
  loading: boolean;
  error: string | null;
}

interface RecommendationsLoadState {
  loading: boolean;
  error: string | null;
  items: readonly RecommendationItem[] | null;
}

function loadDraft(): Answers {
  if (typeof window === 'undefined') return {};
  try {
    const raw = window.localStorage.getItem(DRAFT_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw) as unknown;
    if (!parsed || typeof parsed !== 'object') return {};
    const out: Answers = {};
    for (const [k, v] of Object.entries(parsed as Record<string, unknown>)) {
      if (!Array.isArray(v)) continue;
      const list = v.filter((x): x is string => typeof x === 'string');
      if (list.length > 0) out[k] = list;
    }
    return out;
  } catch {
    return {};
  }
}

function saveDraft(answers: Answers): void {
  if (typeof window === 'undefined') return;
  try {
    window.localStorage.setItem(DRAFT_KEY, JSON.stringify(answers));
  } catch {
    // ignore (private mode / quota)
  }
}

const COVERED_DIMENSION_LABEL: Record<string, string> = {
  meal_period: '用餐时段',
  appetite: '饱腹程度',
  avoidances: '忌口',
  tastes: '口味',
  budget: '预算',
  explicit_food_preference: '明确想吃',
  max_distance_m: '搜索距离',
};

const BUDGET_FIT_LABEL: Record<string, string> = {
  fits: '预算内',
  uncertain: '未标注',
  unlikely: '可能不符',
};

export default function Recommend() {
  const [answers, setAnswers] = useState<Answers>(() => loadDraft());
  const [result, setResult] = useState<QuestionnaireRecomputeResult | null>(null);
  const [loadState, setLoadState] = useState<LoadState>({ loading: true, error: null });
  const [recState, setRecState] = useState<RecommendationsLoadState>({
    loading: false,
    error: null,
    items: null,
  });
  // P5-02：动态追问会话。非空 = 进入 follow_up 态；回答后若 final 则清空并出 5 卡。
  const [followUpSession, setFollowUpSession] = useState<SessionStateResponseV1 | null>(null);
  // P5-02：正在回答 follow_up（answer HTTP 请求中）—— 禁用选项 UI。
  const [answerLoading, setAnswerLoading] = useState(false);
  // D-008：1→3→5 渐进展示。初始只展示 priority=1，点击展开到 3，再点击到 5。
  const [expandLevel, setExpandLevel] = useState<1 | 3 | 5>(1);
  // P5-02A：AI Stepper 当前 active 阶段（1..4）。loading=true 时由 useEffect 按节奏推进。
  const [aiStage, setAiStage] = useState<AiStage>(4);
  const debounceTimer = useRef<number | null>(null);
  const fetchSeq = useRef(0);
  const recAbort = useRef<AbortController | null>(null);
  const stepperTimers = useRef<number[]>([]);

  // ---- P5-02A：Stepper 推进器。recState.loading=true 时启动；结束/卸载时清理。----
  useEffect(() => {
    if (!recState.loading) return;
    // 清理上一轮残留 timers（理论不会有，保险）
    stepperTimers.current.forEach((t) => window.clearTimeout(t));
    stepperTimers.current = [];

    const pushTimer = (t: number) => {
      stepperTimers.current.push(t);
    };

    // 进入 loading：立即 reset aiStage=1，expandLevel=1（和真实结果返回时保持一致）
    setAiStage(1);
    setExpandLevel(1);

    for (const phase of STEPPER_PHASES) {
      if (phase.at === 0) {
        // t=0 的 phase 直接走（阶段 1 立即标完成，active 立刻切到 2 开始 pulse）
        if (phase.expand) setExpandLevel(phase.expand);
        continue;
      }
      const t = window.setTimeout(() => {
        setAiStage(phase.stage);
        if (phase.expand) setExpandLevel(phase.expand);
      }, phase.at);
      pushTimer(t);
    }

    return () => {
      stepperTimers.current.forEach((t) => window.clearTimeout(t));
      stepperTimers.current = [];
    };
  }, [recState.loading]); // eslint-disable-line react-hooks/exhaustive-deps

  // ---- 本地草稿持久化：answers 变化即写 localStorage ----
  useEffect(() => {
    saveDraft(answers);
  }, [answers]);

  // ---- POST /next 核心逻辑（支持防抖；按 fetchSeq 丢弃过期响应） ----
  const fetchNext = useCallback(
    async (currentAnswers: Answers, { signal }: { signal?: AbortSignal } = {}) => {
      const mySeq = ++fetchSeq.current;
      setLoadState((s) => ({ ...s, loading: true, error: null }));
      try {
        const next = await api.questionnaireNext(
          {
            entry_intent: ENTRY_INTENT,
            questionnaire_version: QUESTIONNAIRE_VERSION,
            answers_by_question_id: currentAnswers,
          },
          { signal },
        );
        if (mySeq !== fetchSeq.current) return; // 过期响应丢弃

        // Step: 如果服务端说某些 qid invalidated → 从 answers 里清掉
        if (next.invalidated_answer_ids.length > 0) {
          setAnswers((prev) => {
            const drop = new Set(next.invalidated_answer_ids);
            const nextAnswers: Answers = {};
            for (const [qid, vals] of Object.entries(prev)) {
              if (!drop.has(qid)) nextAnswers[qid] = vals;
            }
            return nextAnswers;
          });
        }

        setResult(next);
        setLoadState({ loading: false, error: null });
      } catch (err) {
        if (mySeq !== fetchSeq.current) return;
        const message =
          err instanceof ApiError
            ? err.message
            : err instanceof Error
              ? err.message
              : '网络错误';
        setLoadState({ loading: false, error: message });
      }
    },
    [],
  );

  // ---- 首调：进入页面立即重算 ----
  useEffect(() => {
    const controller = new AbortController();
    void fetchNext(answers, { signal: controller.signal });
    return () => controller.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ---- answers 变化防抖重算 ----
  useEffect(() => {
    if (debounceTimer.current !== null) window.clearTimeout(debounceTimer.current);
    debounceTimer.current = window.setTimeout(() => {
      void fetchNext(answers);
    }, DEBOUNCE_MS);
    return () => {
      if (debounceTimer.current !== null) window.clearTimeout(debounceTimer.current);
    };
  }, [answers, fetchNext]);

  // ---- 题目 UI 交互：单选/多选 ----
  const toggleOption = useCallback(
    (question: QuestionBankItem, value: string) => {
      setAnswers((prev) => {
        const cur = prev[question.question_id] ?? [];
        if (question.question_type === 'single_choice') {
          // 点击同 value 反选（允许空—— answers 里没这个 qid 就是空）
          return { ...prev, [question.question_id]: cur[0] === value ? [] : [value] };
        }
        // 多选
        const set = new Set(cur);
        if (set.has(value)) set.delete(value);
        else set.add(value);
        return { ...prev, [question.question_id]: Array.from(set) };
      });
    },
    [],
  );

  // ---- 进入结果态：P5-02 动态会话（start → 最多 3 轮 follow_up → final），失败回退 P2 一次性生成 ----
  const generateRecommendations = useCallback(async () => {
    if (recAbort.current) recAbort.current.abort();
    const controller = new AbortController();
    recAbort.current = controller;
    setFollowUpSession(null);
    setAnswerLoading(false);
    setRecState({ loading: true, error: null, items: null });
    try {
      // 先尝试 P5：动态会话（新流程）
      let finalItems: readonly RecommendationItem[] | null = null;
      try {
        const startResp = await api.recommendationsSessionStart(
          {
            entry_intent: ENTRY_INTENT,
            questionnaire_version: QUESTIONNAIRE_VERSION,
            answers_by_question_id: answers,
          },
          { signal: controller.signal },
        );
        if (startResp.stage === 'final') {
          finalItems = startResp.candidates ?? null;
        } else {
          // follow_up：保存会话，交给 follow_up UI 分支
          setRecState({ loading: false, error: null, items: null });
          setFollowUpSession(startResp);
          return;
        }
      } catch (sessionErr) {
        // fallback：旧版 POST /recommendations（P2 兼容兜底）
        if (controller.signal.aborted) throw sessionErr;
        const legacy = await api.recommendationsGenerate(
          {
            entry_intent: ENTRY_INTENT,
            questionnaire_version: QUESTIONNAIRE_VERSION,
            answers_by_question_id: answers,
          },
          { signal: controller.signal },
        );
        finalItems = legacy;
      }
      if (finalItems == null) {
        throw new Error('G-08 违规：服务端未返回候选');
      }
      // 按 priority 升序兜底（后端已保证严格递增，但前端 sort 不影响稳定性）
      const sorted = [...finalItems].sort((a, b) => a.priority - b.priority);
      setFollowUpSession(null);
      setRecState({ loading: false, error: null, items: sorted });
      setExpandLevel(1); // D-008：进入结果态时重置为只展示 1 张
    } catch (err) {
      if (controller.signal.aborted) return;
      const message =
        err instanceof ApiError
          ? err.message
          : err instanceof Error
            ? err.message
            : '推荐失败，请稍后重试';
      setFollowUpSession(null);
      setRecState({ loading: false, error: message, items: null });
    }
  }, [answers]);

  // ---- P5-02：回答一道 follow_up ----
  const answerFollowUp = useCallback(
    async (optionValue: string) => {
      const sess = followUpSession;
      if (!sess || !sess.question || answerLoading) return;
      const controller = new AbortController();
      setAnswerLoading(true);
      try {
        const resp = await api.recommendationsSessionAnswer(
          sess.session_id,
          {
            question_id: sess.question.question_id,
            selected_option_value: optionValue,
          },
          { signal: controller.signal },
        );
        if (resp.stage === 'final') {
          const items = resp.candidates;
          if (!items) throw new Error('G-08 违规：final 阶段未返回 candidates');
          const sorted = [...items].sort((a, b) => a.priority - b.priority);
          setFollowUpSession(null);
          setRecState({ loading: false, error: null, items: sorted });
          setExpandLevel(1);
        } else {
          // 下一轮 follow_up
          setFollowUpSession(resp);
        }
      } catch (err) {
        const message =
          err instanceof ApiError
            ? err.message
            : err instanceof Error
              ? err.message
              : '回答提交失败，请稍后重试';
        setRecState((s) => ({ ...s, error: message }));
      } finally {
        setAnswerLoading(false);
      }
    },
    [answerLoading, followUpSession],
  );

  const nextAction: NextAction = result?.next_action ?? 'proceed_questionnaire';
  const progress = result?.progress ?? 0;
  const coveredDims: DimensionCoverage[] = result?.covered_dimensions ?? [];
  const nextQuestions = result?.next_questions ?? [];
  const requiredMissingIds = useMemo(
    () => new Set(result?.required_not_yet_answered_question_ids ?? []),
    [result],
  );
  const isComplete = result?.is_complete ?? false;
  const isResultView = recState.items !== null;

  const handleResetDraft = useCallback(
    (e?: FormEvent | React.MouseEvent) => {
      e?.preventDefault?.();
      setAnswers({});
      setRecState({ loading: false, error: null, items: null });
      setFollowUpSession(null);
      setAnswerLoading(false);
      if (typeof window !== 'undefined') window.localStorage.removeItem(DRAFT_KEY);
    },
    [],
  );

  const handleBackToQuestionnaire = useCallback(() => {
    setRecState({ loading: false, error: null, items: null });
    setFollowUpSession(null);
    setAnswerLoading(false);
  }, []);

  return (
    <div className="page-shell questionnaire-page">
      <p className="eyebrow">推荐流程 · 自适应问卷</p>
      <h1>
        {isResultView
          ? '为你准备了 5 个候选'
          : followUpSession
            ? '再回答 1–2 个小问题，就能出推荐了'
            : '决定一下，大概想吃什么'}
      </h1>

      {isResultView ? (
        <p className="microcopy" style={{ marginBottom: 'var(--space-4)' }}>
          以下推荐来源于确定性规则引擎（P2-02）；越靠前的越匹配你刚刚回答的偏好。
          {followUpSession?.final_reason ? `（生成来源：${followUpSession.final_reason}）` : ''}
        </p>
      ) : followUpSession ? (
        <p className="microcopy" style={{ marginBottom: 'var(--space-4)' }}>
          为了让推荐更贴近你当下的口味，我们会补充问几个维度。最多 3 轮，随时可提前得出结果。
        </p>
      ) : (
        <p className="microcopy" style={{ marginBottom: 'var(--space-4)' }}>
          只需回答当前显示的 1–2 题；我们会根据你的选择决定"要不要继续追问"。
          所有回答只用来生成食物推荐，不做账号追踪。
        </p>
      )}

      {/* 只有问卷态才显示进度条 */}
      {!isResultView && !followUpSession ? (
        <div className="progress-shell" aria-hidden={false}>
          <div className="progress-header">
            <span>问卷进度</span>
            <span className="progress-num">{progress}%</span>
          </div>
          <div
            className="progress-bar"
            role="progressbar"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={progress}
            aria-label="问卷进度百分比"
          >
            <div className="progress-fill" style={{ width: `${progress}%` }} />
          </div>
        </div>
      ) : null}

      {/* Covered dimensions tags（问卷态+结果态都显示；结果态方便用户对照"为什么推荐这些"） */}
      <div className="dim-tags" aria-label="维度覆盖情况">
        {coveredDims.map((d) => (
          <span
            key={d.field_name}
            className={`dim-tag ${d.covered ? 'is-covered' : 'is-missing'}`}
          >
            {COVERED_DIMENSION_LABEL[d.field_name] ?? d.field_name}
            <em>{d.covered ? '已收集' : '未收集'}</em>
          </span>
        ))}
      </div>

      {/* Error banner（问卷态） */}
      {!isResultView && loadState.error ? (
        <div
          className="notice error-notice"
          role="alert"
          style={{ marginBlock: 'var(--space-4)' }}
        >
          <strong>暂时拿不到下一题：</strong>
          <span>{loadState.error}</span>
          <button
            type="button"
            className="button button-secondary"
            onClick={() => void fetchNext(answers)}
            style={{ marginLeft: 'var(--space-3)' }}
          >
            重试
          </button>
        </div>
      ) : null}

      {/* Error banner（推荐请求失败） */}
      {recState.error ? (
        <div
          className="notice error-notice"
          role="alert"
          style={{ marginBlock: 'var(--space-4)' }}
        >
          <strong>推荐生成失败：</strong>
          <span>{recState.error}</span>
          <button
            type="button"
            className="button button-secondary"
            onClick={() => void generateRecommendations()}
            style={{ marginLeft: 'var(--space-3)' }}
          >
            再试一次
          </button>
        </div>
      ) : null}

      {recState.loading ? (
        // ============ P5-02A：AI 等待骨架 UI ============
        <div className="ai-wait-shell" aria-busy="true" aria-label="正在生成推荐，请稍候">
          {/* 四阶段 Stepper */}
          <ol className="ai-stepper" role="list" aria-label="推荐生成进度">
            {AI_STEP_META.map((step) => {
              // aiStage=N 表示：已完成到第 N 步（step.id <= N → is-done，打勾）
              // 若 N < 4 → active = N+1（下一个正在进行）
              // 若 N = 4 → active = 4（全部都 done 但仍在等最终 HTTP 返回，4 显示 active pulse）
              const done = step.id <= aiStage;
              const active = aiStage < 4 ? step.id === aiStage + 1 : step.id === 4;
              const classNames = ['ai-step'];
              if (done) classNames.push('is-done');
              if (active) classNames.push('is-active');
              return (
                <li key={step.id} className={classNames.join(' ')} aria-current={active ? 'step' : undefined}>
                  <div className="ai-step-indicator">
                    {done ? '✓' : step.id}
                  </div>
                  <div className="ai-step-label">{step.label}</div>
                </li>
              );
            })}
          </ol>

          {/* 5 张骨架卡，1→3→5 节奏与 expandLevel 同步（真实结果出卡节奏一致） */}
          {[1, 2, 3, 4, 5].map((priority) => (
            <div
              key={priority}
              className="skeleton-card"
              data-priority={priority}
              hidden={priority > expandLevel}
              aria-hidden={priority > expandLevel}
            >
              <div className="skeleton-header">
                <div className="skeleton-row skeleton-rank" />
                <div className="skeleton-row skeleton-name" />
                <div className="skeleton-row skeleton-tag" />
              </div>
              <div className="skeleton-row skeleton-summary-line-1" />
              <div className="skeleton-row skeleton-summary-line-2" />
              <div className="skeleton-signals">
                <div className="skeleton-row skeleton-chip" />
                <div className="skeleton-row skeleton-chip" />
                <div className="skeleton-row skeleton-chip" />
              </div>
              <div className="skeleton-row skeleton-note" />
            </div>
          ))}
        </div>
      ) : followUpSession && followUpSession.stage === 'follow_up' && followUpSession.question ? (
        // ============ P5-02：AI 动态追问态 ============
        <section className="follow-up-shell" aria-label="动态追问">
          <header className="follow-up-header">
            <p className="follow-up-rounds">
              AI 追问 · 第 {Math.min(followUpSession.rounds_completed + 1, followUpSession.max_rounds)} / {followUpSession.max_rounds} 轮
            </p>
            <p className="follow-up-purpose">{followUpSession.question.purpose_zh}</p>
          </header>
          <FollowUpQuestionCard
            question={followUpSession.question}
            answering={answerLoading}
            onPick={answerFollowUp}
          />
          <div className="q-footer">
            <button
              type="button"
              className="button button-secondary"
              data-testid="follow-up-back"
              onClick={handleBackToQuestionnaire}
            >
              返回修改问卷答案
            </button>
          </div>
        </section>
      ) : !isResultView ? (
        // ============ 问卷态 ============
        <form onSubmit={handleResetDraft} className="questionnaire-form" noValidate>
          {nextQuestions.length === 0 ? (
            <div className="notice" style={{ marginBlock: 'var(--space-4)' }}>
              {isComplete
                ? '必填题已经答完啦，也可以继续填"口味/忌口/饿不饿"优化推荐结果；或者直接点下方按钮看推荐。'
                : '当前没有需要继续答的题（加载中…）'}
            </div>
          ) : (
            nextQuestions.map((q) => {
              const cur = answers[q.question_id] ?? [];
              const isRequired = requiredMissingIds.has(q.question_id);
              return (
                <fieldset
                  key={q.question_id}
                  className="q-card"
                  data-question-id={q.question_id}
                >
                  <legend>
                    <span className="q-title">
                      {q.title_zh}
                      {isRequired ? (
                        <span className="q-required" aria-label="必填题">
                          *
                        </span>
                      ) : null}
                    </span>
                    <span className="q-hint">
                      {q.question_type === 'single_choice'
                        ? '单选 · 再次点击取消'
                        : '多选 · 可勾多项'}
                    </span>
                  </legend>
                  <div className={`q-options ${q.question_type}`}>
                    {q.options.map((opt) => {
                      const selected = cur.includes(opt.value);
                      return (
                        <button
                          key={opt.value}
                          type="button"
                          className={`q-option ${selected ? 'is-selected' : ''}`}
                          onClick={() => toggleOption(q, opt.value)}
                          aria-pressed={selected}
                        >
                          <span className="q-option-label">{opt.label_zh}</span>
                        </button>
                      );
                    })}
                  </div>
                </fieldset>
              );
            })
          )}

          {loadState.loading ? (
            <div className="loading-row" aria-live="polite">
              正在根据你的选择更新下一题…
            </div>
          ) : null}

          <div className="q-footer">
            {nextAction === 'proceed_generate_recommendations' ? (
              <button
                type="button"
                className="button button-primary button-large"
                data-testid="goto-recommendations"
                aria-disabled={recState.loading}
                onClick={() => void generateRecommendations()}
              >
                {recState.loading
                  ? '正在生成推荐…'
                  : '去看推荐结果（必填已收集）'}
              </button>
            ) : (
              <div className="q-footer-hint">
                {requiredMissingIds.size > 0
                  ? `还有 ${requiredMissingIds.size} 道必答题需要完成`
                  : '回答完这些题目后，还可以再优化口味与忌口。'}
              </div>
            )}
            <button
              type="submit"
              className="button button-secondary"
              style={{ justifySelf: 'end' }}
            >
              清空草稿重来
            </button>
          </div>
        </form>
      ) : (
        // ============ 推荐结果态 ============
        <section
          className="recommendations-list"
          data-testid="recommendations-list"
          aria-label="Top5 推荐列表"
        >
          {recState.items!.map((item) => (
            <article
              key={item.food_code}
              className="recommendation-card"
              data-food-code={item.food_code}
              data-priority={item.priority}
              hidden={item.priority > expandLevel}
            >
              <header className="recommendation-card-header">
                <span className="recommendation-rank" aria-label={`第 ${item.priority} 推荐`}>
                  #{item.priority}
                </span>
                <h2 className="recommendation-name" data-testid={`rec-name-${item.priority}`}>
                  {item.food_code}
                </h2>
                {item.budget_fit ? (
                  <span className={`tag tag-budget tag-budget-${item.budget_fit}`}>
                    {BUDGET_FIT_LABEL[item.budget_fit] ?? item.budget_fit}
                  </span>
                ) : null}
              </header>
              <p className="recommendation-summary">{item.reason.summary_zh}</p>
              <ul className="recommendation-signals" aria-label="匹配信号">
                {item.reason.matched_signals.map((s, idx) => (
                  <li key={`${s}-${idx}`} className="signal-chip">
                    {s}
                  </li>
                ))}
              </ul>
              {item.budget_fit_note_zh ? (
                <p className="budget-note" aria-label="预算说明">
                  {item.budget_fit_note_zh}
                </p>
              ) : null}
            </article>
          ))}

          {expandLevel < 5 && (
            <button
              type="button"
              className="button button-secondary button-large expand-recommendations"
              data-testid="expand-recommendations"
              onClick={() => setExpandLevel(expandLevel === 1 ? 3 : 5)}
            >
              {expandLevel === 1 ? '查看更多推荐（3/5）' : '查看全部推荐（5/5）'}
            </button>
          )}

          <div className="q-footer">
            <button
              type="button"
              className="button button-secondary"
              data-testid="back-to-questionnaire"
              onClick={handleBackToQuestionnaire}
            >
              返回修改答案
            </button>
            <button
              type="button"
              className="button button-primary button-large"
              data-testid="reset-from-result"
              onClick={handleResetDraft}
            >
              重新来一次（清空草稿）
            </button>
          </div>
        </section>
      )}
    </div>
  );
}

// ============ P5-02：FollowUpQuestionCard ============

interface FollowUpQuestionCardProps {
  readonly question: FollowUpQuestionV1;
  readonly answering: boolean;
  readonly onPick: (value: string) => void | Promise<void>;
}

/**
 * 纯展示组件：把一道 follow_up 题渲染为 pill 单选按钮组。
 * - 复用 q-card / q-opt 的视觉体系，减少新增 CSS 表面积；
 * - 回答中 disabled + opacity，避免双提交；
 * - onClick 直接调 onPick(value)，外层统一转 async。
 */
function FollowUpQuestionCard({ question, answering, onPick }: FollowUpQuestionCardProps) {
  return (
    <div
      className="q-card follow-up-question-card"
      role="radiogroup"
      aria-label={question.title_zh}
      aria-disabled={answering}
    >
      <legend className="q-title follow-up-q-title">
        <span className="q-required follow-up-q-badge">追问</span>
        {question.title_zh}
      </legend>
      <div className="q-opts follow-up-opts">
        {question.options.map((opt) => (
          <button
            key={opt.value}
            type="button"
            role="radio"
            aria-checked={false}
            aria-disabled={answering}
            disabled={answering}
            className={`q-opt opt-pill follow-up-opt ${answering ? 'is-disabled' : ''}`}
            data-testid={`follow-up-option-${opt.value}`}
            onClick={(e: React.MouseEvent<HTMLButtonElement>) => {
              e.preventDefault();
              if (answering) return;
              void onPick(opt.value);
            }}
          >
            <span className="q-opt-val">{opt.label_zh}</span>
          </button>
        ))}
      </div>
    </div>
  );
}
