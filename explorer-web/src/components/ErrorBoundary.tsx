/**
 * Animica Explorer — React Error Boundary
 * -----------------------------------------------------------------------------
 * Catches unhandled React errors and displays a user-friendly fallback UI.
 * Integrates with the toast system to notify users of errors.
 * 
 * Usage:
 *   <ErrorBoundary>
 *     <YourComponent />
 *   </ErrorBoundary>
 */

import React, { Component, ErrorInfo, ReactNode } from 'react';

interface Props {
  children: ReactNode;
  /** Optional fallback UI to display instead of default */
  fallback?: ReactNode;
  /** Optional callback when an error is caught */
  onError?: (error: Error, errorInfo: ErrorInfo) => void;
}

interface State {
  hasError: boolean;
  error: Error | null;
}

export class ErrorBoundary extends Component<Props, State> {
  constructor(props: Props) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error: Error): State {
    // Update state so the next render will show the fallback UI
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, errorInfo: ErrorInfo) {
    // Log error to console for debugging
    console.error('[ErrorBoundary] Caught error:', error);
    console.error('[ErrorBoundary] Component stack:', errorInfo.componentStack);

    // Call optional error handler
    this.props.onError?.(error, errorInfo);

    // Emit a toast notification
    try {
      window.dispatchEvent(
        new CustomEvent('explorer:toast', {
          detail: {
            kind: 'error',
            title: 'Application Error',
            message: `Something went wrong: ${error.message}`,
            durationMs: 8000,
          },
        })
      );
    } catch {
      // Ignore toast errors
    }
  }

  render() {
    if (this.state.hasError) {
      // Custom fallback or default error UI
      if (this.props.fallback) {
        return this.props.fallback;
      }

      return <DefaultErrorFallback error={this.state.error} />;
    }

    return this.props.children;
  }
}

/**
 * Default fallback UI shown when an error is caught.
 */
function DefaultErrorFallback({ error }: { error: Error | null }) {
  const handleReload = () => {
    window.location.reload();
  };

  const handleReset = () => {
    // Clear localStorage and reload
    try {
      localStorage.clear();
    } catch {
      // Ignore if localStorage is unavailable
    }
    window.location.reload();
  };

  return (
    <div style={styles.container}>
      <div style={styles.card}>
        <div style={styles.iconContainer}>
          <svg
            width="64"
            height="64"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            style={styles.icon}
          >
            <circle cx="12" cy="12" r="10" />
            <line x1="12" y1="8" x2="12" y2="12" />
            <line x1="12" y1="16" x2="12.01" y2="16" />
          </svg>
        </div>

        <h1 style={styles.title}>Something went wrong</h1>
        
        <p style={styles.message}>
          The explorer encountered an unexpected error and couldn't recover automatically.
        </p>

        {error && (
          <details style={styles.details}>
            <summary style={styles.summary}>Error details</summary>
            <pre style={styles.pre}>
              <code>{error.message}</code>
              {error.stack && (
                <>
                  {'\n\n'}
                  <code style={styles.stack}>{error.stack}</code>
                </>
              )}
            </pre>
          </details>
        )}

        <div style={styles.actions}>
          <button onClick={handleReload} style={styles.primaryButton}>
            Reload Page
          </button>
          <button onClick={handleReset} style={styles.secondaryButton}>
            Reset & Reload
          </button>
        </div>

        <p style={styles.hint}>
          💡 If this error persists, check the browser console (F12) for more details
          or try clearing your browser cache.
        </p>
      </div>
    </div>
  );
}

// Inline styles to ensure error UI always renders correctly
const styles: Record<string, React.CSSProperties> = {
  container: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    minHeight: '100vh',
    padding: '2rem',
    backgroundColor: '#0b0c10',
    color: '#e8edf2',
  },
  card: {
    maxWidth: '600px',
    width: '100%',
    backgroundColor: '#111318',
    border: '1px solid #1e222b',
    borderRadius: '0.8rem',
    padding: '3rem 2rem',
    textAlign: 'center',
    boxShadow: '0 20px 60px rgba(0,0,0,.35)',
  },
  iconContainer: {
    display: 'flex',
    justifyContent: 'center',
    marginBottom: '1.5rem',
  },
  icon: {
    color: '#ff6b6b',
  },
  title: {
    fontSize: '1.75rem',
    fontWeight: '700',
    marginBottom: '1rem',
    color: '#e8edf2',
  },
  message: {
    fontSize: '1rem',
    lineHeight: '1.5',
    color: '#a3adba',
    marginBottom: '1.5rem',
  },
  details: {
    textAlign: 'left',
    marginTop: '1.5rem',
    marginBottom: '1.5rem',
    backgroundColor: '#0b0c10',
    border: '1px solid #1e222b',
    borderRadius: '0.5rem',
    padding: '1rem',
  },
  summary: {
    cursor: 'pointer',
    fontWeight: '600',
    marginBottom: '0.5rem',
    color: '#e8edf2',
  },
  pre: {
    margin: '0.5rem 0 0 0',
    padding: '0',
    overflow: 'auto',
    fontSize: '0.85rem',
    lineHeight: '1.4',
  },
  stack: {
    color: '#a3adba',
    fontSize: '0.75rem',
  },
  actions: {
    display: 'flex',
    gap: '1rem',
    justifyContent: 'center',
    marginTop: '2rem',
    marginBottom: '1.5rem',
  },
  primaryButton: {
    backgroundColor: '#40a9ff',
    color: '#ffffff',
    border: 'none',
    borderRadius: '0.5rem',
    padding: '0.75rem 1.5rem',
    fontSize: '1rem',
    fontWeight: '600',
    cursor: 'pointer',
    transition: 'all 0.2s ease',
  },
  secondaryButton: {
    backgroundColor: 'transparent',
    color: '#e8edf2',
    border: '1px solid #1e222b',
    borderRadius: '0.5rem',
    padding: '0.75rem 1.5rem',
    fontSize: '1rem',
    fontWeight: '600',
    cursor: 'pointer',
    transition: 'all 0.2s ease',
  },
  hint: {
    fontSize: '0.85rem',
    color: '#a3adba',
    marginTop: '1rem',
  },
};
