import React, { useState } from 'react';

export interface ErrorDisplayProps {
  title?: string;
  message: string;
  details?: string;
  onRetry?: () => void;
  onDismiss?: () => void;
  severity?: 'error' | 'warning' | 'info';
}

/**
 * ErrorDisplay — User-friendly error component with optional details
 * 
 * Usage:
 *   <ErrorDisplay 
 *     message="Failed to load data"
 *     details={error.stack}
 *     onRetry={() => refetch()}
 *   />
 */
export function ErrorDisplay({
  title,
  message,
  details,
  onRetry,
  onDismiss,
  severity = 'error',
}: ErrorDisplayProps) {
  const [showDetails, setShowDetails] = useState(false);

  const icons = {
    error: '❌',
    warning: '⚠️',
    info: 'ℹ️',
  };

  return (
    <div className={`error-display error-display--${severity}`}>
      <div className="error-display-header">
        <span className="error-display-icon">{icons[severity]}</span>
        <div className="error-display-text">
          {title && <h3 className="error-display-title">{title}</h3>}
          <p className="error-display-message">{message}</p>
        </div>
        {onDismiss && (
          <button
            className="error-display-close"
            onClick={onDismiss}
            aria-label="Dismiss"
          >
            ×
          </button>
        )}
      </div>

      {details && (
        <div className="error-display-details-wrapper">
          <button
            className="error-display-details-toggle"
            onClick={() => setShowDetails(!showDetails)}
          >
            {showDetails ? '▼' : '▶'} Show Details
          </button>
          {showDetails && (
            <pre className="error-display-details">{details}</pre>
          )}
        </div>
      )}

      {onRetry && (
        <div className="error-display-actions">
          <button className="error-display-retry" onClick={onRetry}>
            Try Again
          </button>
        </div>
      )}

      <style>{`
        .error-display {
          border-radius: var(--radius-md);
          padding: var(--space-4);
          margin: var(--space-4) 0;
          border: 1px solid;
          box-shadow: var(--shadow-sm);
        }
        .error-display--error {
          background: color-mix(in oklab, var(--color-danger) 8%, transparent);
          border-color: color-mix(in oklab, var(--color-danger) 30%, transparent);
        }
        .error-display--warning {
          background: color-mix(in oklab, var(--color-warning) 8%, transparent);
          border-color: color-mix(in oklab, var(--color-warning) 30%, transparent);
        }
        .error-display--info {
          background: color-mix(in oklab, var(--color-accent) 8%, transparent);
          border-color: color-mix(in oklab, var(--color-accent) 30%, transparent);
        }
        .error-display-header {
          display: flex;
          align-items: flex-start;
          gap: var(--space-3);
        }
        .error-display-icon {
          font-size: 24px;
          flex-shrink: 0;
        }
        .error-display-text {
          flex: 1;
        }
        .error-display-title {
          font-size: var(--text-md);
          font-weight: 600;
          margin-bottom: var(--space-1);
          color: var(--color-text-strong);
        }
        .error-display-message {
          color: var(--color-text);
          line-height: 1.5;
          margin: 0;
        }
        .error-display-close {
          background: none;
          border: none;
          font-size: 24px;
          line-height: 1;
          cursor: pointer;
          color: var(--color-text-muted);
          padding: 0;
          width: 24px;
          height: 24px;
          display: flex;
          align-items: center;
          justify-content: center;
          border-radius: var(--radius-sm);
          transition: background 120ms ease, color 120ms ease;
        }
        .error-display-close:hover {
          background: color-mix(in oklab, var(--color-text) 10%, transparent);
          color: var(--color-text-strong);
        }
        .error-display-details-wrapper {
          margin-top: var(--space-3);
        }
        .error-display-details-toggle {
          background: none;
          border: none;
          color: var(--color-text-muted);
          font-size: var(--text-sm);
          cursor: pointer;
          padding: var(--space-2);
          margin-left: calc(32px + var(--space-3));
          border-radius: var(--radius-sm);
          transition: background 120ms ease, color 120ms ease;
        }
        .error-display-details-toggle:hover {
          background: color-mix(in oklab, var(--color-text) 8%, transparent);
          color: var(--color-text);
        }
        .error-display-details {
          background: var(--color-surface);
          border: 1px solid var(--color-border);
          border-radius: var(--radius-sm);
          padding: var(--space-3);
          margin: var(--space-2) 0 0 calc(32px + var(--space-3));
          font-family: var(--font-mono);
          font-size: var(--text-xs);
          color: var(--color-text-muted);
          overflow: auto;
          max-height: 200px;
          white-space: pre-wrap;
          word-break: break-word;
        }
        .error-display-actions {
          display: flex;
          gap: var(--space-2);
          margin-top: var(--space-4);
          margin-left: calc(32px + var(--space-3));
        }
        .error-display-retry {
          padding: var(--space-2) var(--space-4);
          background: var(--color-accent);
          color: var(--color-on-accent);
          border: none;
          border-radius: var(--radius-sm);
          font-weight: 600;
          font-size: var(--text-sm);
          cursor: pointer;
          transition: transform 120ms ease, box-shadow 120ms ease;
        }
        .error-display-retry:hover {
          transform: translateY(-1px);
          box-shadow: var(--shadow-sm);
        }
        .error-display-retry:active {
          transform: translateY(0);
        }
      `}</style>
    </div>
  );
}

export interface NetworkErrorBannerProps {
  rpcUrl: string;
  wsUrl?: string;
  onOpenSettings?: () => void;
}

/**
 * NetworkErrorBanner — Specific banner for network connection issues
 */
export function NetworkErrorBanner({ rpcUrl, wsUrl, onOpenSettings }: NetworkErrorBannerProps) {
  return (
    <div className="network-error-banner">
      <div className="network-error-content">
        <span className="network-error-icon">⚠️</span>
        <div className="network-error-text">
          <strong>Network Connection Issue</strong>
          <p>Unable to reach the RPC endpoint: {rpcUrl}</p>
          {wsUrl && <p className="network-error-secondary">WebSocket: {wsUrl}</p>}
        </div>
      </div>
      {onOpenSettings && (
        <button className="network-error-button" onClick={onOpenSettings}>
          Network Settings
        </button>
      )}

      <style>{`
        .network-error-banner {
          background: color-mix(in oklab, var(--color-warning) 12%, transparent);
          border: 1px solid color-mix(in oklab, var(--color-warning) 40%, transparent);
          border-radius: var(--radius-md);
          padding: var(--space-4);
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: var(--space-4);
          margin: var(--space-4);
          box-shadow: var(--shadow-sm);
        }
        .network-error-content {
          display: flex;
          align-items: flex-start;
          gap: var(--space-3);
          flex: 1;
        }
        .network-error-icon {
          font-size: 24px;
        }
        .network-error-text strong {
          display: block;
          font-weight: 600;
          margin-bottom: var(--space-1);
          color: var(--color-text-strong);
        }
        .network-error-text p {
          margin: 0;
          font-size: var(--text-sm);
          color: var(--color-text);
          font-family: var(--font-mono);
        }
        .network-error-secondary {
          color: var(--color-text-muted);
          margin-top: var(--space-1);
        }
        .network-error-button {
          padding: var(--space-2) var(--space-4);
          background: var(--color-warning);
          color: #000;
          border: none;
          border-radius: var(--radius-sm);
          font-weight: 600;
          font-size: var(--text-sm);
          cursor: pointer;
          white-space: nowrap;
          transition: transform 120ms ease;
        }
        .network-error-button:hover {
          transform: translateY(-1px);
        }
        @media (max-width: 640px) {
          .network-error-banner {
            flex-direction: column;
            align-items: flex-start;
          }
          .network-error-button {
            width: 100%;
          }
        }
      `}</style>
    </div>
  );
}

export default {
  ErrorDisplay,
  NetworkErrorBanner,
};
