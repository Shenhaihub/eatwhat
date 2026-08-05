import { render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import * as apiClient from '../services/api/client';
import type {
  QuestionnaireNextRequestV1,
  QuestionnaireRecomputeResult,
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

const Q01: QuestionnaireRecomputeResult['next_questions'][number] = {
  question_id: 'q01_meal_period',
  title_zh: '现在想吃哪顿饭？',
  question_type: 'single_choice',
  options: [
    { option_id: 'breakfast', label_zh: '早餐', value: 'breakfast' },
    { option_id: 'lunch', label_zh: '午餐', value: 'lunch' },
    { option_id: 'dinner', label_zh: '晚餐', value: 'dinner' },
  ],
  maps_to: { field_name: 'meal_period', is_array: false, value_is_enum_value: true },
  display_if: null,
  required_for_entry_intents: ['ai_recommend'],
};

const Q02: QuestionnaireRecomputeResult['next_questions'][number] = {
  question_id: 'q02_explicit_food',
  title_zh: '现在有明确想吃的吗？',
  question_type: 'single_choice',
  options: [
    { option_id: 'undecided', label_zh: '随便', value: 'undecided' },
    { option_id: 'malatang', label_zh: '麻辣烫', value: 'malatang' },
  ],
  maps_to: { field_name: 'explicit_food_preference', is_array: false, value_is_enum_value: true },
  display_if: null,
  required_for_entry_intents: ['ai_recommend'],
};

const Q06: QuestionnaireRecomputeResult['next_questions'][number] = {
  question_id: 'q06_appetite',
  title_zh: '现在饿不饿？',
  question_type: 'single_choice',
  options: [
    { option_id: 'light', label_zh: '没啥胃口', value: 'light' },
    { option_id: 'normal', label_zh: '正常', value: 'normal' },
  ],
  maps_to: { field_name: 'appetite', is_array: false, value_is_enum_value: true },
  display_if: {
    operator: 'in',
    operand_question_id: 'q01_meal_period',
    operand_value: ['lunch', 'dinner'],
  },
  required_for_entry_intents: [],
};

describe('/recommend 问卷页（P2-03B 前端接入 questionnaireNext）', () => {
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
    if (typeof window !== 'undefined') window.localStorage.clear();
  });

  it('1) 首调：页面渲染 next_questions 中的两道题，并显示 progress=0%', async () => {
    const nextSpy = vi
      .spyOn(apiClient.api, 'questionnaireNext')
      .mockResolvedValue({
        ...BASE_RESULT,
        next_questions: [Q01, Q02],
        next_question_ids: ['q01_meal_period', 'q02_explicit_food'],
      });

    render(<Recommend />);
    await waitFor(() => expect(nextSpy).toHaveBeenCalledTimes(1));

    expect(screen.getByText('现在想吃哪顿饭？')).toBeInTheDocument();
    expect(screen.getByText('现在有明确想吃的吗？')).toBeInTheDocument();
    expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '0');

    const req = nextSpy.mock.calls[0]?.[0] as QuestionnaireNextRequestV1;
    expect(req.entry_intent).toBe('ai_recommend');
    expect(req.questionnaire_version).toBe('v1.0');
    expect(req.answers_by_question_id).toEqual({});
  });

  it('2) invalidate：首调返回 invalidated=[q06_appetite] → 草稿里的 appetite 答案被清空（UI 取消 is-selected）', async () => {
    // 模拟"昨天的草稿 answers 里有 q01 晚餐 + q06 饿"，但今天用户打开页面时服务端
    // 检测到某种上下文变化（或存档过期）直接 invalidated q06_appetite，UI 必须立即清空。
    const draftAnswers = {
      q01_meal_period: ['dinner'],
      q06_appetite: ['normal'],
    };
    if (typeof window !== 'undefined') {
      window.localStorage.setItem(
        'eatwhat:questionnaire:draft:v1.0:ai_recommend',
        JSON.stringify(draftAnswers),
      );
    }

    vi.spyOn(apiClient.api, 'questionnaireNext').mockResolvedValue({
      ...BASE_RESULT,
      next_questions: [Q01, Q06],
      next_question_ids: ['q01_meal_period', 'q06_appetite'],
      progress: 17,
      // 关键：首调就宣布 appetite 的答案作废
      invalidated_answer_ids: ['q06_appetite'],
      covered_dimensions: BASE_RESULT.covered_dimensions.map((d) =>
        d.field_name === 'meal_period' ? { ...d, covered: true } : d,
      ),
      required_not_yet_answered_question_ids: ['q02_explicit_food', 'q03_budget'],
    } satisfies QuestionnaireRecomputeResult);

    render(<Recommend />);
    // 等待首调响应落库 → setResult + invalidated 分支 setAnswers 删除 q06_appetite
    await waitFor(
      () => {
        // progress 出现 → 响应已经 render
        expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '17');
      },
      { timeout: 3000 },
    );

    // 因为 invalidated=[q06_appetite]，所以 appetite 下所有选项都不应选中
    // （即便 localStorage 草稿里 normal 是选中的）
    const appetiteNormal = screen.getByRole('button', { name: '正常' });
    expect(appetiteNormal).not.toHaveClass('is-selected');
    const appetiteLight = screen.getByRole('button', { name: '没啥胃口' });
    expect(appetiteLight).not.toHaveClass('is-selected');
  });

  it('3) complete：必填答完 next_action=proceed_generate_recommendations → 底部出现"去看推荐结果"主按钮', async () => {
    vi.spyOn(apiClient.api, 'questionnaireNext').mockResolvedValue({
      ...BASE_RESULT,
      progress: 100,
      is_complete: true,
      next_action: 'proceed_generate_recommendations',
      completion_reason: 'all_required_answered',
      next_questions: [],
      next_question_ids: [],
      required_not_yet_answered_question_ids: [],
      covered_dimensions: BASE_RESULT.covered_dimensions.map((d) => ({ ...d, covered: true })),
    } satisfies QuestionnaireRecomputeResult);

    render(<Recommend />);
    await waitFor(() =>
      expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '100'),
    );
    const btn = screen.getByTestId('goto-recommendations');
    expect(btn).toBeInTheDocument();
    expect(btn.className).toMatch(/button-primary/);
  });
});
