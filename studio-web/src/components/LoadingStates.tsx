import React from 'react';

/**
 * LoadingStates — Collection of loading/skeleton components
 * 
 * Usage:
 *   <LoadingSpinner />
 *   <LoadingSkeleton />
 *   <LoadingCard />
 */

export interface LoadingSpinnerProps {
  size?: 'sm' | 'md' | 'lg';
  label?: string;
}

export function LoadingSpinner({ size = 'md', label }: LoadingSpinnerProps) {
  const sizeClass = {
    sm: 'spinner-sm',
    md: 'spinner-md',
    lg: 'spinner-lg',
  }[size];

  return (
    <div className="loading-spinner-wrapper">
      <div className={`loading-spinner ${sizeClass}`}>
        <div className="spinner-ring" />
      </div>
      {label && <p className="loading-label">{label}</p>}

      <style>{`
        .loading-spinner-wrapper {
          display: flex;
          flex-direction: column;
          align-items: center;
          gap: var(--space-3);
        }
        .loading-spinner {
          position: relative;
          display: inline-block;
        }
        .loading-spinner.spinner-sm {
          width: 20px;
          height: 20px;
        }
        .loading-spinner.spinner-md {
          width: 32px;
          height: 32px;
        }
        .loading-spinner.spinner-lg {
          width: 48px;
          height: 48px;
        }
        .spinner-ring {
          position: absolute;
          inset: 0;
          border: 3px solid color-mix(in oklab, var(--color-accent) 20%, transparent);
          border-top-color: var(--color-accent);
          border-radius: 50%;
          animation: spin 800ms linear infinite;
        }
        .loading-spinner.spinner-sm .spinner-ring {
          border-width: 2px;
        }
        .loading-label {
          color: var(--color-text-muted);
          font-size: var(--text-sm);
        }
        @keyframes spin {
          to {
            transform: rotate(360deg);
          }
        }
      `}</style>
    </div>
  );
}

export interface LoadingSkeletonProps {
  width?: string | number;
  height?: string | number;
  variant?: 'text' | 'rect' | 'circle';
  count?: number;
}

export function LoadingSkeleton({ 
  width = '100%', 
  height = '1em', 
  variant = 'text',
  count = 1 
}: LoadingSkeletonProps) {
  const items = Array.from({ length: count }, (_, i) => i);
  
  const getStyle = () => {
    const baseStyle: React.CSSProperties = {
      width: typeof width === 'number' ? `${width}px` : width,
      height: typeof height === 'number' ? `${height}px` : height,
    };

    if (variant === 'circle') {
      return { ...baseStyle, borderRadius: '50%', aspectRatio: '1' };
    }
    if (variant === 'rect') {
      return { ...baseStyle, borderRadius: 'var(--radius-md)' };
    }
    // text variant
    return { ...baseStyle, borderRadius: 'var(--radius-sm)' };
  };

  return (
    <>
      {items.map((i) => (
        <div key={i} className="skeleton" style={getStyle()} />
      ))}

      <style>{`
        .skeleton {
          background: linear-gradient(
            90deg,
            color-mix(in oklab, var(--color-surface) 85%, var(--color-border)),
            color-mix(in oklab, var(--color-surface) 95%, var(--color-border)),
            color-mix(in oklab, var(--color-surface) 85%, var(--color-border))
          );
          background-size: 200% 100%;
          animation: skeleton-shimmer 1.5s ease-in-out infinite;
        }
        .skeleton + .skeleton {
          margin-top: var(--space-2);
        }
        @keyframes skeleton-shimmer {
          0% {
            background-position: 200% 0;
          }
          100% {
            background-position: -200% 0;
          }
        }
      `}</style>
    </>
  );
}

export interface LoadingCardProps {
  lines?: number;
  hasHeader?: boolean;
}

export function LoadingCard({ lines = 3, hasHeader = true }: LoadingCardProps) {
  return (
    <div className="loading-card">
      {hasHeader && (
        <div className="loading-card-header">
          <LoadingSkeleton width="40%" height="24px" variant="rect" />
        </div>
      )}
      <div className="loading-card-body">
        {Array.from({ length: lines }, (_, i) => (
          <LoadingSkeleton 
            key={i} 
            width={i === lines - 1 ? '70%' : '100%'} 
            height="16px" 
          />
        ))}
      </div>

      <style>{`
        .loading-card {
          background: var(--color-surface);
          border: 1px solid var(--color-border);
          border-radius: var(--radius-lg);
          overflow: hidden;
        }
        .loading-card-header {
          padding: var(--space-4) var(--space-5);
          border-bottom: 1px solid var(--color-border);
        }
        .loading-card-body {
          padding: var(--space-5);
          display: flex;
          flex-direction: column;
          gap: var(--space-3);
        }
      `}</style>
    </div>
  );
}

export interface LoadingOverlayProps {
  visible: boolean;
  label?: string;
}

export function LoadingOverlay({ visible, label }: LoadingOverlayProps) {
  if (!visible) return null;

  return (
    <div className="loading-overlay">
      <div className="loading-overlay-content">
        <LoadingSpinner size="lg" label={label} />
      </div>

      <style>{`
        .loading-overlay {
          position: fixed;
          inset: 0;
          background: color-mix(in oklab, var(--color-bg) 80%, transparent);
          backdrop-filter: blur(4px);
          display: flex;
          align-items: center;
          justify-content: center;
          z-index: 9999;
          animation: fadeIn 200ms ease;
        }
        .loading-overlay-content {
          background: var(--color-surface);
          padding: var(--space-8);
          border-radius: var(--radius-lg);
          box-shadow: var(--shadow-lg);
          border: 1px solid var(--color-border);
        }
        @keyframes fadeIn {
          from {
            opacity: 0;
          }
          to {
            opacity: 1;
          }
        }
      `}</style>
    </div>
  );
}

export interface EmptyStateProps {
  icon?: string;
  title: string;
  message?: string;
  action?: {
    label: string;
    onClick: () => void;
  };
}

export function EmptyState({ icon = '📭', title, message, action }: EmptyStateProps) {
  return (
    <div className="empty-state">
      <div className="empty-state-icon">{icon}</div>
      <h3 className="empty-state-title">{title}</h3>
      {message && <p className="empty-state-message">{message}</p>}
      {action && (
        <button className="empty-state-button" onClick={action.onClick}>
          {action.label}
        </button>
      )}

      <style>{`
        .empty-state {
          display: flex;
          flex-direction: column;
          align-items: center;
          justify-content: center;
          padding: var(--space-12) var(--space-6);
          text-align: center;
        }
        .empty-state-icon {
          font-size: 48px;
          margin-bottom: var(--space-4);
          opacity: 0.8;
        }
        .empty-state-title {
          font-size: var(--text-xl);
          font-weight: 600;
          color: var(--color-text-strong);
          margin-bottom: var(--space-2);
        }
        .empty-state-message {
          color: var(--color-text-muted);
          max-width: 400px;
          line-height: 1.6;
          margin-bottom: var(--space-6);
        }
        .empty-state-button {
          padding: var(--space-3) var(--space-5);
          background: var(--color-accent);
          color: var(--color-on-accent);
          border: none;
          border-radius: var(--radius-md);
          font-weight: 600;
          cursor: pointer;
          transition: transform 120ms ease, box-shadow 120ms ease;
          box-shadow: var(--shadow-sm);
        }
        .empty-state-button:hover {
          transform: translateY(-1px);
          box-shadow: var(--shadow-md);
        }
      `}</style>
    </div>
  );
}

export default {
  Spinner: LoadingSpinner,
  Skeleton: LoadingSkeleton,
  Card: LoadingCard,
  Overlay: LoadingOverlay,
  EmptyState,
};
