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
import { useNavigate, useSearchParams } from 'react-router';

import { api, ApiError } from '../services/api/client';
import type {
  AiQuotaInfo,
  DimensionCoverage,
  FeedbackSubmitRequest,
  FeedbackType,
  FeedbackTypeOption,
  FollowUpQuestionV1,
  MergedPrefField,
  NextAction,
  QuestionBankItem,
  QuestionnaireRecomputeResult,
  RecommendationItem,
  RecommendationsGenerateResponseV1,
  SessionStateResponseV1,
} from '../services/api/types';
import { describeFinalReason } from '../lib/sourceBadge';
import { useAuth } from '../context/AuthContext';
import '../styles/recommendations.css';
import { track } from '../lib/track';

/**
 * 自动跳 follow_up：把 "答案 key" 映射到 "follow_up 维度判别器"。
 * 只有 answers 里已有 seed 预填（q02_explicit_food / q07_cuisine_preference）时，
 * 才会尝试把 follow_up 里重复问的题自动答掉。
 * 每个判别器返回的是「answers 里对应的字符串候选值数组（key first，再中文别名）」。
 */
type FollowUpDimMatcher = (q: FollowUpQuestionV1) => readonly string[] | null;

const CUISINE_KEY_ALIASES: Readonly<Record<string, readonly string[]>> = {
  japanese: ['japanese', '日料', '日式', '日式料理', '寿司', '日韩', 'japanese_korean'],
  korean: ['korean', '韩餐', '韩式', '韩式料理', '韩国料理', '日韩', 'japanese_korean'],
  sichuan: ['sichuan', '川菜', '川湘菜', '川味', '麻辣', 'spicy', '麻辣火锅'],
  cantonese: ['cantonese', '粤菜', '粤式', '广式', '港式'],
  chinese: ['chinese', '中餐', '中式', '家常菜', '中国菜', '北方家常', '南方家常'],
  western: ['western', '西餐', '西式', '欧式'],
  southeast_asian: ['southeast_asian', '东南亚', '泰式', '越式', '新加坡'],
  vegetarian: ['vegetarian', '素食', '素菜', '斋'],
  spicy_hotpot: ['spicy_hotpot', '火锅', '麻辣火锅', '串串', '只要辣', 'spicy'],
  barbecue: ['barbecue', '烧烤', 'BBQ', '烤肉', '韩式烤肉'],
};
function q07CuisineAliases(prefKey: string): readonly string[] {
  const direct = CUISINE_KEY_ALIASES[prefKey];
  return direct ? [prefKey, ...direct] : [prefKey];
}

/** follow_up 的 question 是在问"菜系风格"吗？返回 answers 里的候选值（key + 中文别名） */
const cuisineMatcher: FollowUpDimMatcher = (q) => {
  const hay = `${q.question_id}\n${q.purpose_zh}\n${q.title_zh}`.toLowerCase();
  const isCuisine =
    hay.includes('菜系') ||
    hay.includes('cuisine') ||
    hay.includes('风格') ||
    hay.includes('国家') ||
    hay.includes('地方');
  if (!isCuisine) return null;
  const key = 'q07_cuisine_preference';
  return q07CuisineAliases(key as any); // 真正取值时从 answers 取 key 的实际值再 alias
};
/** follow_up 的 question 是在问"明确想吃什么菜 / food_code 级具体菜"吗？ */
const explicitFoodMatcher: FollowUpDimMatcher = (q) => {
  const hay = `${q.question_id}\n${q.purpose_zh}\n${q.title_zh}`.toLowerCase();
  const isExplicit =
    hay.includes('明确想吃') ||
    hay.includes('q02') ||
    hay.includes('food_code') ||
    hay.includes('具体想吃') ||
    hay.includes('今天想吃什么');
  if (!isExplicit) return null;
  return ['q02_explicit_food']; // 实际值从 answers['q02_explicit_food'] 取
};
const DIM_MATCHERS: readonly FollowUpDimMatcher[] = [cuisineMatcher, explicitFoodMatcher];

/**
 * 一道 follow_up 能否自动回答？能 → 返回匹配到的 option.value（注意是 option.value，后端要求传这个）；
 * 不能 → 返回 null。策略：宁可不自动跳（保守）也不跳错。
 *
 * 匹配策略（按优先级）：
 *   Pass 1 — 严格等值匹配（value 或 label_zh 完全相等）
 *   Pass 2 — 模糊/子集匹配：answer 候选值是 option.value 的子串 或 反之；
 *            用于应对后端把 japanese+korean 合并成 japanese_korean / 日韩 这种场景
 *   Pass 3 — 中文别名子串匹配：answer 的中文别名是否出现在 label_zh 里
 */
function findAutoAnswerOption(
  answers: Answers,
  question: FollowUpQuestionV1,
): string | null {
  if (!question?.options?.length) return null;

  // 遍历维度判别器：先找到"这道题属于哪个 answers 维度"
  let prefValsFromAnswers: readonly string[] = [];
  for (const matcher of DIM_MATCHERS) {
    const tag = matcher(question);
    if (!tag) continue;
    // tag 里的第 0 位是 answers 的 key（q07_cuisine_preference / q02_explicit_food）
    const answersKey = tag[0];
    const arr = answers[answersKey];
    if (!Array.isArray(arr) || arr.length === 0) return null;
    const actualFirst = arr[0];
    if (!actualFirst) return null;
    // q07：把 answers 的 key（japanese）展开成别名候选；q02 直接用原值
    if (answersKey === 'q07_cuisine_preference') {
      prefValsFromAnswers = q07CuisineAliases(actualFirst);
    } else {
      prefValsFromAnswers = [actualFirst];
    }
    break;
  }
  if (!prefValsFromAnswers.length) return null;

  const lowerCandidates = new Set(prefValsFromAnswers.map((s) => s.toLowerCase().trim()));
  const zhCandidates = new Set(prefValsFromAnswers.map((s) => s.trim()));

  // Pass 1：严格等值匹配（value → label_zh）
  for (const opt of question.options) {
    const v = opt.value.toLowerCase().trim();
    if (lowerCandidates.has(v)) return opt.value;
  }
  for (const opt of question.options) {
    if (zhCandidates.has(opt.label_zh.trim())) return opt.value;
  }

  // Pass 2：模糊子集匹配
  // 2a) candidate 是 option.value 的子串 或 反之（如 candidate='japanese' 是 option.value='japanese_korean' 的子串）
  for (const cand of lowerCandidates) {
    if (cand.length < 2) continue;
    for (const opt of question.options) {
      const ov = opt.value.toLowerCase().trim();
      if (ov.includes(cand) || cand.includes(ov)) return opt.value;
    }
  }
  // 2b) candidate 是 label_zh 的子串 或 反之（如 candidate='日韩' 出现在 label_zh='日韩（寿司/冷面/炸鸡）'）
  for (const cand of zhCandidates) {
    if (cand.length < 2) continue;
    for (const opt of question.options) {
      const ol = opt.label_zh.trim();
      if (ol.includes(cand) || cand.includes(ol)) return opt.value;
    }
  }

  // Pass 3：label_zh 中的每个字是否覆盖 candidate 中的每个字（中文子序列匹配，防止「韩」出现在「日韩」里被漏掉）
  // 仅对 q07 菜系维度启用（由 candidates 里是否含中文字符自动判定）
  // 算法：cand 中每个字符都必须在 label_zh 中按顺序出现（允许中间插入别的字符）
  for (const cand of zhCandidates) {
    if (!/[\u4e00-\u9fff]/.test(cand)) continue; // 只处理含中文的候选
    for (const opt of question.options) {
      const ol = opt.label_zh;
      let idx = 0;
      for (const ch of cand) {
        const found = ol.indexOf(ch, idx);
        if (found === -1) break;
        idx = found + 1;
      }
      if (idx >= cand.length) return opt.value;
    }
  }

  return null;
}

const QUESTIONNAIRE_VERSION = 'v1.0';
const ENTRY_INTENT = 'ai_recommend' as const;
const DRAFT_KEY = `eatwhat:questionnaire:draft:${QUESTIONNAIRE_VERSION}:${ENTRY_INTENT}`;
const DEBOUNCE_MS = 200;

/**
 * 前端本地的 qid → maps_to.field_name 映射（与 v1.0 题库 1:1）。
 * 用于在 display_if invalidated 某些题（例如 q07_cuisine_preference）时，
 * 仍然能根据用户 answers 里实际填的值来修正"维度覆盖情况"标签显示，
 * 避免用户看到"韩料/菜系未收集"但实际上已经通过社区跳转 prefill 了 q07。
 */
const QID_TO_FIELD_NAME_V1: Readonly<Record<string, string>> = {
  q01_meal_period: 'meal_period',
  q02_explicit_food: 'explicit_food_preference',
  q03_appetite_level: 'appetite',
  q04_taste_preference: 'tastes',
  q05_dietary_restriction: 'avoidances',
  q06_budget_range: 'budget',
  q07_cuisine_preference: 'cuisine_preferences',
};

type Answers = Record<string, string[]>;

// ========== P5-02A：分级延迟显示策略 ==========
// 短延迟：loading < 300ms 不显示骨架屏（避免快速请求的闪烁）
const SKELETON_MIN_DELAY_MS = 300;
// 长延迟：loading > 15s 显示超时重试选项
const TIMEOUT_THRESHOLD_MS = 15000;
// 追问加载：短延迟阈值
const FOLLOW_UP_SKELETON_DELAY_MS = 200;

// ========== P5-02A：AI 生成四阶段 Stepper ==========
type AiStage = 1 | 2 | 3 | 4;

const AI_STEP_META: ReadonlyArray<{ id: AiStage; label: string; activeLabel: string }> = [
  { id: 1, label: '接收偏好', activeLabel: '正在整理你的选择…' },
  { id: 2, label: '生成候选', activeLabel: '正在生成候选美食…' },
  { id: 3, label: '匹配规则', activeLabel: '正在匹配口味规则…' },
  { id: 4, label: '排序优化', activeLabel: '正在优化排序…' },
] as const;

// 追问阶段文案
const FOLLOW_UP_LOADING_MESSAGES = [
  '正在思考下一道问题…',
  '正在生成追问…',
  '正在补充你的偏好…',
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

// 手动保存画像状态：idle=初始 / saving=保存中(按钮禁用) / saved=成功 / error=失败
type ManualSaveStatus = 'idle' | 'saving' | 'saved' | 'error';

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
  const nav = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const auth = useAuth();  // P5-04A：登录态 + isAuthenticated 判断

  // C：URL 预填 ?seed_food=xxx / ?prefill_cuisine=yyy（从社区 Feed / 主题区 / 按钮跳转而来）。
  // 规则：仅当对应维度草稿为「empty 或 undecided」时才塞；
  //       用户已有明确草稿就保留用户选择，不粗暴覆盖。
  //       写过一次后就移除 URL 参数（刷新不会再次触发）。
  const seedFood = searchParams.get('seed_food') ?? '';
  const prefillCuisine = searchParams.get('prefill_cuisine') ?? '';
  const draft = loadDraft();
  const initial = draft;
  // 记录哪些 key 是 URL seed 预填的 —— 用 useRef 持久化，防止 re-render 时丢失
  // 它们是"种子偏好"，不是用户通过问卷答的题，不受 questionnaire display_if 失效规则影响。
  // 后端可能因 display_if 判这些题为 invalidated，但前端必须保留它们，
  // 否则 session/start 时后端 rule_answers 拿不到对应维度 → follow_up 还会重复问。
  const seedPrefillKeysRef = useRef<Set<string>>(new Set());
  if (seedFood.trim() && !initial['q02_explicit_food']?.length) {
    initial['q02_explicit_food'] = [seedFood.trim()];
    seedPrefillKeysRef.current.add('q02_explicit_food');
  }
  if (prefillCuisine.trim() && !initial['q07_cuisine_preference']?.length) {
    initial['q07_cuisine_preference'] = [prefillCuisine.trim()];
    seedPrefillKeysRef.current.add('q07_cuisine_preference');
  }
  const [answers, setAnswers] = useState<Answers>(initial);
  // C：seed_food / prefill_cuisine 漏斗埋点：
  // 只有真的被写进了 answers（对应维度原来为空）才打"应用成功"事件，
  // 如果用户已有草稿 → 不会替换 → 不打事件。
  useEffect(() => {
    const appliedSeed = Array.isArray(initial['q02_explicit_food']) && initial['q02_explicit_food'][0] === seedFood.trim()
      ? seedFood.trim()
      : null;
    const appliedCuisine = Array.isArray(initial['q07_cuisine_preference']) && initial['q07_cuisine_preference'][0] === prefillCuisine.trim()
      ? prefillCuisine.trim()
      : null;
    if (appliedSeed || appliedCuisine) {
      track('recommend.seed_prefill_applied', {
        seed_food: appliedSeed,
        prefill_cuisine: appliedCuisine,
        had_draft: Object.keys(draft).length > 0,
      });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  // 消费后清 URL 参数（保留其它参数），保证刷新不重复"预填覆盖"
  useEffect(() => {
    const hasSeed = searchParams.has('seed_food');
    const hasCuisine = searchParams.has('prefill_cuisine');
    if (!hasSeed && !hasCuisine) return;
    const next = new URLSearchParams(searchParams);
    if (hasSeed) next.delete('seed_food');
    if (hasCuisine) next.delete('prefill_cuisine');
    setSearchParams(next, { replace: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
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
  // P5-04A：AI 增益开关。默认关=免费规则引擎；勾上需登录 + 扣每日 3 次额度。
  const [preferAiGain, setPreferAiGain] = useState(false);
  // P5-07：AI 额度展示（最新一次响应带回的配额；仅登录态 preferAiGain=true 时在结果卡顶部展示）
  const [aiQuota, setAiQuota] = useState<AiQuotaInfo | null>(null);
  // P5-08：最终推荐的来源标记。followUpSession 在进入结果态时会被清空，所以单独存一份。
  //   - "ai_gain" / "rule_engine_fallback_ai_fail" / "legacy_rule_engine" / null
  const [resultFinalReason, setResultFinalReason] = useState<string | null>(null);
  // P7-07：P6-02 冷启动画像合并的 banner 状态（非空 = 命中画像 + 有实际合并字段）。
  // 来源：'start' = session/start（新流程），'answer' = session/answer（自动跳过 follow_up 或手动 answer 后返回），'legacy' = 直连 POST /recommendations（兜底流程）。
  const [mergedPrefBanner, setMergedPrefBanner] = useState<{
    merged: readonly MergedPrefField[];
    from: 'start' | 'legacy' | 'answer';
    dismissed: boolean;
  } | null>(null);
  const debounceTimer = useRef<number | null>(null);
  const fetchSeq = useRef(0);
  const recAbort = useRef<AbortController | null>(null);
  const stepperTimers = useRef<number[]>([]);
  // P5-02A：分级延迟显示控制
  const [showSkeleton, setShowSkeleton] = useState(false);
  const [showFollowUpSkeleton, setShowFollowUpSkeleton] = useState(false);
  const [timeoutReached, setTimeoutReached] = useState(false);
  const loadingStartAt = useRef<number>(0);
  const skeletonDelayTimer = useRef<number | null>(null);
  const timeoutCheckTimer = useRef<number | null>(null);

  // P5-02：内部帮助：清理 stepperTimers，避免结果出卡时未完成的 timer 偷偷把 expandLevel 改回去
  const clearStepperTimers = useCallback(() => {
    if (!stepperTimers.current.length) return;
    stepperTimers.current.forEach((t) => window.clearTimeout(t));
    stepperTimers.current = [];
  }, []);

  // P5-02A：清理延迟显示相关 timers
  const clearLoadingTimers = useCallback(() => {
    if (skeletonDelayTimer.current !== null) {
      window.clearTimeout(skeletonDelayTimer.current);
      skeletonDelayTimer.current = null;
    }
    if (timeoutCheckTimer.current !== null) {
      window.clearTimeout(timeoutCheckTimer.current);
      timeoutCheckTimer.current = null;
    }
  }, []);

  // P5-02A：启动分级延迟显示流程
  const startLoadingDisplay = useCallback(() => {
    loadingStartAt.current = Date.now();
    setShowSkeleton(false);
    setTimeoutReached(false);
    clearLoadingTimers();

    // 短延迟后显示骨架屏
    skeletonDelayTimer.current = window.setTimeout(() => {
      setShowSkeleton(true);
    }, SKELETON_MIN_DELAY_MS);

    // 长延迟后显示超时选项
    timeoutCheckTimer.current = window.setTimeout(() => {
      setTimeoutReached(true);
    }, TIMEOUT_THRESHOLD_MS);
  }, [clearLoadingTimers]);

  // P5-02A：停止加载显示
  const stopLoadingDisplay = useCallback(() => {
    clearLoadingTimers();
    setShowSkeleton(false);
    setTimeoutReached(false);
  }, [clearLoadingTimers]);

  // P5-02A：追问加载短延迟显示
  const startFollowUpLoading = useCallback(() => {
    setShowFollowUpSkeleton(false);
    const t = window.setTimeout(() => {
      setShowFollowUpSkeleton(true);
    }, FOLLOW_UP_SKELETON_DELAY_MS);
    return () => window.clearTimeout(t);
  }, []);

  // ---- P7-07：Banner 交互 ----
  const [prefBannerOpen, setPrefBannerOpen] = useState(false);
  useEffect(() => {
    // 每次 banner 新到来时，默认折叠防止挡住问卷
    setPrefBannerOpen(false);
  }, [mergedPrefBanner?.merged.length, mergedPrefBanner?.from]);

  // ---- P5-02A：AI Stepper 推进器。recState.loading=true 时启动；结束/卸载时清理。----
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
        // 但 URL seed 预填的 key 是"种子偏好"，不应被问卷 display_if 失效规则清掉。
        // 关键：如果所有 invalidated key 都是 seed key（实际不会删任何东西），
        // 就不调 setAnswers，避免创建新对象引用 → answers useEffect → fetchNext → 无限循环。
        if (next.invalidated_answer_ids.length > 0) {
          const keepKeys = seedPrefillKeysRef.current;
          const wouldRemove = next.invalidated_answer_ids.filter((qid) => !keepKeys.has(qid));
          if (wouldRemove.length > 0) {
            const dropSet = new Set(wouldRemove);
            setAnswers((prev) => {
              const nextAnswers: Answers = {};
              for (const [qid, vals] of Object.entries(prev)) {
                if (dropSet.has(qid)) continue;
                nextAnswers[qid] = vals;
              }
              return nextAnswers;
            });
          }
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
    // P5-02A：启动分级延迟显示流程
    startLoadingDisplay();
    // 新一轮生成先清 banner；再依据 startResp / legacy 返回是否需要展示。
    setMergedPrefBanner(null);

    // P5-04A：AI 增益开关登录拦截。
    // （后端也会再 401 一次，前端先挡掉节省一次请求）
    if (preferAiGain && !auth.isAuthenticated) {
      try {
        // 保存 next path，supabase 登录后回跳回到 /recommend
        if (typeof window !== 'undefined') {
          window.localStorage.setItem('eatwhat:next_path', '/recommend');
        }
      } catch {
        /* 忽略 storage 异常（cookie 禁写等隐私设置） */
      }
      clearStepperTimers();
      setRecState({ loading: false, error: null, items: null });
      nav('/login', { replace: false });
      return;
    }

    try {
      // 先尝试 P5：动态会话（新流程）
      let finalItems: readonly RecommendationItem[] | null = null;

      try {
        const startResp = await api.recommendationsSessionStart(
          {
            entry_intent: ENTRY_INTENT,
            questionnaire_version: QUESTIONNAIRE_VERSION,
            answers_by_question_id: answers,
            prefer_ai_gain: preferAiGain,
          },
          { signal: controller.signal },
        );
        // P7-07：冷启动画像合并命中 → 填 banner
        if (Array.isArray(startResp.merged_pref_fields) && startResp.merged_pref_fields.length > 0) {
          setMergedPrefBanner({ merged: startResp.merged_pref_fields, from: 'start', dismissed: false });
        }
        if (startResp.stage === 'final') {
          finalItems = startResp.candidates ?? null;
          setResultFinalReason(startResp.final_reason ?? null);
          setResultAutowrite(startResp.autowrite ?? null);
          // P5-07：会话也回传额度，存一份供结果区展示
          if (startResp.ai_quota) setAiQuota(startResp.ai_quota);
        } else {
          // follow_up：**先尝试"自动回答过滤器"**，避免 seed 预填过 q07/q02 后，
          // follow_up 还重复问"菜系风格/明确想吃"让用户再选一次的不合理体验。
          // 循环：直到"找到不能自动跳过的题（显示给用户）"或"直接推进到 final"或"达到 max_rounds 防无限"。
          let current = startResp;
          let autoAnswered = 0;
          while (
            current.stage === 'follow_up' &&
            current.question &&
            autoAnswered < current.max_rounds
          ) {
            const autoOpt = findAutoAnswerOption(answers, current.question);
            if (!autoOpt) break; // 找不到命中项 → 显示给用户
            try {
              // 埋点：记录自动跳过哪道 follow_up（方便以后统计"seed 预填"省了多少重复题）
              track('recommend.follow_up_auto_skipped', {
                question_id: current.question.question_id,
                title_zh: current.question.title_zh,
                purpose_zh: current.question.purpose_zh,
                answered_option_value: autoOpt,
                answers_has_seed_food: Array.isArray(answers['q02_explicit_food']) && answers['q02_explicit_food'].length > 0,
                answers_has_prefill_cuisine: Array.isArray(answers['q07_cuisine_preference']) && answers['q07_cuisine_preference'].length > 0,
              });
              const ans = await api.recommendationsSessionAnswer(
                current.session_id,
                {
                  question_id: current.question.question_id,
                  selected_option_value: autoOpt,
                },
                { signal: controller.signal },
              );
              autoAnswered += 1;
              if (ans.stage === 'final') {
                current = ans;
                break;
              }
              current = ans;
            } catch (e) {
              // 自动回答失败（网络/后端 4xx）不应该阻塞：直接退出自动回答链，
              // 回退到"把当前这道 follow_up 展示给用户自己选"。
              break;
            }
          }
          if (current.stage === 'final') {
            // 自动跳过 1 道或多道 follow_up 后，直接进入最终结果态
            const items = current.candidates;
            if (!items) throw new Error('G-08 违规：final 阶段未返回 candidates');
            finalItems = items;
            setResultFinalReason(current.final_reason ?? null);
            setResultAutowrite(current.autowrite ?? null);
            if (current.ai_quota) setAiQuota(current.ai_quota);
            if (Array.isArray(current.merged_pref_fields) && current.merged_pref_fields.length > 0) {
              setMergedPrefBanner({ merged: current.merged_pref_fields, from: 'answer', dismissed: false });
            }
          } else {
            // follow_up：不能自动跳过了 → 保存会话，交给 follow_up UI 分支让用户自己选
            clearStepperTimers();
            setRecState({ loading: false, error: null, items: null });
            setResultFinalReason(null);
            setFollowUpSession(current);
            return;
          }
        }
      } catch (sessionErr) {
        // fallback：旧版 POST /recommendations（P2 兼容兜底）
        if (controller.signal.aborted) throw sessionErr;
        const legacy = await api.recommendationsGenerate(
          {
            entry_intent: ENTRY_INTENT,
            questionnaire_version: QUESTIONNAIRE_VERSION,
            answers_by_question_id: answers,
            prefer_ai_gain: preferAiGain,
          },
          { signal: controller.signal },
        );
        // 兼容响应体：新格式 { items, merged_pref_fields }，若仍收到数组（降级情况）也能工作
        const items: readonly RecommendationItem[] = Array.isArray(legacy)
          ? legacy
          : Array.isArray((legacy as { items?: unknown }).items)
            ? ((legacy as { items: readonly RecommendationItem[] }).items)
            : [];
        const merged: readonly MergedPrefField[] = Array.isArray((legacy as { merged_pref_fields?: unknown }).merged_pref_fields)
          ? ((legacy as { merged_pref_fields: readonly MergedPrefField[] }).merged_pref_fields)
          : [];
        const awRaw = (legacy as { autowrite?: unknown }).autowrite;
        const aw: AutoWriteInfo | null =
          awRaw && typeof awRaw === 'object' ? (awRaw as AutoWriteInfo) : null;
        if (merged.length > 0) {
          setMergedPrefBanner({ merged, from: 'legacy', dismissed: false });
        }
        finalItems = items;
        setResultAutowrite(aw);
        // legacy：有 prefer_ai_gain=true 时会有 used_ai/final_reason/ai_quota；否则保留规则默认文案
        const l = legacy as { used_ai?: boolean; final_reason?: string | null; ai_quota?: AiQuotaInfo };
        setResultFinalReason(l.final_reason ?? 'legacy_rule_engine');
        if (l.ai_quota) setAiQuota(l.ai_quota);
      }
      if (finalItems == null || finalItems.length === 0) {
        throw new Error('G-08 违规：服务端未返回候选');
      }
      // 按 priority 升序兜底（后端已保证严格递增，但前端 sort 不影响稳定性）
      const sorted = [...finalItems].sort((a, b) => a.priority - b.priority);
      setFollowUpSession(null);
      setRecState({ loading: false, error: null, items: sorted });
      // P5-02A：停止加载显示
      stopLoadingDisplay();
      // A2 FIX D-008：1→3→5 渐进展示被误设为全展开
      // 原因：STEPPER_PHASES 的 timer 可能还有未 fire 的（比如 2400ms 的 expand=5），
      // 出卡后它们仍会把 expandLevel 改上去。我们在"真正出卡前"先 clear stepperTimers，
      // 再把 expandLevel 重置为 1，确保用户看到的是只展开 #1。
      clearStepperTimers();
      setExpandLevel(1);
    } catch (err) {
      if (controller.signal.aborted) return;
      const message =
        err instanceof ApiError
          ? err.message
          : err instanceof Error
            ? err.message
            : '推荐失败，请稍后重试';
      setFollowUpSession(null);
      setResultFinalReason(null);
      clearStepperTimers();
      // P5-02A：停止加载显示
      stopLoadingDisplay();
      setRecState({ loading: false, error: message, items: null });
    }
  }, [answers, auth.isAuthenticated, clearStepperTimers, nav, preferAiGain, stopLoadingDisplay]);

  // ---- P5-02：回答一道 follow_up ----
  const answerFollowUp = useCallback(
    async (optionValue: string) => {
      const sess = followUpSession;
      if (!sess || !sess.question || answerLoading) return;
      const controller = new AbortController();
      setAnswerLoading(true);
      // P5-02A：启动追问加载短延迟显示
      const cancelFollowUpLoading = startFollowUpLoading();
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
          setResultFinalReason(resp.final_reason ?? null);
          setResultAutowrite(resp.autowrite ?? null);
          if (resp.ai_quota) setAiQuota(resp.ai_quota);
          setRecState({ loading: false, error: null, items: sorted });
          // P5-02A：停止追问加载显示
          setShowFollowUpSkeleton(false);
          // A2 FIX D-008：追问到 final 时也清理 stepperTimers 再把 expand 重置为 1
          clearStepperTimers();
          setExpandLevel(1);
        } else {
          // 下一轮 follow_up
          setFollowUpSession(resp);
          // P5-02A：下一轮追问已到达，隐藏骨架
          setShowFollowUpSkeleton(false);
        }
      } catch (err) {
        const message =
          err instanceof ApiError
            ? err.message
            : err instanceof Error
              ? err.message
              : '回答提交失败，请稍后重试';
        setRecState((s) => ({ ...s, error: message }));
        setShowFollowUpSkeleton(false);
      } finally {
        setAnswerLoading(false);
        cancelFollowUpLoading();
      }
    },
    [answerLoading, followUpSession, startFollowUpLoading],
  );

  const nextAction: NextAction = result?.next_action ?? 'proceed_questionnaire';
  const progress = result?.progress ?? 0;
  const rawCoveredDims: DimensionCoverage[] = result?.covered_dimensions ?? [];

  // P7：前端按用户当前实际 answers 修正 covered_dimensions 显示。
  // 问卷层的 covered_dimensions 只对"通过 display_if 激活的题"计算，
  // 但社区跳转 prefill_cuisine（q07 被 q02≠undecided 而 invalidated）等场景，
  // 实际上答案已经会被 rule_answers 接收并生效。为避免显示误导（"cuisine_preferences 未收集"），
  // 这里前端用本地 qid→field 映射做二次覆盖。
  const coveredDims: DimensionCoverage[] = useMemo(() => {
    const base: Record<string, boolean> = {};
    for (const d of rawCoveredDims) base[d.field_name] = d.covered;
    for (const qid of Object.keys(answers)) {
      const vals = answers[qid];
      if (!vals || vals.length === 0) continue;
      const field = QID_TO_FIELD_NAME_V1[qid];
      if (field) base[field] = true;
    }
    // 取两者 field_name 的并集（以 raw 中已有顺序 + 新增 7 维中缺失项补齐）
    const ordered = Array.from(
      new Set([
        ...rawCoveredDims.map((d) => d.field_name),
        ...Object.values(QID_TO_FIELD_NAME_V1),
        'max_distance_m',
        'cuisine_preferences',
      ]),
    );
    return ordered.map((field_name) => ({
      field_name,
      covered: Boolean(base[field_name]),
    }));
  }, [rawCoveredDims, answers]);

  const nextQuestions = result?.next_questions ?? [];
  const requiredMissingIds = useMemo(
    () => new Set(result?.required_not_yet_answered_question_ids ?? []),
    [result],
  );
  const isComplete = result?.is_complete ?? false;
  const isResultView = recState.items !== null;

  // P7-08：成功生成推荐（进入结果态）后清空 localStorage 草稿。
  // 目的：用户每次进入 /recommend 都从未答状态开始，避免"上次的答案"残留。
  // 只清存储不清内存 answers —— 结果页的交互（1→3→5 展开、反馈、保存画像）仍正常。
  // 用 useRef 防止结果态期间多次重渲染触发重复清空（幂等，无副作用）。
  const draftClearedForResultRef = useRef(false);
  useEffect(() => {
    if (isResultView && !draftClearedForResultRef.current) {
      if (typeof window !== 'undefined') window.localStorage.removeItem(DRAFT_KEY);
      draftClearedForResultRef.current = true;
    }
  }, [isResultView]);

  const handleResetDraft = useCallback(
    (e?: FormEvent | React.MouseEvent) => {
      e?.preventDefault?.();
      setAnswers({});
      setRecState({ loading: false, error: null, items: null });
      setFollowUpSession(null);
      setAnswerLoading(false);
      setResultFinalReason(null);
      if (typeof window !== 'undefined') window.localStorage.removeItem(DRAFT_KEY);
    },
    [],
  );

  const handleBackToQuestionnaire = useCallback(() => {
    setRecState({ loading: false, error: null, items: null });
    setFollowUpSession(null);
    setAnswerLoading(false);
    setResultFinalReason(null);
  }, []);

  // ---- P7-07c：Banner「去修改」滚动到对应 q 卡（问卷态滚动；follow_up 先回问卷；结果态给出提示） ----
  const scrollOrJumpToQuestion = useCallback(
    (qid: string) => {
      const findCard = () =>
        document.getElementById(`q-card-${CSS.escape(qid)}`) as HTMLElement | null;
      const highlight = (el: HTMLElement) => {
        el.dataset.prefJumpHighlight = '1';
        window.setTimeout(() => {
          delete el.dataset.prefJumpHighlight;
        }, 1800);
      };
      // 1) 问卷态 / 或已经能找到 DOM 卡片
      const first = findCard();
      if (first) {
        first.scrollIntoView({ behavior: 'smooth', block: 'center', inline: 'nearest' });
        highlight(first);
        return;
      }
      // 2) 正在 follow_up：切回问卷态（handleBackToQuestionnaire），等一小会儿再滚
      if (followUpSession?.stage === 'follow_up') {
        handleBackToQuestionnaire();
        window.setTimeout(() => {
          const el = findCard();
          if (el) {
            el.scrollIntoView({ behavior: 'smooth', block: 'center', inline: 'nearest' });
            highlight(el);
          }
        }, 900);
        return;
      }
      // 3) 结果态 / 没找到卡片 → 显示一条临时提示（banner 内）
      setPrefBannerTip(
        `当前不在问卷编辑页，无法跳到「${qid}」。请先点"返回修改问卷答案"或"清空草稿重来"回到问卷态后再点。`,
      );
    },
    [followUpSession, handleBackToQuestionnaire],
  );
  // 「去修改」跳转失败时的提示文案（Banner 下方展示）
  const [prefBannerTip, setPrefBannerTip] = useState<string | null>(null);
  useEffect(() => {
    if (!prefBannerTip) return;
    const t = window.setTimeout(() => setPrefBannerTip(null), 6000);
    return () => window.clearTimeout(t);
  }, [prefBannerTip]);

  // ---- P0 修复：结果页保存状态 + 手动保存 ----
  type AutoWriteInfo = NonNullable<RecommendationsGenerateResponseV1['autowrite']>;
  const [resultAutowrite, setResultAutowrite] = useState<AutoWriteInfo | null>(null);
  const [manualSaveStatus, setManualSaveStatus] = useState<ManualSaveStatus>('idle');
  const [manualSaveMessage, setManualSaveMessage] = useState<string | null>(null);
  // 新一轮生成时清掉旧的保存状态
  useEffect(() => {
    if (recState.loading) {
      setResultAutowrite(null);
      setManualSaveStatus('idle');
      setManualSaveMessage(null);
      setFeedbackOpen(false);
      setFeedbackStatus('idle');
      setFeedbackMessage(null);
    }
  }, [recState.loading]);

  // ---- 推荐结果页反馈入口（P7-08A） ----
  const [feedbackOpen, setFeedbackOpen] = useState(false);
  const [feedbackStatus, setFeedbackStatus] = useState<'idle' | 'submitting' | 'done' | 'error'>('idle');
  const [feedbackMessage, setFeedbackMessage] = useState<string | null>(null);
  const [feedbackType, setFeedbackType] = useState<FeedbackType | ''>('');
  const [feedbackContent, setFeedbackContent] = useState('');
  const [feedbackTypes, setFeedbackTypes] = useState<FeedbackTypeOption[] | null>(null);

  const loadFeedbackTypes = useCallback(async () => {
    if (feedbackTypes) return feedbackTypes;
    try {
      const types = await api.feedbackTypes();
      setFeedbackTypes(types);
      return types;
    } catch (e) {
      // 兜底：与后端 v1 保持一致
      const fallback: FeedbackTypeOption[] = [
        { key: 'bug_report', label: 'Bug / 异常问题', description: '页面报错、按钮没反应、加载卡住等' },
        { key: 'content_report', label: '推荐内容不对', description: '推荐结果不靠谱、AI 不可信、维度标签错误' },
        { key: 'feature_request', label: '建议 / 想要的功能', description: '希望增加的功能或流程优化建议' },
        { key: 'general', label: '其它反馈', description: '不属于以上任何一类的反馈' },
      ];
      setFeedbackTypes(fallback);
      return fallback;
    }
  }, [feedbackTypes]);

  const handleSubmitFeedback = useCallback(async () => {
    if (!feedbackType || feedbackContent.trim().length < 2) {
      setFeedbackStatus('error');
      setFeedbackMessage('请先选择反馈类型，并写至少 2 个字的说明～');
      return;
    }
    setFeedbackStatus('submitting');
    setFeedbackMessage(null);
    try {
      const top3 = (recState.items ?? []).slice(0, 3).map((i) => i.food_code).join('|');
      const payload: FeedbackSubmitRequest = {
        feedback_type: feedbackType,
        content: feedbackContent.trim(),
        page_url: typeof window !== 'undefined' ? window.location.href : null,
        context: top3
          ? {
              'recommended_foods_top3': top3,
              'final_reason': resultFinalReason ?? '',
              'entry_intent': ENTRY_INTENT,
            }
          : { 'final_reason': resultFinalReason ?? '', 'entry_intent': ENTRY_INTENT },
      };
      const res = await api.feedbackSubmit(payload);
      setFeedbackStatus('done');
      setFeedbackMessage(res.message ?? '反馈已收到，感谢！我们会尽快处理。');
      setFeedbackContent('');
      setFeedbackType('');
      // 3 秒后自动收起面板
      window.setTimeout(() => {
        setFeedbackOpen(false);
        setFeedbackStatus('idle');
        setFeedbackMessage(null);
      }, 2800);
    } catch (e) {
      setFeedbackStatus('error');
      setFeedbackMessage(e instanceof Error ? e.message : '提交反馈失败，请稍后再试');
    }
  }, [feedbackType, feedbackContent, recState.items, resultFinalReason]);

  const handleManualSavePreference = useCallback(async () => {
    if (manualSaveStatus === 'saving') return;
    setManualSaveStatus('saving');
    setManualSaveMessage(null);
    try {
      // 1) 构造快照：直接把当前 answers 翻译到标准字段；字典版本沿用题目 v1.0
      const snapshot: Record<string, unknown> = {
        // 带上当前 answers_by_question_id（与后端写入的 snapshot_jsonb 结构保持一致）
        ai_follow_up_answers: {},
      };
      // 逐字段映射（与 questionnaire_to_rule 中字段名对齐）
      const DIM_TO_FIELD: Record<string, string> = {
        q01_meal_period: 'meal_period',
        q02_explicit_food: 'explicit_food_preference',
        q03_budget: 'budget',
        q04_tastes: 'tastes',
        q05_avoidances: 'avoidances',
        q06_appetite: 'appetite',
        q07_cuisine_preference: 'cuisine_preferences',
      };
      for (const [qid, vals] of Object.entries(answers)) {
        const field = DIM_TO_FIELD[qid] ?? null;
        if (!field) continue;
        if (Array.isArray(vals) && vals.length === 0) continue;
        // 单选：取 0；多选：整个数组（tastes / avoidances / cuisine_preferences）
        if (field === 'tastes' || field === 'avoidances' || field === 'cuisine_preferences') {
          snapshot[field] = [...vals];
        } else if (vals.length >= 1) {
          snapshot[field] = vals[0];
        }
      }
      snapshot['_meta'] = { final_reason: resultFinalReason ?? 'frontend_manual_save' };
      await api.preferenceCreate({
        questionnaire_version: QUESTIONNAIRE_VERSION,
        snapshot,
      });
      setManualSaveStatus('saved');
      setManualSaveMessage('✓ 已手动保存到你的饮食偏好画像，去「设置→饮食偏好」可看时间轴。');
    } catch (e) {
      setManualSaveStatus('error');
      const msg = e instanceof Error ? e.message : '保存失败';
      setManualSaveMessage(msg);
    }
  }, [answers, manualSaveStatus, resultFinalReason]);

  const gotoSettingsPreference = useCallback(() => {
    // 用 React Router 原生 navigate，保证 ProtectedRoute 正确触发 & 组件重新挂载。
    // 传 searchParams ?tab=preference 与 Settings.TAB_ITEMS 里的 id 完全对应。
    nav('/settings?tab=preference');
  }, [nav]);

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

      {/* P7-07：冷启动画像合并命中的预填 Banner（可以手动 dismiss） */}
      {mergedPrefBanner && !mergedPrefBanner.dismissed ? (
        <div
          className="pref-merge-banner"
          role="status"
          aria-live="polite"
          style={{ marginTop: 'var(--space-3)', marginBottom: 'var(--space-4)' }}
        >
          <div className="pref-merge-banner__head">
            <div className="pref-merge-banner__title">
              <span className="pref-merge-banner__icon" aria-hidden>
                🎯
              </span>
              <span>
                我们已基于你的历史画像自动预填了{' '}
                <strong>{mergedPrefBanner.merged.length}</strong> 项（可手动修改）
              </span>
            </div>
            <button
              type="button"
              className="pref-merge-banner__close"
              aria-label="关闭提示"
              onClick={() =>
                setMergedPrefBanner((s) => (s ? { ...s, dismissed: true } : null))
              }
            >
              ✕
            </button>
          </div>
          <div className="pref-merge-banner__row">
            <button
              type="button"
              className="pref-merge-banner__toggle"
              aria-expanded={prefBannerOpen}
              onClick={() => setPrefBannerOpen((v) => !v)}
            >
              {prefBannerOpen ? '收起合并详情' : '查看合并详情'}
              <span aria-hidden>{prefBannerOpen ? '▴' : '▾'}</span>
            </button>
            <span className="pref-merge-banner__from">
              来源：{mergedPrefBanner.from === 'start' ? 'AI 动态会话' : '快速规则引擎'}
            </span>
          </div>
          {prefBannerOpen ? (
            <div className="pref-merge-banner__details" aria-label="合并明细">
              <ul className="pref-merge-banner__list">
                {mergedPrefBanner.merged.map((f) => {
                  // 1) 根据后端返回的 field 名，反查当前 nextQuestions 里的题目（映射：field_name → question）
                  const qFromField =
                    nextQuestions.find((q) => q.maps_to.field_name === f.field) ?? null;
                  // 2) 若有题目就用它的标题和 question_id，否则 fallback 到内部字段名
                  const qid = qFromField?.question_id ?? f.field;
                  const label = qFromField?.title_zh ?? f.field;
                  // 3) 翻译枚举值为中文标签（优先用题目的 options 做 value→label_zh 映射）
                  const optLabel = (v: unknown): string => {
                    const raw = String(v ?? '');
                    if (!raw) return '（未填）';
                    if (qFromField) {
                      const found = qFromField.options.find((o) => String(o.value) === raw);
                      if (found) return found.label_zh;
                    }
                    // 内置常见字段兜底（避免显示枚举英文代码）
                    const DIM_LABEL: Record<string, Record<string, string>> = {
                      meal_period: { breakfast: '早餐', lunch: '午餐', dinner: '晚餐', snack: '加餐/宵夜', any: '都可以' },
                      appetite: { light: '少量清淡', normal: '正常量', heavy: '要吃饱/重口', famished: '超级饿' },
                      budget: { cheap: '省钱/平价位', mid: '中档', treat: '犒劳一顿', any: '不限' },
                      explicit_food_preference: { meat_lover: '肉食党', veg_heavy: '多蔬菜', balanced: '均衡', light_cal: '低卡清淡' },
                    };
                    return DIM_LABEL[f.field]?.[raw] ?? raw;
                  };
                  // 4) 中文 kind 标签 + CSS 后缀（要与 global.css 已有的 3 个 --list_append / --scalar_override / --ai_follow_up 保持一致）
                  let kindLabel = '预填';
                  let kindClass: 'scalar_override' | 'list_append' | 'ai_follow_up' = 'scalar_override';
                  if (f.kind === 'single') {
                    kindLabel = '覆盖填充';
                    kindClass = 'scalar_override';
                  } else if (f.kind === 'list') {
                    kindLabel = '新增选项';
                    kindClass = 'list_append';
                  } else if (f.kind === 'ai_follow_up') {
                    kindLabel = 'AI 追问新增';
                    kindClass = 'ai_follow_up';
                  }
                  // 5) ai_follow_up 时把 added_keys 展开多条，其他 kind 就是单条
                  if (f.kind === 'ai_follow_up') {
                    const addedMap =
                      (f.added_items_map as unknown as Record<string, unknown> | null) ??
                      (typeof f.added_items === 'object' &&
                      f.added_items !== null &&
                      !Array.isArray(f.added_items)
                        ? (f.added_items as unknown as Record<string, unknown>)
                        : null);
                    const keys: readonly string[] = f.added_keys ?? [];
                    if (keys.length === 0) {
                      return (
                        <li key={`${f.field}::${f.kind}`} className="pref-merge-banner__item">
                          <div className="pref-merge-banner__q">
                            <span className={`pref-merge-banner__kind pref-merge-banner__kind--${kindClass}`}>
                              {kindLabel}
                            </span>
                            <span className="pref-merge-banner__qlabel">AI 追问维度合并</span>
                            <span className="pref-merge-banner__qid">(无对应问卷卡片)</span>
                          </div>
                          <div className="pref-merge-banner__values">
                            <div className="pref-merge-banner__after">
                              <span className="pref-merge-banner__tagafter">合并后</span>
                              <div className="pref-merge-banner__valuebox">
                                <span className="chip chip--accent">（无可见明细）</span>
                              </div>
                            </div>
                          </div>
                        </li>
                      );
                    }
                    return keys.map((k) => {
                      const v = addedMap?.[k] ?? '';
                      const vRepr = Array.isArray(v) ? v.map(optLabel).join('、') : optLabel(v);
                      return (
                        <li key={`${f.field}::${f.kind}::${k}`} className="pref-merge-banner__item">
                          <div className="pref-merge-banner__q">
                            <span className={`pref-merge-banner__kind pref-merge-banner__kind--${kindClass}`}>
                              {kindLabel}
                            </span>
                            <span className="pref-merge-banner__qlabel">{k}</span>
                            <span className="pref-merge-banner__qid">(AI 追问题，无对应卡片)</span>
                            <span className="pref-merge-banner__qspacer" />
                            <button
                              type="button"
                              className="pref-merge-banner__jumpto"
                              disabled
                              aria-disabled
                              title="此条由 AI 追问动态生成，不在当前问卷卡片里"
                            >
                              去修改 →
                            </button>
                          </div>
                          <div className="pref-merge-banner__values">
                            <div className="pref-merge-banner__before">
                              <span className="pref-merge-banner__tagbefore">合并前</span>
                              <div className="pref-merge-banner__valuebox"><em>（未填）</em></div>
                            </div>
                            <span className="pref-merge-banner__arrow" aria-hidden>→</span>
                            <div className="pref-merge-banner__after">
                              <span className="pref-merge-banner__tagafter">合并后</span>
                              <div className="pref-merge-banner__valuebox">
                                <span className="chip chip--accent">{vRepr || '（空）'}</span>
                              </div>
                            </div>
                          </div>
                        </li>
                      );
                    });
                  }
                  // 6) 普通 single/list
                  const beforeIsList = Array.isArray(f.before);
                  const afterIsList = Array.isArray(f.after);
                  return (
                    <li key={`${f.field}::${f.kind}`} className="pref-merge-banner__item">
                      <div className="pref-merge-banner__q">
                        <span className={`pref-merge-banner__kind pref-merge-banner__kind--${kindClass}`}>
                          {kindLabel}
                        </span>
                        <span className="pref-merge-banner__qlabel">{label}</span>
                        <span className="pref-merge-banner__qid">({qid})</span>
                        <span className="pref-merge-banner__qspacer" />
                        {qFromField ? (
                          <button
                            type="button"
                            className="pref-merge-banner__jumpto"
                            onClick={() => scrollOrJumpToQuestion(qid)}
                            aria-label={`跳到题目${label}`}
                          >
                            去修改 →
                          </button>
                        ) : (
                          <button
                            type="button"
                            className="pref-merge-banner__jumpto"
                            disabled
                            aria-disabled
                            title="当前问卷没有对应题目卡片（可能是后续扩展维度）"
                          >
                            去修改 →
                          </button>
                        )}
                      </div>
                      <div className="pref-merge-banner__values">
                        <div className="pref-merge-banner__before">
                          <span className="pref-merge-banner__tagbefore">合并前</span>
                          <div className="pref-merge-banner__valuebox">
                            {beforeIsList
                              ? (f.before as unknown[]).length === 0
                                ? <em>（未填）</em>
                                : (f.before as unknown[]).map((v, i) => (
                                    <span key={`${String(v)}-${i}`} className="chip chip--muted">{optLabel(v)}</span>
                                  ))
                              : f.before == null || String(f.before) === ''
                                ? <em>（未填）</em>
                                : <span className="chip chip--muted">{optLabel(f.before)}</span>}
                          </div>
                        </div>
                        <span className="pref-merge-banner__arrow" aria-hidden>→</span>
                        <div className="pref-merge-banner__after">
                          <span className="pref-merge-banner__tagafter">合并后</span>
                          <div className="pref-merge-banner__valuebox">
                            {afterIsList
                              ? (f.after as unknown[])
                                  .map((v, i) => (
                                    <span
                                      key={`${String(v)}-${i}`}
                                      className="chip chip--accent"
                                    >
                                      {optLabel(v)}
                                    </span>
                                  ))
                              : <span className="chip chip--accent">{optLabel(f.after)}</span>}
                          </div>
                        </div>
                      </div>
                    </li>
                  );
                })}
              </ul>
            </div>
          ) : null}
          {prefBannerTip ? (
            <div
              data-scroll-tip="pref-merge"
              className="pref-merge-banner__tip"
              role="note"
            >
              {prefBannerTip}
            </div>
          ) : (
            <div data-scroll-tip="pref-merge" hidden />
          )}
        </div>
      ) : null}

      {isResultView ? (
        (() => {
          const meta = describeFinalReason(resultFinalReason);

          // ===== P0 修复：顶部 Autowrite 状态 Banner（绿/黄/红/引导 4 态）
          // 设计位置：结果标题正下方、推荐卡片列表正上方（一进结果页就能看到）。
          // =====
          type BannerStyle = 'ok' | 'warn' | 'err' | 'guide';
          let style: BannerStyle = 'guide';
          let icon = '💾';
          // 判断优先级：手动保存结果 > 自动 autowrite > 引导态
          if (manualSaveStatus === 'saved' || resultAutowrite?.preference_saved) {
            style = 'ok';
            icon = '✅';
          } else if (
            manualSaveStatus === 'error' ||
            (resultAutowrite &&
              (resultAutowrite.logged_in === false ||
                resultAutowrite.preference_saved === false ||
                resultAutowrite.history_saved === false))
          ) {
            style = 'err';
            icon = '❌';
          } else if (resultAutowrite || manualSaveMessage) {
            style = 'warn';
            icon = '⚠️';
          }
          const BANNER_STYLE: Record<BannerStyle, React.CSSProperties> = {
            ok: {
              marginTop: 'var(--space-2)',
              marginBottom: 'var(--space-3)',
              padding: 'var(--space-3) var(--space-4)',
              borderRadius: 'var(--radius-md)',
              border: '1px solid color-mix(in oklab, var(--color-primary) 35%, var(--color-border))',
              background:
                'color-mix(in oklab, var(--color-primary) 12%, var(--color-surface))',
              fontSize: '0.95rem',
              lineHeight: 1.6,
            },
            warn: {
              marginTop: 'var(--space-2)',
              marginBottom: 'var(--space-3)',
              padding: 'var(--space-3) var(--space-4)',
              borderRadius: 'var(--radius-md)',
              border: '1px solid color-mix(in oklab, #e6a23c 35%, var(--color-border))',
              background: 'color-mix(in oklab, #f0c78a 12%, var(--color-surface))',
              fontSize: '0.95rem',
              lineHeight: 1.6,
            },
            err: {
              marginTop: 'var(--space-2)',
              marginBottom: 'var(--space-3)',
              padding: 'var(--space-3) var(--space-4)',
              borderRadius: 'var(--radius-md)',
              border: '1px solid color-mix(in oklab, #c0392b 35%, var(--color-border))',
              background: 'color-mix(in oklab, #e74c3c 12%, var(--color-surface))',
              fontSize: '0.95rem',
              lineHeight: 1.6,
            },
            guide: {
              marginTop: 'var(--space-2)',
              marginBottom: 'var(--space-3)',
              padding: 'var(--space-3) var(--space-4)',
              borderRadius: 'var(--radius-md)',
              border: '1px solid color-mix(in oklab, var(--color-accent) 35%, var(--color-border))',
              background: 'color-mix(in oklab, var(--color-accent) 10%, var(--color-surface))',
              fontSize: '0.95rem',
              lineHeight: 1.6,
            },
          };
          const titleText: Record<BannerStyle, string> = {
            ok: '已自动保存到你的饮食偏好画像与推荐历史',
            warn: '保存提醒',
            err: '自动写入偏好画像失败',
            guide: '保存这一次的偏好，下次进来会更懂你',
          };
          const defaultReason: Record<BannerStyle, string> = {
            ok: '',
            warn: '',
            err: '',
            guide: '点下面的按钮，把菜系/口味/预算等偏好记下来。后续推荐会自动基于历史画像预填。',
          };
          const reasonText = manualSaveMessage ?? resultAutowrite?.reason ?? defaultReason[style];
          // 手动保存按钮（非 ok 态都显示）
          const showSaveBtn =
            style !== 'ok' && manualSaveStatus !== 'saving';
          const saveBtnDisabled =
            manualSaveStatus === 'saving' || manualSaveStatus === 'saved';
          const autowriteBannerNode = (
            <div
              className="autowrite-banner"
              role="status"
              aria-live="polite"
              style={BANNER_STYLE[style]}
            >
              <div style={{ display: 'flex', gap: 'var(--space-2)', alignItems: 'flex-start' }}>
                <span aria-hidden style={{ fontSize: '1.1rem' }}>{icon}</span>
                <div style={{ flex: 1 }}>
                  <div style={{ fontWeight: 600, marginBottom: 'var(--space-1)' }}>
                    {titleText[style]}
                  </div>
                  {reasonText ? <div style={{ opacity: 0.9 }}>{reasonText}</div> : null}
                  {style === 'ok' && resultAutowrite?.preference_id ? (
                    <div style={{ marginTop: 'var(--space-1)', fontSize: '0.85rem', opacity: 0.8 }}>
                      偏好快照 ID：{resultAutowrite.preference_id.slice(0, 8)}… &nbsp;|&nbsp;
                      推荐历史 ID：{resultAutowrite.history_id?.slice(0, 8) ?? '—'}…
                    </div>
                  ) : null}
                  {/* 手动操作按钮区：保存 + 去设置页查看 */}
                  <div
                    style={{
                      marginTop: 'var(--space-3)',
                      display: 'flex',
                      gap: 'var(--space-2)',
                      flexWrap: 'wrap',
                    }}
                  >
                    {showSaveBtn ? (
                      <button
                        type="button"
                        className="button button-primary"
                        onClick={() => void handleManualSavePreference()}
                        disabled={saveBtnDisabled}
                        style={{ margin: 0 }}
                      >
                        {/* showSaveBtn 过滤后 manualSaveStatus 只能是 idle/error，按钮统一文案 */}
                        💾 保存到我的饮食偏好画像
                      </button>
                    ) : null}
                    {manualSaveStatus === 'saved' || style === 'ok' ? (
                      <button
                        type="button"
                        className="button button-secondary"
                        onClick={gotoSettingsPreference}
                        style={{ margin: 0 }}
                      >
                        去设置 → 查看偏好时间轴
                      </button>
                    ) : null}
                    {style === 'err' && resultAutowrite?.logged_in === false ? (
                      <span style={{ alignSelf: 'center', fontSize: '0.85rem', opacity: 0.8 }}>
                        💡 提示：未登录状态下只能本地手动保存；登录后会自动写入云端。
                      </span>
                    ) : null}
                  </div>
                </div>
              </div>
            </div>
          );

          return (
            <>
              {autowriteBannerNode}
              <div
                style={{
                  marginBottom: 'var(--space-3)',
                  display: 'flex',
                  flexWrap: 'wrap',
                  alignItems: 'center',
                  justifyContent: 'space-between',
                  gap: 'var(--space-2)',
                }}
              >
                <p className="microcopy" style={{ margin: 0 }}>
                  {meta.summaryText ??
                    '以下推荐来源于确定性规则引擎；越靠前的越匹配你刚刚回答的偏好。'}
                </p>
                {/* P5-07：AI 额度条（登录态 + 有 quota 就展示）。 */}
                {aiQuota && (aiQuota.user_limit > 0) ? (() => {
                  const remaining = Math.max(0, aiQuota.user_limit - aiQuota.user_used);
                  const pct = aiQuota.user_limit > 0 ? Math.min(100, Math.max(0, (aiQuota.user_used / aiQuota.user_limit) * 100)) : 0;
                  return (
                    <div
                      className="ai-quota-chip"
                      role="note"
                      aria-label={`今日 AI 增益：已使用 ${aiQuota.user_used}/${aiQuota.user_limit} 次，剩余 ${remaining} 次`}
                      style={{
                        display: 'inline-flex',
                        alignItems: 'center',
                        gap: 'var(--space-2)',
                        padding: '0.4rem 0.7rem',
                        borderRadius: 'var(--radius-md)',
                        border: '1px solid color-mix(in oklab, var(--color-primary) 35%, var(--color-border))',
                        background: 'color-mix(in oklab, var(--color-primary) 10%, var(--color-surface))',
                        fontSize: '0.82rem',
                        lineHeight: 1.4,
                      }}
                    >
                      <span aria-hidden style={{ fontSize: '0.95rem' }}>✨</span>
                      <span style={{ fontWeight: 600, color: 'var(--color-primary)' }}>
                        今日 AI 增益 {aiQuota.user_used}/{aiQuota.user_limit}
                      </span>
                      <span aria-hidden style={{ display: 'inline-block', width: 80, height: 6, borderRadius: 999, background: 'color-mix(in oklab, var(--color-primary) 22%, var(--color-border))', overflow: 'hidden', verticalAlign: 'middle' }}>
                        <span
                          aria-hidden
                          style={{
                            display: 'block',
                            width: `${pct}%`,
                            height: '100%',
                            background: 'var(--color-primary)',
                          }}
                        />
                      </span>
                      <span style={{ opacity: 0.8 }}>剩 {remaining} 次</span>
                    </div>
                  );
                })() : null}
              </div>
              <div
                style={{
                  marginBottom: 'var(--space-4)',
                  display: 'flex',
                  alignItems: 'center',
                  gap: 'var(--space-2)',
                  flexWrap: 'wrap',
                }}
              >
                <span
                  className={`source-badge source-badge--${meta.variant}`}
                  role="note"
                  aria-label={meta.accessibleLabel}
                >
                  {meta.label}
                </span>
              </div>
            </>
          );
        })()
      ) : followUpSession ? (
        <p className="microcopy" style={{ marginBottom: 'var(--space-4)' }}>
          为了让推荐更贴近你当下的口味，我们会补充问几个维度。最多 3 轮，随时可提前得出结果。
        </p>
      ) : (
        <p className="microcopy" style={{ marginBottom: 'var(--space-4)' }}>
          只需回答当前显示的 1 题；我们会根据你的选择决定"要不要继续追问"。
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
        // ============ P5-02A：AI 等待骨架 UI（分级延迟显示） ============
        showSkeleton ? (
          <div className="ai-wait-shell" aria-busy="true" aria-label="正在生成推荐，请稍候">
            {/* 动态阶段文案 */}
            <div className="ai-stage-message" role="status" aria-live="polite">
              {AI_STEP_META.find((s) => s.id === (aiStage < 4 ? aiStage + 1 : 4))?.activeLabel ?? '正在生成推荐…'}
            </div>
            
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

            {/* 超时重试选项 */}
            {timeoutReached ? (
              <div className="ai-timeout-notice" role="alert">
                <p>生成时间较长，你可以：</p>
                <div className="ai-timeout-actions">
                  <button
                    type="button"
                    className="button button-secondary"
                    onClick={() => {
                      // 继续等待：重置超时计时器
                      setTimeoutReached(false);
                      loadingStartAt.current = Date.now();
                      const t = window.setTimeout(() => setTimeoutReached(true), TIMEOUT_THRESHOLD_MS);
                      timeoutCheckTimer.current = t;
                    }}
                  >
                    继续等待
                  </button>
                  <button
                    type="button"
                    className="button button-primary"
                    onClick={() => {
                      // 重新生成
                      if (recAbort.current) recAbort.current.abort();
                      generateRecommendations();
                    }}
                  >
                    重新生成
                  </button>
                </div>
              </div>
            ) : null}
          </div>
        ) : (
          // 短延迟：不显示骨架屏，显示极简加载提示
          <div className="ai-wait-minimal" aria-busy="true">
            <div className="ai-spinner" aria-hidden="true" />
            <span>正在生成推荐…</span>
          </div>
        )
      ) : followUpSession && followUpSession.stage === 'follow_up' && followUpSession.question ? (
        // ============ P5-02：AI 动态追问态 ============
        <section className="follow-up-shell" aria-label="动态追问">
          <header className="follow-up-header">
            <p className="follow-up-rounds">
              {preferAiGain && auth.isAuthenticated
                ? 'AI 追问'
                : '补充追问'}{' '}
              · 第 {Math.min(followUpSession.rounds_completed + 1, followUpSession.max_rounds)} / {followUpSession.max_rounds} 轮
            </p>
            <p className="follow-up-purpose">{followUpSession.question.purpose_zh}</p>
          </header>
          
          {/* P5-02A：追问加载骨架屏 */}
          {answerLoading && showFollowUpSkeleton ? (
            <div className="follow-up-loading" aria-busy="true" aria-live="polite">
              <div className="follow-up-loading-message">
                {FOLLOW_UP_LOADING_MESSAGES[Math.min(followUpSession.rounds_completed, FOLLOW_UP_LOADING_MESSAGES.length - 1)]}
              </div>
              <div className="follow-up-skeleton-card">
                <div className="skeleton-row" style={{ height: '24px', width: '40%', marginBottom: 'var(--space-3)' }} />
                <div className="follow-up-skeleton-options">
                  <div className="skeleton-row" style={{ height: '36px', width: '120px', borderRadius: '999px' }} />
                  <div className="skeleton-row" style={{ height: '36px', width: '140px', borderRadius: '999px' }} />
                  <div className="skeleton-row" style={{ height: '36px', width: '100px', borderRadius: '999px' }} />
                </div>
              </div>
            </div>
          ) : (
            <FollowUpQuestionCard
              question={followUpSession.question}
              answering={answerLoading}
              onPick={answerFollowUp}
            />
          )}
          
          <div className="q-footer">
            <button
              type="button"
              className="button button-secondary"
              data-testid="follow-up-back"
              onClick={handleBackToQuestionnaire}
              disabled={answerLoading}
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
                  id={`q-card-${q.question_id}`}
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

          {/* P5-04A：AI 增益开关（问卷态底栏上方；默认关=免费规则；勾上需登录+扣每日3次额度） */}
          <div
            className="ai-gain-switch-shell"
            aria-label="AI 优化推荐开关"
            style={{
              marginTop: 'var(--space-3)',
              marginBottom: 'var(--space-3)',
              padding: 'var(--space-3) var(--space-4)',
              borderRadius: 'var(--radius-md)',
              border: '1px solid var(--color-border)',
              background: 'color-mix(in oklab, var(--color-primary) 6%, var(--color-surface))',
            }}
          >
            <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 'var(--space-2)', justifyContent: 'space-between' }}>
              <div style={{ flex: '1 1 260px', minWidth: 0 }}>
                <div style={{ fontWeight: 600, fontSize: '0.95rem' }}>
                  ✨ 使用 AI 优化推荐
                </div>
                <div style={{ fontSize: '0.82rem', opacity: 0.85, marginTop: 2 }}>
                  {preferAiGain
                    ? auth.isAuthenticated
                      ? '已开启：推荐会调用大模型进行口味/场景再排序，每天 3 次额度。'
                      : '开启后需要登录（每天 3 次额度）；未登录时默认使用免费的确定性规则引擎。'
                    : '默认关闭：使用免费的确定性规则引擎，不扣额度，不要求登录。'}
                </div>
              </div>
              <label
                className="ai-gain-switch-label"
                style={{ display: 'inline-flex', alignItems: 'center', gap: 'var(--space-2)', cursor: recState.loading ? 'not-allowed' : 'pointer', userSelect: 'none' }}
              >
                <span style={{ fontSize: '0.82rem', opacity: 0.85 }}>
                  {preferAiGain ? '已开启' : '默认关闭'}
                </span>
                <span
                  className={`switch ${preferAiGain ? 'is-on' : 'is-off'}`}
                  role="switch"
                  aria-checked={preferAiGain}
                  tabIndex={0}
                  onKeyDown={(e) => {
                    if (recState.loading) return;
                    if (e.key === 'Enter' || e.key === ' ') {
                      e.preventDefault();
                      setPreferAiGain((v) => !v);
                    }
                  }}
                  onClick={() => {
                    if (recState.loading) return;
                    setPreferAiGain((v) => !v);
                  }}
                  style={{
                    width: 44,
                    height: 24,
                    borderRadius: 999,
                    border: '1px solid var(--color-border)',
                    background: preferAiGain
                      ? 'var(--color-primary)'
                      : 'color-mix(in oklab, var(--color-border) 60%, var(--color-surface))',
                    position: 'relative',
                    transition: 'background 0.18s ease',
                    flex: '0 0 auto',
                  }}
                >
                  <span
                    aria-hidden
                    style={{
                      position: 'absolute',
                      top: 2,
                      left: preferAiGain ? 22 : 2,
                      width: 18,
                      height: 18,
                      borderRadius: 999,
                      background: 'var(--color-surface)',
                      boxShadow: '0 1px 2px rgba(0,0,0,0.18)',
                      transition: 'left 0.18s ease',
                    }}
                  />
                </span>
              </label>
            </div>
            {/* 未登录 + 开关打开时：显示登录 CTA */}
            {preferAiGain && !auth.isAuthenticated ? (
              <div
                role="note"
                style={{
                  marginTop: 'var(--space-2)',
                  padding: 'var(--space-2) var(--space-3)',
                  borderRadius: 'var(--radius-sm)',
                  background: 'color-mix(in oklab, #e6a23c 14%, var(--color-surface))',
                  border: '1px dashed color-mix(in oklab, #e6a23c 50%, var(--color-border))',
                  fontSize: '0.82rem',
                  display: 'flex',
                  flexWrap: 'wrap',
                  alignItems: 'center',
                  gap: 'var(--space-2)',
                }}
              >
                <span>「AI 优化推荐」需要先登录后使用（每天 3 次额度）。登录后每次推荐会先调用 AI 再给 5 条更贴合你的最终候选。</span>
                <button
                  type="button"
                  className="button button-primary"
                  onClick={() => {
                    try {
                      if (typeof window !== 'undefined') {
                        window.localStorage.setItem('eatwhat:next_path', '/recommend');
                      }
                    } catch { /* ignore */ }
                    nav('/login');
                  }}
                  style={{ margin: 0 }}
                >
                  去登录
                </button>
              </div>
            ) : null}
          </div>

          <div className="q-footer">
            {nextAction === 'proceed_generate_recommendations' ? (
              <button
                type="button"
                className="button button-primary button-large"
                data-testid="goto-recommendations"
                disabled={recState.loading}
                aria-disabled={recState.loading}
                onClick={() => {
                  if (!recState.loading) {
                    void generateRecommendations();
                  }
                }}
                style={recState.loading ? { cursor: 'not-allowed', opacity: 0.7 } : undefined}
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
              <div
                style={{
                  marginTop: 'var(--space-3)',
                  display: 'flex',
                  justifyContent: 'flex-end',
                }}
              >
                <button
                  type="button"
                  className="button button-secondary"
                  onClick={() => nav(`/nearby?food_code=${encodeURIComponent(item.food_code)}`)}
                  data-testid={`rec-nearby-${item.priority}`}
                >
                  📍 查附近「{item.food_code}」商家 →
                </button>
              </div>
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

          {/* P7-08A：推荐结果页反馈入口（显眼位置 + 内联展开表单） */}
          <div
            style={{
              marginTop: 'var(--space-4)',
              padding: 'var(--space-3) var(--space-4)',
              borderRadius: 'var(--radius-md)',
              border: '1px dashed color-mix(in oklab, var(--color-border) 70%, transparent)',
              background:
                'color-mix(in oklab, var(--color-surface-alt) 55%, var(--color-surface))',
            }}
            aria-label="反馈入口"
          >
            <div
              style={{
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
                gap: 'var(--space-2)',
                flexWrap: 'wrap',
              }}
            >
              <p style={{ margin: 0, fontSize: '0.92rem', opacity: 0.9 }}>
                💡 推荐结果符合你的预期吗？有任何不满意都可以告诉我们，帮助我们越做越好。
              </p>
              <button
                type="button"
                className="button button-secondary"
                data-testid="toggle-feedback"
                onClick={() => {
                  const next = !feedbackOpen;
                  setFeedbackOpen(next);
                  if (next) void loadFeedbackTypes();
                }}
                style={{ margin: 0 }}
              >
                {feedbackOpen ? '收起反馈' : '✉️ 提交反馈'}
              </button>
            </div>
            {feedbackOpen ? (
              <div
                style={{
                  marginTop: 'var(--space-3)',
                  display: 'grid',
                  gap: 'var(--space-3)',
                }}
                role="region"
                aria-label="反馈表单"
              >
                <fieldset
                  style={{
                    border: 'none',
                    padding: 0,
                    margin: 0,
                    display: 'grid',
                    gap: 'var(--space-2)',
                  }}
                >
                  <legend style={{ fontWeight: 600, fontSize: '0.88rem' }}>反馈类型</legend>
                  <div
                    style={{
                      display: 'grid',
                      gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))',
                      gap: 'var(--space-2)',
                    }}
                  >
                    {(feedbackTypes ?? []).map((t) => {
                      const selected = feedbackType === t.key;
                      return (
                        <button
                          key={t.key}
                          type="button"
                          onClick={() => setFeedbackType(t.key)}
                          aria-pressed={selected}
                          style={{
                            textAlign: 'left',
                            padding: 'var(--space-2) var(--space-3)',
                            borderRadius: 'var(--radius-md)',
                            border: `1px solid ${
                              selected
                                ? 'color-mix(in oklab, var(--color-primary) 60%, var(--color-border))'
                                : 'var(--color-border)'
                            }`,
                            background: selected
                              ? 'color-mix(in oklab, var(--color-primary) 12%, var(--color-surface))'
                              : 'var(--color-surface)',
                            cursor: 'pointer',
                          }}
                        >
                          <div style={{ fontWeight: 600, marginBottom: 2 }}>{t.label}</div>
                          <div style={{ fontSize: '0.82rem', opacity: 0.8 }}>{t.description}</div>
                        </button>
                      );
                    })}
                  </div>
                </fieldset>
                <label
                  style={{
                    display: 'grid',
                    gap: 'var(--space-1)',
                    fontSize: '0.88rem',
                    fontWeight: 600,
                  }}
                >
                  具体说明
                  <textarea
                    value={feedbackContent}
                    onChange={(e) => setFeedbackContent(e.target.value)}
                    placeholder="例如：选择韩料之后推荐的第 2 个 food_code 不在字典里 / 结果标签显示 cuisine_preferences 未收集但实际已填…"
                    rows={4}
                    style={{
                      padding: 'var(--space-2) var(--space-3)',
                      borderRadius: 'var(--radius-md)',
                      border: '1px solid var(--color-border)',
                      background: 'var(--color-surface)',
                      fontSize: '0.92rem',
                      fontWeight: 400,
                      lineHeight: 1.55,
                      resize: 'vertical',
                      minHeight: 90,
                    }}
                  />
                </label>
                {feedbackMessage ? (
                  <div
                    role="status"
                    aria-live="polite"
                    style={{
                      padding: 'var(--space-2) var(--space-3)',
                      borderRadius: 'var(--radius-md)',
                      fontSize: '0.88rem',
                      background:
                        feedbackStatus === 'error'
                          ? 'color-mix(in oklab, #e74c3c 10%, var(--color-surface))'
                          : feedbackStatus === 'done'
                            ? 'color-mix(in oklab, #27ae60 10%, var(--color-surface))'
                            : 'color-mix(in oklab, var(--color-primary) 10%, var(--color-surface))',
                      color: feedbackStatus === 'error' ? 'color-mix(in oklab, #c0392b, var(--color-text))' : undefined,
                    }}
                  >
                    {feedbackStatus === 'submitting' ? '⏳ ' : feedbackStatus === 'done' ? '✅ ' : feedbackStatus === 'error' ? '❌ ' : 'ℹ️ '}
                    {feedbackMessage}
                  </div>
                ) : null}
                <div
                  style={{
                    display: 'flex',
                    justifyContent: 'flex-end',
                    gap: 'var(--space-2)',
                    flexWrap: 'wrap',
                  }}
                >
                  <button
                    type="button"
                    className="button button-secondary"
                    onClick={() => {
                      setFeedbackOpen(false);
                      setFeedbackStatus('idle');
                      setFeedbackMessage(null);
                    }}
                    disabled={feedbackStatus === 'submitting'}
                  >
                    取消
                  </button>
                  <button
                    type="button"
                    className="button button-primary"
                    onClick={() => void handleSubmitFeedback()}
                    disabled={feedbackStatus === 'submitting'}
                  >
                    {feedbackStatus === 'submitting' ? '提交中…' : '提交反馈'}
                  </button>
                </div>
              </div>
            ) : null}
          </div>

          <div className="q-footer">
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 'var(--space-2)', alignItems: 'center' }}>
              <button
                type="button"
                className="button button-secondary"
                data-testid="back-to-questionnaire"
                onClick={handleBackToQuestionnaire}
              >
                返回修改答案
              </button>
              {/* P0 修复：画像未保存时显示「保存」按钮；已保存时显示「去设置看画像」按钮 */}
              {resultAutowrite?.preference_saved || manualSaveStatus === 'saved' ? (
                <button
                  type="button"
                  className="button button-secondary"
                  onClick={gotoSettingsPreference}
                >
                  查看饮食偏好画像与时间轴 →
                </button>
              ) : (
                <button
                  type="button"
                  className="button button-primary"
                  onClick={handleManualSavePreference}
                  disabled={manualSaveStatus === 'saving'}
                  data-testid="manual-save-preference"
                >
                  {manualSaveStatus === 'saving'
                    ? '保存中…'
                    : resultAutowrite && !resultAutowrite.logged_in
                      ? '登录后可保存画像（点击前往设置）'
                      : '保存到我的饮食偏好画像'}
                </button>
              )}
              {resultAutowrite && !resultAutowrite.logged_in ? (
                <button
                  type="button"
                  className="button button-secondary"
                  onClick={gotoSettingsPreference}
                >
                  去设置→登录/画像 Tab
                </button>
              ) : null}
            </div>
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
