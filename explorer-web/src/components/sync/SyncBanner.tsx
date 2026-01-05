/**
 * SyncBanner - Displays a prominent banner when node is syncing
 * 
 * Shows sync status, progress, and warnings about incomplete data
 */

import React from 'react';

export interface SyncBannerProps {
  syncPhase?: string;
  syncProgress?: number;
  peers?: number;
  className?: string;
}

export function SyncBanner({ syncPhase, syncProgress, peers, className = '' }: SyncBannerProps) {
  // Only show banner if not fully synced
  if (!syncPhase || syncPhase === 'fully-synced' || syncPhase === 'idle') {
    return null;
  }

  const isActive = syncPhase === 'syncing' || syncPhase === 'catching-up' || syncPhase === 'headers';
  const progressPercent = syncProgress ? Math.round(syncProgress * 100) : undefined;

  return (
    <div
      className={`sync-banner ${isActive ? 'sync-banner--active' : 'sync-banner--warning'} ${className}`}
      role="alert"
      aria-live="polite"
    >
      <div className="sync-banner__content">
        <div className="sync-banner__icon">
          {isActive ? (
            <svg className="sync-banner__spinner" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
              <circle cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="3" strokeDasharray="60" strokeLinecap="round" />
            </svg>
          ) : (
            <svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
              <path d="M12 9v4l2 2m6-2a8 8 0 11-16 0 8 8 0 0116 0z" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
            </svg>
          )}
        </div>
        
        <div className="sync-banner__text">
          <div className="sync-banner__title">
            {isActive ? '⚡ Node is syncing' : '⏸️ Node sync in progress'}
          </div>
          <div className="sync-banner__details">
            <span className="sync-banner__phase">
              Phase: <strong>{syncPhase}</strong>
            </span>
            {progressPercent !== undefined && (
              <span className="sync-banner__progress">
                Progress: <strong>{progressPercent}%</strong>
              </span>
            )}
            {peers !== undefined && (
              <span className="sync-banner__peers">
                Peers: <strong>{peers}</strong>
              </span>
            )}
          </div>
          <div className="sync-banner__warning">
            ⚠️ Blockchain data may be incomplete. Features that rely on recent blocks may show partial information.
          </div>
        </div>
      </div>

      <style>{`
        .sync-banner {
          position: sticky;
          top: 0;
          z-index: 100;
          border-radius: 0.5rem;
          padding: 1rem 1.5rem;
          margin-bottom: 1rem;
          animation: slideDown 0.3s ease-out;
        }

        .sync-banner--active {
          background: linear-gradient(135deg, #3b82f6 0%, #2563eb 100%);
          color: white;
          box-shadow: 0 4px 6px -1px rgba(59, 130, 246, 0.3);
        }

        .sync-banner--warning {
          background: linear-gradient(135deg, #f59e0b 0%, #d97706 100%);
          color: white;
          box-shadow: 0 4px 6px -1px rgba(245, 158, 11, 0.3);
        }

        .sync-banner__content {
          display: flex;
          align-items: flex-start;
          gap: 1rem;
        }

        .sync-banner__icon {
          flex-shrink: 0;
          width: 2rem;
          height: 2rem;
        }

        .sync-banner__icon svg {
          width: 100%;
          height: 100%;
        }

        .sync-banner__spinner {
          animation: spin 1.5s linear infinite;
        }

        @keyframes spin {
          from {
            transform: rotate(0deg);
          }
          to {
            transform: rotate(360deg);
          }
        }

        @keyframes slideDown {
          from {
            opacity: 0;
            transform: translateY(-1rem);
          }
          to {
            opacity: 1;
            transform: translateY(0);
          }
        }

        .sync-banner__text {
          flex: 1;
          min-width: 0;
        }

        .sync-banner__title {
          font-size: 1.125rem;
          font-weight: 600;
          margin-bottom: 0.5rem;
        }

        .sync-banner__details {
          display: flex;
          flex-wrap: wrap;
          gap: 1rem;
          font-size: 0.875rem;
          margin-bottom: 0.5rem;
          opacity: 0.95;
        }

        .sync-banner__details > span {
          display: inline-flex;
          align-items: center;
          gap: 0.25rem;
        }

        .sync-banner__details strong {
          font-weight: 700;
        }

        .sync-banner__warning {
          font-size: 0.875rem;
          opacity: 0.9;
          margin-top: 0.5rem;
          padding-top: 0.5rem;
          border-top: 1px solid rgba(255, 255, 255, 0.2);
        }

        @media (max-width: 640px) {
          .sync-banner {
            padding: 0.75rem 1rem;
          }

          .sync-banner__content {
            gap: 0.75rem;
          }

          .sync-banner__icon {
            width: 1.5rem;
            height: 1.5rem;
          }

          .sync-banner__title {
            font-size: 1rem;
          }

          .sync-banner__details {
            flex-direction: column;
            gap: 0.25rem;
          }
        }
      `}</style>
    </div>
  );
}
