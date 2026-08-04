import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { describe, expect, it } from 'vitest';
import App from './App';

describe('App 路由壳', () => {
  it('渲染首页', () => {
    render(
      <MemoryRouter initialEntries={['/']}>
        <App />
      </MemoryRouter>,
    );
    expect(screen.getByRole('heading', { name: /别再纠结/ })).toBeInTheDocument();
  });

  it('未知路由渲染 404 页', () => {
    render(
      <MemoryRouter initialEntries={['/no-such-page']}>
        <App />
      </MemoryRouter>,
    );
    expect(screen.getByRole('heading', { name: '页面不存在' })).toBeInTheDocument();
  });

  it('渲染"跳到主要内容"跳转链接', () => {
    render(
      <MemoryRouter initialEntries={['/']}>
        <App />
      </MemoryRouter>,
    );
    expect(screen.getByRole('link', { name: '跳到主要内容' })).toBeInTheDocument();
  });
});
