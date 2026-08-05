import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import * as apiClient from '../services/api/client';
import type {
  QuestionnaireNextRequestV1,
  QuestionnaireRecomputeResult,
  RecommendationsGenerateRequestV1,
  RecommendationsGenerateResponseV1,
  RecommendationItem,
} from '../services/api/types';
import Recommend from '../pages/Recommend';

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

const TOP5_RESPONSE: RecommendationsGenerateResponseV1 = [
  makeRec('malatang', 1),
  makeRec('zhou_cai', 2),
  makeRec('rice_noodle', 3),
  makeRec('braised_pork_rice', 4),
  makeRec('small_bowl_dishes', 5),
];

describe('/recommend 推荐结果端到端（P2-04 前端接入 recommendationsGenerate）', () => {
  beforeEach(() => {
    if (typeof window !== 'undefined') window.localStorage.clear();
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

    render(<Recommend />);
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

    render(<Recommend />);
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

    render(<Recommend />);
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
      expect(nameEl).toHaveTextContent(TOP5_RESPONSE[p - 1]!.food_code);
    }
    // MEM-024 验证：链路 B 首菜不是小碗菜（是 malatang）
    expect(screen.getByTestId('rec-name-1')).toHaveTextContent('malatang');
  });
});
