import { render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import ErrorBoundary from './ErrorBoundary';

function Bomb(): never {
  throw new Error('boom');
}

describe('ErrorBoundary', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('捕获子组件渲染错误并渲染兜底界面', () => {
    // React 会向 console.error 报告被边界捕获的错误，测试中静音预期输出
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {});
    render(
      <ErrorBoundary>
        <Bomb />
      </ErrorBoundary>,
    );
    expect(screen.getByRole('heading', { name: /页面暂时无法显示/ })).toBeInTheDocument();
    expect(spy).toHaveBeenCalled();
  });

  it('子组件正常时透传内容', () => {
    render(
      <ErrorBoundary>
        <h1>正常页面</h1>
      </ErrorBoundary>,
    );
    expect(screen.getByRole('heading', { name: '正常页面' })).toBeInTheDocument();
  });
});
