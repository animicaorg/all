import React from 'react';

type Props = {
  children: React.ReactNode;
  context: 'popup' | 'onboarding' | 'approve';
};

type State = {
  hasError: boolean;
  message: string;
};

const RELOAD_FLAG_PREFIX = 'animica.ui.autoreload';

export default class RecoverableErrorBoundary extends React.Component<Props, State> {
  public state: State = { hasError: false, message: '' };

  static getDerivedStateFromError(error: unknown): State {
    return {
      hasError: true,
      message: error instanceof Error ? error.message : String(error),
    };
  }

  componentDidCatch(error: unknown, info: React.ErrorInfo): void {
    console.error(`[ui:${this.props.context}] render crash`, error, info.componentStack);
    const flagKey = `${RELOAD_FLAG_PREFIX}:${this.props.context}`;
    try {
      if (sessionStorage.getItem(flagKey) === '1') return;
      sessionStorage.setItem(flagKey, '1');
      window.setTimeout(() => window.location.reload(), 300);
    } catch {
      // no-op
    }
  }

  componentDidMount(): void {
    const flagKey = `${RELOAD_FLAG_PREFIX}:${this.props.context}`;
    try {
      sessionStorage.removeItem(flagKey);
    } catch {
      // no-op
    }
  }

  private handleReload = () => {
    window.location.reload();
  };

  render() {
    if (!this.state.hasError) return this.props.children;

    return (
      <div style={{ padding: 16, fontFamily: 'Inter, system-ui, sans-serif', color: '#111827' }}>
        <h2 style={{ margin: '0 0 8px 0', fontSize: 18 }}>Something went wrong</h2>
        <p style={{ margin: '0 0 12px 0', fontSize: 13, color: '#4b5563' }}>
          The wallet UI hit an unexpected error and tried to auto-refresh.
        </p>
        {this.state.message ? (
          <pre
            style={{
              margin: '0 0 12px 0',
              padding: 10,
              borderRadius: 8,
              background: '#f3f4f6',
              fontSize: 12,
              whiteSpace: 'pre-wrap',
              wordBreak: 'break-word',
            }}
          >
            {this.state.message}
          </pre>
        ) : null}
        <button
          onClick={this.handleReload}
          style={{
            background: '#2563eb',
            color: '#fff',
            border: 0,
            borderRadius: 8,
            padding: '8px 12px',
            fontWeight: 600,
            cursor: 'pointer',
          }}
        >
          Refresh now
        </button>
      </div>
    );
  }
}
