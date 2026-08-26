import { act, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import BackendWakeOverlay from './BackendWakeOverlay';
import {
  BACKEND_AWAKE_EVENT,
  BACKEND_WAKING_EVENT,
} from '../../services/api/client';

function fire(eventName: string): void {
  act(() => {
    window.dispatchEvent(new Event(eventName));
  });
}

describe('BackendWakeOverlay', () => {
  afterEach(() => {
    fire(BACKEND_AWAKE_EVENT);
  });

  it('初始不显示等待层', () => {
    render(<BackendWakeOverlay />);
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('收到后端唤醒事件后显示等待层', () => {
    render(<BackendWakeOverlay />);
    fire(BACKEND_WAKING_EVENT);
    expect(screen.getByRole('dialog')).toBeTruthy();
    expect(screen.getByText(/正在唤醒后端服务/)).toBeTruthy();
    expect(screen.getByText(/往上跳/)).toBeTruthy();
  });

  it('收到全部请求结束事件后关闭等待层', () => {
    render(<BackendWakeOverlay />);
    fire(BACKEND_WAKING_EVENT);
    expect(screen.getByRole('dialog')).toBeTruthy();
    fire(BACKEND_AWAKE_EVENT);
    expect(screen.queryByRole('dialog')).toBeNull();
  });
});
