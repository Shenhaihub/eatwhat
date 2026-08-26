import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MemoryRouter } from 'react-router';

import * as apiClient from '../services/api/client';
import type {
  QuestionnaireNextRequestV1,
  QuestionnaireRecomputeResult,
  RecommendationsGenerateRequestV1,
  RecommendationsGenerateResponseV1,
  RecommendationItem,
  SessionStateResponseV1,
} from '../services/api/types';
import { AuthProvider } from '../context/AuthContext';
import Recommend from '../pages/Recommend';
import { displayFoodName } from '../lib/foodNames';

function renderInContext(ui: React.ReactElement) {
  return render(
    <MemoryRouter initialEntries={['/recommend']}>
      <AuthProvider>{ui}</AuthProvider>
    </MemoryRouter>,
  );
}

const BASE_RESULT: QuestionnaireRecomputeResult = {
  questionnaire_version: 'v1.0',
  next_questions: [],
  next_question_ids: [],
  invalidated_answer_ids: [],
  is_complete: false,
  progress: 0,
  covered_dimensions: [
    { field_name: 'meal_period', covered: false },
    { field_name: 'appetite', covered: false },
    { field_name: 'avoidances', covered: false },
    { field_name: 'tastes', covered: false },
    { field_name: 'budget', covered: false },
    { field_name: 'explicit_food_preference', covered: false },
  ],
  completion_reason: 'not_complete',
  required_not_yet_answered_question_ids: ['q01_meal_period', 'q02_explicit_food', 'q03_budget'],
  next_action: 'proceed_questionnaire',
};

const COMPLETE_RESULT: QuestionnaireRecomputeResult = {
  ...BASE_RESULT,
  progress: 100,
  is_complete: true,
  next_action: 'proceed_generate_recommendations',
  completion_reason: 'all_required_answered',
  required_not_yet_answered_question_ids: [],
  covered_dimensions: BASE_RESULT.covered_dimensions.map((d) => ({ ...d, covered: true })),
};

// 准备一段 answers_by_qid，用于 assert 请求体映射正确
const COMPLETE_ANSWERS: Record<string, string[]> = {
  q01_meal_period: ['lunch'],
  q02_explicit_food: ['malatang'],
  q03_budget: ['from_20_to_30'],
  q04_tastes: ['spicy'],
  q06_appetite: ['hungry'],
};

function makeRec(food_code: string, priority: 1 | 2 | 3 | 4 | 5): RecommendationItem {
  return {
    priority,
    food_code,
    source_type: 'ai_recommended',
    generation_mode: 'rule',
    reason: {
      summary_zh: `根据你的选择，优先推荐 ${food_code}。`,
      matched_signals: [
        `餐段=lunch`,
        `明确想吃=malatang`,
        `预算=20-30`,
      ],
    },
    budget_fit: priority === 1 ? 'fits' : 'uncertain',
    budget_fit_note_zh:
      priority === 1 ? '价格仅为平台参考，不承诺具体商户售价（G-10）' : null,
  };
}

const TOP5_ITEMS: readonly RecommendationItem[] = [
  makeRec('malatang', 1),
  makeRec('zhou_cai', 2),
  makeRec('rice_noodle', 3),
  makeRec('braised_pork_rice', 4),
  makeRec('small_bowl_dishes', 5),
];
const TOP5_RESPONSE: RecommendationsGenerateResponseV1 = {
  items: TOP5_ITEMS,
  merged_pref_fields: [],
};

function _items(r: RecommendationsGenerateResponseV1): readonly RecommendationItem[] {
  return Array.isArray(r) ? r : r.items;
}

describe('/recommend 推荐结果端到端（P2-04 前端接入 recommendationsGenerate）', () => {
  beforeEach(() => {
    if (typeof window !== 'undefined') window.localStorage.clear();

    // P5 动态会话是"新推荐链路"，测试意图在验证 P2-04 的老流程 fallback：
    // 让 recommendationsSessionStart 抛非 AbortError，代码会自动 fallback 到 legacy recommendationsGenerate，
    // 这样就能复用原先 recSpy 的断言（测试请求体映射 + 结果渲染 + 1-3-5 渐进展开）。
    vi.spyOn(apiClient.api, 'recommendationsSessionStart').mockRejectedValue(
      new Error('[RecGen test] 模拟 session API 未实现，fallback 到 legacy recommendationsGenerate'),
    );
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
    if (typeof window !== 'undefined') window.localStorage.clear();
  });

  it('1) 触发：问卷 complete → 点击"去看推荐结果"按钮会触发 recommendationsGenerate() 一次', async () => {
    const user = userEvent.setup();

    // 先把 answers 写成"全部答完"的草稿，这样 questionnaireNext 返回 complete=yes 后进入
    // proceed_generate_recommendations，用户能点按钮
    if (typeof window !== 'undefined') {
      window.localStorage.setItem(
        'eatwhat:questionnaire:draft:v1.0:ai_recommend',
        JSON.stringify(COMPLETE_ANSWERS),
      );
    }

    const nextSpy = vi
      .spyOn(apiClient.api, 'questionnaireNext')
      .mockResolvedValue(COMPLETE_RESULT);
    const recSpy = vi
      .spyOn(apiClient.api, 'recommendationsGenerate')
      .mockResolvedValue(TOP5_RESPONSE);

    renderInContext(<Recommend />);
    // 等 progress=100 落库
    await waitFor(() =>
      expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '100'),
    );
    expect(nextSpy).toHaveBeenCalled();

    // 点击"去看推荐结果"
    const btn = screen.getByTestId('goto-recommendations');
    await user.click(btn);

    // 应该触发一次 recommendationsGenerate
    await waitFor(() => expect(recSpy).toHaveBeenCalledTimes(1), { timeout: 3000 });
  });

  it('2) 映射：recommendationsGenerate() 请求体里 entry_intent=ai_recommend + questionnaire_version=v1.0 + answers_by_question_id 与草稿一致', async () => {
    const user = userEvent.setup();

    if (typeof window !== 'undefined') {
      window.localStorage.setItem(
        'eatwhat:questionnaire:draft:v1.0:ai_recommend',
        JSON.stringify(COMPLETE_ANSWERS),
      );
    }

    vi.spyOn(apiClient.api, 'questionnaireNext').mockResolvedValue(COMPLETE_RESULT);
    let captured: RecommendationsGenerateRequestV1 | null = null;
    vi.spyOn(apiClient.api, 'recommendationsGenerate').mockImplementation(async (req) => {
      captured = req;
      return TOP5_RESPONSE;
    });

    renderInContext(<Recommend />);
    await waitFor(() =>
      expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '100'),
    );

    const btn = screen.getByTestId('goto-recommendations');
    await user.click(btn);
    await waitFor(() => expect(captured).not.toBeNull(), { timeout: 3000 });

    // eslint-disable-next-line @typescript-eslint/no-unnecessary-type-assertion
    const req = captured as unknown as RecommendationsGenerateRequestV1;
    expect(req.entry_intent).toBe('ai_recommend');
    expect(req.questionnaire_version).toBe('v1.0');
    // 不允许客户端塞 G-07 字段（这里只断言：不存在 source_type 字段）
    expect(Object.prototype.hasOwnProperty.call(req, 'source_type')).toBe(false);
    // answers 与草稿一致
    expect(req.answers_by_question_id).toEqual(COMPLETE_ANSWERS);

    // 顺带保证 questionnaireNext 的首调 body 也不含 source_type（G-07 前端契约）
    const firstNextCall = vi.mocked(apiClient.api.questionnaireNext).mock.calls[0]?.[0] as
      | QuestionnaireNextRequestV1
      | undefined;
    expect(firstNextCall).toBeDefined();
    expect(
      Object.prototype.hasOwnProperty.call(firstNextCall ?? {}, 'source_type'),
    ).toBe(false);
  });

  it('3) 渲染：响应返回正好 5 张卡片；priority 1..5 从上到下递增，第 1 张名称是 malatang（首菜差异化）', async () => {
    const user = userEvent.setup();

    if (typeof window !== 'undefined') {
      window.localStorage.setItem(
        'eatwhat:questionnaire:draft:v1.0:ai_recommend',
        JSON.stringify(COMPLETE_ANSWERS),
      );
    }

    vi.spyOn(apiClient.api, 'questionnaireNext').mockResolvedValue(COMPLETE_RESULT);
    vi.spyOn(apiClient.api, 'recommendationsGenerate').mockResolvedValue(TOP5_RESPONSE);

    renderInContext(<Recommend />);
    await waitFor(() =>
      expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '100'),
    );

    await user.click(screen.getByTestId('goto-recommendations'));
    // 等 Top5 列表出现
    await waitFor(() => screen.getByTestId('recommendations-list'), { timeout: 3000 });

    const list = screen.getByTestId('recommendations-list');
    const cards = list.querySelectorAll('.recommendation-card');
    expect(cards).toHaveLength(5);

    // 按 data-priority 顺序检查
    for (let p = 1; p <= 5; p++) {
      const card = list.querySelector(`[data-priority="${p}"]`);
      expect(card).toBeTruthy();
      const nameEl = screen.getByTestId(`rec-name-${p}`);
      expect(nameEl).toHaveTextContent(displayFoodName(_items(TOP5_RESPONSE)[p - 1]!));
    }
    // MEM-024 验证：链路 B 首菜不是小碗菜（是 malatang）
    expect(screen.getByTestId('rec-name-1')).toHaveTextContent(displayFoodName(TOP5_ITEMS[0]!));
  });

  it('4) 1→3→5 渐进展示：初始只可见 1 张卡片 → 点击展开到 3 → 再点击展开到 5', async () => {
    const user = userEvent.setup();

    if (typeof window !== 'undefined') {
      window.localStorage.setItem(
        'eatwhat:questionnaire:draft:v1.0:ai_recommend',
        JSON.stringify(COMPLETE_ANSWERS),
      );
    }

    vi.spyOn(apiClient.api, 'questionnaireNext').mockResolvedValue(COMPLETE_RESULT);
    vi.spyOn(apiClient.api, 'recommendationsGenerate').mockResolvedValue(TOP5_RESPONSE);

    renderInContext(<Recommend />);
    await waitFor(() =>
      expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '100'),
    );

    await user.click(screen.getByTestId('goto-recommendations'));
    await waitFor(() => screen.getByTestId('recommendations-list'), { timeout: 3000 });

    // 辅助：统计可见卡片数（hidden 属性 = 不可见）
    const countVisible = () =>
      screen
        .getByTestId('recommendations-list')
        .querySelectorAll('.recommendation-card:not([hidden])').length;

    // 初始：只展示 priority=1（1 张可见）
    expect(countVisible()).toBe(1);
    expect(screen.getByTestId('expand-recommendations')).toHaveTextContent('3/5');

    // 第一次展开 → 3 张可见
    await user.click(screen.getByTestId('expand-recommendations'));
    expect(countVisible()).toBe(3);
    expect(screen.getByTestId('expand-recommendations')).toHaveTextContent('5/5');

    // 第二次展开 → 5 张可见，按钮消失
    await user.click(screen.getByTestId('expand-recommendations'));
    expect(countVisible()).toBe(5);
    expect(screen.queryByTestId('expand-recommendations')).toBeNull();
  });
});

// ---------- P5-02 新链路：follow_up 自动跳过 seed 预填维度 ----------
describe('/recommend P5 session follow_up 自动跳过 seed 预填（菜系/明确想吃重复题不再显示）', () => {
  // answers 基础集 + q07 预填=japanese（对应社区"就按日料给我生成推荐"跳过来）
  const ANSWERS_WITH_JAPANESE: Record<string, string[]> = {
    q01_meal_period: ['lunch'],
    q02_explicit_food: ['sushi'],
    q03_budget: ['from_20_to_30'],
    q04_tastes: ['light'],
    q06_appetite: ['normal'],
    q07_cuisine_preference: ['japanese'],
  };
  const ANSWERS_NO_CUISINE: Record<string, string[]> = {
    q01_meal_period: ['lunch'],
    q02_explicit_food: [],
    q03_budget: ['from_20_to_30'],
    q04_tastes: ['light'],
    q06_appetite: ['normal'],
  };

  // —— 与真实后端 FOLLOW_UP_TEMPLATES 保持一致的选项结构 ——
  // 后端把 japanese+korean 合并成 japanese_korean / 日韩，验证模糊匹配链能兜住
  const CUISINE_FOLLOW_UP_QUESTION = {
    question_id: 'ai_fu_001_cuisine',
    title_zh: '今天想吃哪种菜系风格？',
    purpose_zh: '补充菜系偏好维度，避免推荐不随地域偏好变化',
    should_continue: true,
    options: [
      { value: 'chinese_north', label_zh: '北方家常（面/粥/饼/炖菜）' },
      { value: 'chinese_south', label_zh: '南方家常（米饭/小炒/汤）' },
      { value: 'western', label_zh: '西式（汉堡/三明治/披萨/沙拉）' },
      { value: 'japanese_korean', label_zh: '日韩（寿司/冷面/炸鸡）' },
      { value: 'spicy', label_zh: '只要辣（川菜/麻辣烫/烧烤）' },
    ],
  };

  function sessionFollowUp(question = CUISINE_FOLLOW_UP_QUESTION): SessionStateResponseV1 {
    return {
      session_id: 'sess_auto_skip_001',
      stage: 'follow_up',
      question,
      rounds_completed: 0,
      max_rounds: 3,
      candidates: null,
      final_reason: null,
      merged_pref_fields: [],
    };
  }
  function sessionFinal(): SessionStateResponseV1 {
    return {
      session_id: 'sess_auto_skip_001',
      stage: 'final',
      question: null,
      rounds_completed: 1,
      max_rounds: 3,
      candidates: TOP5_ITEMS,
      final_reason: 'rule_engine_fallback_ai_fail',
      merged_pref_fields: [],
    };
  }

  beforeEach(() => {
    if (typeof window !== 'undefined') window.localStorage.clear();
  });
  afterEach(() => {
    vi.restoreAllMocks();
    if (typeof window !== 'undefined') window.localStorage.clear();
  });

  it('5) 正向：answers.q07=japanese 时 session 返回菜系 follow_up → 前端自动回答并推进到 final，用户看不到重复题', async () => {
    const user = userEvent.setup();
    if (typeof window !== 'undefined') {
      window.localStorage.setItem(
        'eatwhat:questionnaire:draft:v1.0:ai_recommend',
        JSON.stringify(ANSWERS_WITH_JAPANESE),
      );
    }

    vi.spyOn(apiClient.api, 'questionnaireNext').mockResolvedValue(COMPLETE_RESULT);
    const startSpy = vi
      .spyOn(apiClient.api, 'recommendationsSessionStart')
      .mockResolvedValue(sessionFollowUp());
    const answerSpy = vi
      .spyOn(apiClient.api, 'recommendationsSessionAnswer')
      .mockResolvedValue(sessionFinal());

    renderInContext(<Recommend />);
    await waitFor(() =>
      expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '100'),
    );

    await user.click(screen.getByTestId('goto-recommendations'));

    // 断言 1：sessionStart 被调用 1 次
    await waitFor(() => expect(startSpy).toHaveBeenCalledTimes(1), { timeout: 3000 });
    // 断言 2：recommendationsSessionAnswer 被调用 1 次，且选项值 === 'japanese_korean'（模糊匹配命中 japanese_korean）
    expect(answerSpy).toHaveBeenCalledTimes(1);
    const callArg = vi.mocked(apiClient.api.recommendationsSessionAnswer).mock.calls[0]?.[1];
    expect(callArg?.selected_option_value).toBe('japanese_korean');
    expect(callArg?.question_id).toBe(CUISINE_FOLLOW_UP_QUESTION.question_id);

    // 断言 3：最终渲染 5 张结果卡；follow_up 标题「今天想吃哪种菜系风格？」没有在 DOM 上出现过（用户看不到重复题）
    await waitFor(() => screen.getByTestId('recommendations-list'), { timeout: 3000 });
    expect(screen.queryByText(/今天想吃哪种菜系风格/)).toBeNull();
    expect(screen.getByTestId('recommendations-list').querySelectorAll('.recommendation-card')).toHaveLength(5);
    expect(screen.getByTestId('rec-name-1')).toHaveTextContent(displayFoodName(TOP5_ITEMS[0]!));
  });

  it('6) 反向：answers 未填菜系时 session 返回菜系 follow_up → 不自动跳，显示给用户自己选（防误跳保护）', async () => {
    const user = userEvent.setup();
    if (typeof window !== 'undefined') {
      window.localStorage.setItem(
        'eatwhat:questionnaire:draft:v1.0:ai_recommend',
        JSON.stringify(ANSWERS_NO_CUISINE),
      );
    }

    vi.spyOn(apiClient.api, 'questionnaireNext').mockResolvedValue(COMPLETE_RESULT);
    vi.spyOn(apiClient.api, 'recommendationsSessionStart').mockResolvedValue(sessionFollowUp());
    const answerSpy = vi
      .spyOn(apiClient.api, 'recommendationsSessionAnswer')
      .mockResolvedValue(sessionFinal());

    renderInContext(<Recommend />);
    await waitFor(() =>
      expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '100'),
    );

    await user.click(screen.getByTestId('goto-recommendations'));

    // 自动回答不应触发（因为 q07 为空），answerSpy 被调用 0 次
    await waitFor(() =>
      expect(screen.getByText(/今天想吃哪种菜系风格/)).toBeTruthy(),
      { timeout: 3000 },
    );
    expect(answerSpy).toHaveBeenCalledTimes(0);
  });
});
