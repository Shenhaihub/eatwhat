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
  NextAction,
  QuestionBankItem,
  QuestionnaireRecomputeResult,
  RecommendationItem,
} from '../services/api/types';
import '../styles/recommendations.css';

const QUESTIONNAIRE_VERSION = 'v1.0';
const ENTRY_INTENT = 'ai_recommend' as const;
const DRAFT_KEY = `eatwhat:questionnaire:draft:${QUESTIONNAIRE_VERSION}:${ENTRY_INTENT}`;
const DEBOUNCE_MS = 200;

type Answers = Record<string, string[]>;

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
  // D-008：1→3→5 渐进展示。初始只展示 priority=1，点击展开到 3，再点击到 5。
  const [expandLevel, setExpandLevel] = useState<1 | 3 | 5>(1);
  const debounceTimer = useRef<number | null>(null);
  const fetchSeq = useRef(0);
  const recAbort = useRef<AbortController | null>(null);

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

  // ---- 进入结果态：POST /recommendations ----
  const generateRecommendations = useCallback(async () => {
    if (recAbort.current) recAbort.current.abort();
    const controller = new AbortController();
    recAbort.current = controller;
    setRecState({ loading: true, error: null, items: null });
    try {
      const items = await api.recommendationsGenerate(
        {
          entry_intent: ENTRY_INTENT,
          questionnaire_version: QUESTIONNAIRE_VERSION,
          answers_by_question_id: answers,
        },
        { signal: controller.signal },
      );
      // 按 priority 升序兜底（后端已保证严格递增，但前端 sort 不影响稳定性）
      const sorted = [...items].sort((a, b) => a.priority - b.priority);
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
      setRecState({ loading: false, error: message, items: null });
    }
  }, [answers]);

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
      if (typeof window !== 'undefined') window.localStorage.removeItem(DRAFT_KEY);
    },
    [],
  );

  const handleBackToQuestionnaire = useCallback(() => {
    setRecState({ loading: false, error: null, items: null });
  }, []);

  return (
    <div className="page-shell questionnaire-page">
      <p className="eyebrow">推荐流程 · 自适应问卷</p>
      <h1>{isResultView ? '为你准备了 5 个候选' : '决定一下，大概想吃什么'}</h1>

      {!isResultView ? (
        <p className="microcopy" style={{ marginBottom: 'var(--space-4)' }}>
          只需回答当前显示的 1–2 题；我们会根据你的选择决定"要不要继续追问"。
          所有回答只用来生成食物推荐，不做账号追踪。
        </p>
      ) : (
        <p className="microcopy" style={{ marginBottom: 'var(--space-4)' }}>
          以下推荐来源于确定性规则引擎（P2-02）；越靠前的越匹配你刚刚回答的偏好。
        </p>
      )}

      {/* 只有问卷态才显示进度条 */}
      {!isResultView ? (
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

      {!isResultView ? (
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
