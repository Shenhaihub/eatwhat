import { Component, type ErrorInfo, type ReactNode } from 'react';

interface ErrorBoundaryProps {
  children: ReactNode;
  fallback?: ReactNode;
}

interface ErrorBoundaryState {
  hasError: boolean;
}

/**
 * 顶层错误边界：捕获渲染期错误，避免整个应用白屏。
 * 兜底文案不暴露内部错误细节；后续可接入错误上报。
 */
export default class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = { hasError: false };

  static getDerivedStateFromError(): ErrorBoundaryState {
    return { hasError: true };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // 只记录到控制台/上报，不向用户展示内部细节
    console.error('EatWhat UI error:', error, info);
  }

  private reset = (): void => {
    this.setState({ hasError: false });
    window.location.hash = '#/';
  };

  render(): ReactNode {
    if (this.state.hasError) {
      return (
        this.props.fallback ?? (
          <div className="page-shell placeholder-page">
            <p className="eyebrow">出错了</p>
            <h1>页面暂时无法显示</h1>
            <p>请刷新重试。如果问题持续，可以返回首页继续使用不依赖 AI 的功能。</p>
            <button type="button" className="button button-primary" onClick={this.reset}>
              返回首页
            </button>
          </div>
        )
      );
    }
    return this.props.children;
  }
}
