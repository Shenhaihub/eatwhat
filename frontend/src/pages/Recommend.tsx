/**
 * P2-03B：推荐流程问卷页
 * - 路由：/recommend
 * - 入口意图固定 ai_recommend（P2 只做推荐问卷）；其他四入口在 P3/P4 接入。
 * - 数据流：
 *     1) 首调：POST /questionnaire/next（answers={}） → 拿 next_questions 渲染
 *     2) 用户答题 → 改 answers_by_question_id → 防抖 200ms 再 POST /next
 *     3) 响应里 invalidated_answer_ids 非空 → 从 answers 里移除这些 qid（UI 也清）
 *     4) 顶部 progress 条 + covered_dimensions 标签
 *     5) 底部按钮：
 *        - next_action = proceed_questionnaire && required_not_yet_answered 有 → 显示"还有必答题"
 *        - next_action = proceed_generate_recommendations → 显示"去看推荐结果"按钮
 *
 * - 草稿持久化：localStorage key=`eatwhat:questionnaire:draft:v1.0:ai_recommend`；
 *   页面刷新后自动还原（注意：还原后首调仍然 POST /next 让服务端重算 invalidated）。
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { FormEvent } from 'react';

import { api, ApiError } from '../services/api/client';
import type {
  DimensionCoverage,
  NextAction,
  QuestionBankItem,
  QuestionnaireRecomputeResult,
} from '../services/api/types';

const QUESTIONNAIRE_VERSION = 'v1.0';
const ENTRY_INTENT = 'ai_recommend' as const;
const DRAFT_KEY = `eatwhat:questionnaire:draft:${QUESTIONNAIRE_VERSION}:${ENTRY_INTENT}`;
const DEBOUNCE_MS = 200;

type Answers = Record<string, string[]>;

interface LoadState {
  loading: boolean;
  error: string | null;
}

function loadDraft(): Answers {
  if (typeof window === 'undefined') return {};
  try {
    const raw = window.localStorage.getItem(DRAFT_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw) as unknown;
    if (!parsed || typeof parsed !== 'object') return {};
    // 简单形状收敛：只保留 Record<string, string[]>
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
};

export default function Recommend() {
  const [answers, setAnswers] = useState<Answers>(() => loadDraft());
  const [result, setResult] = useState<QuestionnaireRecomputeResult | null>(null);
  const [loadState, setLoadState] = useState<LoadState>({ loading: true, error: null });
  const debounceTimer = useRef<number | null>(null);
  const fetchSeq = useRef(0);

  // ---- 本地草稿持久化：answers 变化即写 localStorage ----
  useEffect(() => {
    saveDraft(answers);
  }, [answers]);

  // ---- POST /next 核心逻辑（支持防抖；按 fetchSeq 丢弃过期响应） ----
  const fetchNext = useCallback(async (currentAnswers: Answers, { signal }: { signal?: AbortSignal } = {}) => {
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

      // Step: 如果服务端说某些 qid invalidated → 从 answers 里清掉（UI 也会随之清空）
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
      const message = err instanceof ApiError ? err.message : err instanceof Error ? err.message : '网络错误';
      setLoadState({ loading: false, error: message });
    }
  }, []);

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
          // 单选出勤：点击同 value 反选（允许空——允许"还没选"这个状态，answers 里没这个 qid 就是空）
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

  const nextAction: NextAction = result?.next_action ?? 'proceed_questionnaire';
  const progress = result?.progress ?? 0;
  const coveredDims: DimensionCoverage[] = result?.covered_dimensions ?? [];
  const nextQuestions = result?.next_questions ?? [];
  const requiredMissingIds = useMemo(
    () => new Set(result?.required_not_yet_answered_question_ids ?? []),
    [result],
  );
  const isComplete = result?.is_complete ?? false;

  const handleResetDraft = (e: FormEvent): void => {
    e.preventDefault();
    setAnswers({});
    if (typeof window !== 'undefined') window.localStorage.removeItem(DRAFT_KEY);
  };

  return (
    <div className="page-shell questionnaire-page">
      <p className="eyebrow">推荐流程 · 自适应问卷</p>
      <h1>决定一下，大概想吃什么</h1>
      <p className="microcopy" style={{ marginBottom: 'var(--space-4)' }}>
        只需回答当前显示的 1–2 题；我们会根据你的选择决定"要不要继续追问"。
        所有回答只用来生成食物推荐，不做账号追踪。
      </p>

      {/* Progress bar */}
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

      {/* Covered dimensions tags */}
      <div className="dim-tags" aria-label="维度覆盖情况">
        {coveredDims.map((d) => (
          <span key={d.field_name} className={`dim-tag ${d.covered ? 'is-covered' : 'is-missing'}`}>
            {COVERED_DIMENSION_LABEL[d.field_name] ?? d.field_name}
            <em>{d.covered ? '已收集' : '未收集'}</em>
          </span>
        ))}
      </div>

      {/* Error banner */}
      {loadState.error ? (
        <div className="notice error-notice" role="alert" style={{ marginBlock: 'var(--space-4)' }}>
          <strong>暂时拿不到下一题：</strong>
          <span>{loadState.error}</span>
          <button type="button" className="button button-secondary" onClick={() => void fetchNext(answers)} style={{ marginLeft: 'var(--space-3)' }}>
            重试
          </button>
        </div>
      ) : null}

      {/* Questions */}
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
              <fieldset key={q.question_id} className="q-card" data-question-id={q.question_id}>
                <legend>
                  <span className="q-title">
                    {q.title_zh}
                    {isRequired ? <span className="q-required" aria-label="必填题">*</span> : null}
                  </span>
                  <span className="q-hint">
                    {q.question_type === 'single_choice' ? '单选 · 再次点击取消' : '多选 · 可勾多项'}
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

        {/* Loading overlay indicator */}
        {loadState.loading ? <div className="loading-row" aria-live="polite">正在根据你的选择更新下一题…</div> : null}

        {/* Next action footer */}
        <div className="q-footer">
          {nextAction === 'proceed_generate_recommendations' ? (
            <button
              type="button"
              className="button button-primary button-large"
              data-testid="goto-recommendations"
              onClick={() => {
                // P2-03B 只把按钮样式接好；真正的推荐结果页在 P2-04。
                // 这里先给一个轻量的 toast-like 提示占位
                setLoadState((s) => ({ ...s, error: null }));
                // eslint-disable-next-line no-alert
                window.alert('推荐生成会在 P2-04 接入 /api/v1/recommendations 时上线；现在前端问卷接入 OK ✅');
              }}
            >
              去看推荐结果（必填已收集）
            </button>
          ) : (
            <div className="q-footer-hint">
              {requiredMissingIds.size > 0 ? `还有 ${requiredMissingIds.size} 道必答题需要完成` : '回答完这些题目后，还可以再优化口味与忌口。'}
            </div>
          )}
          <button type="submit" className="button button-secondary" style={{ justifySelf: 'end' }}>
            清空草稿重来
          </button>
        </div>
      </form>
    </div>
  );
}
