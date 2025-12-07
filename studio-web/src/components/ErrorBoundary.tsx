import React, { Component, ErrorInfo, ReactNode } from 'react';

interface Props {
  children: ReactNode;
  fallback?: ReactNode;
}

interface State {
  hasError: boolean;
  error: Error | null;
  errorInfo: ErrorInfo | null;
}

/**
 * ErrorBoundary — catches React errors and displays a graceful fallback UI
 * 
 * Usage:
 *   <ErrorBoundary>
 *     <YourComponent />
 *   </ErrorBoundary>
 * 
 * Or with custom fallback:
 *   <ErrorBoundary fallback={<MyCustomError />}>
 *     <YourComponent />
 *   </ErrorBoundary>
 */
export class ErrorBoundary extends Component<Props, State> {
  constructor(props: Props) {
    super(props);
    this.state = { hasError: false, error: null, errorInfo: null };
  }

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error, errorInfo: null };
  }

  componentDidCatch(error: Error, errorInfo: ErrorInfo) {
    console.error('ErrorBoundary caught an error:', error, errorInfo);
    this.state = { ...this.state, errorInfo };
  }

  reset = () => {
    this.setState({ hasError: false, error: null, errorInfo: null });
  };

  render() {
    const { hasError, error, errorInfo } = this.state;
    const { children, fallback } = this.props;

    if (hasError) {
      if (fallback) {
        return fallback;
      }

      return (
        <div className="error-boundary">
          <div className="error-boundary__content">
            <div className="error-boundary__icon">⚠️</div>
            <h2 className="error-boundary__title">Something went wrong</h2>
            <p className="error-boundary__message">
              {error?.message || 'An unexpected error occurred'}
            </p>
            
            {process.env.NODE_ENV === 'development' && errorInfo && (
              <details className="error-boundary__details">
                <summary>Error Details (Dev Only)</summary>
                <pre className="error-boundary__stack">
                  {error?.stack}
                  {'\n\n'}
                  {errorInfo.componentStack}
                </pre>
              </details>
            )}

            <button 
              className="error-boundary__button"
              onClick={this.reset}
            >
              Try Again
            </button>
          </div>

          <style>{`
            .error-boundary {
              min-height: 400px;
              display: flex;
              align-items: center;
              justify-content: center;
              padding: var(--space-8);
              background: var(--color-surface);
            }
            .error-boundary__content {
              max-width: 560px;
              text-align: center;
              padding: var(--space-8);
              background: var(--color-bg);
              border: 1px solid var(--color-border);
              border-radius: var(--radius-lg);
              box-shadow: var(--shadow-card);
            }
            .error-boundary__icon {
              font-size: 48px;
              margin-bottom: var(--space-4);
            }
            .error-boundary__title {
              font-size: var(--text-2xl);
              font-weight: 600;
              color: var(--color-text-strong);
              margin-bottom: var(--space-3);
            }
            .error-boundary__message {
              color: var(--color-text-muted);
              margin-bottom: var(--space-6);
              line-height: 1.6;
            }
            .error-boundary__details {
              text-align: left;
              margin: var(--space-6) 0;
              padding: var(--space-4);
              background: color-mix(in oklab, var(--color-danger) 8%, transparent);
              border: 1px solid color-mix(in oklab, var(--color-danger) 20%, transparent);
              border-radius: var(--radius-md);
            }
            .error-boundary__details summary {
              cursor: pointer;
              font-weight: 600;
              color: var(--color-danger);
              margin-bottom: var(--space-3);
            }
            .error-boundary__stack {
              font-family: var(--font-mono);
              font-size: var(--text-sm);
              white-space: pre-wrap;
              word-break: break-word;
              color: var(--color-text);
              background: var(--color-surface);
              padding: var(--space-3);
              border-radius: var(--radius-sm);
              overflow: auto;
              max-height: 300px;
            }
            .error-boundary__button {
              padding: var(--space-3) var(--space-6);
              background: var(--color-accent);
              color: var(--color-on-accent);
              border: none;
              border-radius: var(--radius-md);
              font-weight: 600;
              cursor: pointer;
              transition: transform 120ms ease, box-shadow 120ms ease;
              box-shadow: var(--shadow-sm);
            }
            .error-boundary__button:hover {
              transform: translateY(-1px);
              box-shadow: var(--shadow-md);
            }
            .error-boundary__button:active {
              transform: translateY(0);
            }
          `}</style>
        </div>
      );
    }

    return children;
  }
}

export default ErrorBoundary;
