/**
 * Animica Explorer — Cache Status Component
 * -----------------------------------------------------------------------------
 * Displays cache status, sync progress, and provides cache management controls.
 */

import React, { useEffect, useState } from 'react';
import type { SyncStatus } from '../services/sync';
import type { CacheStats } from '../services/cache';

export interface CacheStatusProps {
  syncStatus: SyncStatus | null;
  cacheAvailable: boolean;
  onClearCache?: () => void;
  getCacheStats?: () => Promise<CacheStats | null>;
  className?: string;
}

export function CacheStatus({
  syncStatus,
  cacheAvailable,
  onClearCache,
  getCacheStats,
  className = '',
}: CacheStatusProps): JSX.Element {
  const [stats, setStats] = useState<CacheStats | null>(null);
  const [showDetails, setShowDetails] = useState(false);
  const [loading, setLoading] = useState(false);

  // Fetch cache stats periodically
  useEffect(() => {
    if (!getCacheStats || !cacheAvailable) return;

    let cancelled = false;

    async function fetchStats() {
      if (cancelled) return;
      try {
        const s = await getCacheStats();
        if (!cancelled && s) setStats(s);
      } catch (err) {
        console.error('[CacheStatus] Failed to fetch stats:', err);
      }
    }

    fetchStats();
    const interval = setInterval(() => {
      fetchStats().catch((e) => {
        console.debug('[CacheStatus] Fetch stats error:', e);
      });
    }, 10000); // Update every 10s

    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [getCacheStats, cacheAvailable]);

  if (!cacheAvailable) {
    return (
      <div className={`cache-status cache-unavailable ${className}`}>
        <div className="cache-status-icon">⚠️</div>
        <div className="cache-status-text">
          <div className="cache-status-title">Cache Unavailable</div>
          <div className="cache-status-subtitle">
            IndexedDB not supported in this browser
          </div>
        </div>
      </div>
    );
  }

  const handleClearCache = async () => {
    if (!onClearCache) return;
    const confirmed = window.confirm(
      'Are you sure you want to clear the cache? This will delete all locally stored blockchain data.'
    );
    if (!confirmed) return;

    setLoading(true);
    try {
      await onClearCache();
      setStats(null);
      // Refresh stats after clearing
      if (getCacheStats) {
        const newStats = await getCacheStats();
        if (newStats) setStats(newStats);
      }
    } finally {
      setLoading(false);
    }
  };

  const renderSyncStatus = () => {
    if (!syncStatus) {
      return <span className="cache-sync-status idle">Idle</span>;
    }

    if (syncStatus.error) {
      return (
        <span className="cache-sync-status error" title={syncStatus.error}>
          Error
        </span>
      );
    }

    if (syncStatus.isSynced) {
      return <span className="cache-sync-status synced">Synced ✓</span>;
    }

    if (syncStatus.isRunning) {
      const progress = Math.round(syncStatus.progress * 100);
      return (
        <span className="cache-sync-status syncing">
          Syncing... {progress}%
          {syncStatus.blocksToSync > 0 && ` (${syncStatus.blocksToSync} blocks)`}
        </span>
      );
    }

    return <span className="cache-sync-status idle">Idle</span>;
  };

  const formatSize = (mb: number) => {
    if (mb < 1) return `${Math.round(mb * 1024)} KB`;
    if (mb < 1024) return `${mb.toFixed(1)} MB`;
    return `${(mb / 1024).toFixed(2)} GB`;
  };

  const formatTime = (ts: number | null) => {
    if (!ts) return 'Never';
    const date = new Date(ts);
    const now = Date.now();
    const diff = now - ts;

    if (diff < 60000) return 'Just now';
    if (diff < 3600000) return `${Math.floor(diff / 60000)}m ago`;
    if (diff < 86400000) return `${Math.floor(diff / 3600000)}h ago`;
    return date.toLocaleDateString();
  };

  return (
    <div className={`cache-status ${className}`}>
      <div className="cache-status-header" onClick={() => setShowDetails(!showDetails)}>
        <div className="cache-status-icon">
          {syncStatus?.isSynced ? '💾' : syncStatus?.isRunning ? '⏳' : '📦'}
        </div>
        <div className="cache-status-text">
          <div className="cache-status-title">Local Cache</div>
          <div className="cache-status-subtitle">{renderSyncStatus()}</div>
        </div>
        <button
          className="cache-status-toggle"
          aria-label={showDetails ? 'Hide details' : 'Show details'}
        >
          {showDetails ? '▼' : '▶'}
        </button>
      </div>

      {showDetails && (
        <div className="cache-status-details">
          {stats && (
            <div className="cache-stats">
              <div className="cache-stat-row">
                <span className="cache-stat-label">Blocks:</span>
                <span className="cache-stat-value">{stats.blocksCount.toLocaleString()}</span>
              </div>
              <div className="cache-stat-row">
                <span className="cache-stat-label">Transactions:</span>
                <span className="cache-stat-value">{stats.txsCount.toLocaleString()}</span>
              </div>
              <div className="cache-stat-row">
                <span className="cache-stat-label">Addresses:</span>
                <span className="cache-stat-value">{stats.addressesCount.toLocaleString()}</span>
              </div>
              <div className="cache-stat-row">
                <span className="cache-stat-label">Size:</span>
                <span className="cache-stat-value">~{formatSize(stats.estimatedSize)}</span>
              </div>
              <div className="cache-stat-row">
                <span className="cache-stat-label">Last Sync:</span>
                <span className="cache-stat-value">
                  Block #{stats.lastSyncHeight?.toLocaleString() ?? 'N/A'}
                  {' '}
                  ({formatTime(stats.lastSyncTime)})
                </span>
              </div>
            </div>
          )}

          {syncStatus && !syncStatus.isSynced && syncStatus.blocksToSync > 0 && (
            <div className="cache-sync-progress">
              <div className="cache-sync-progress-label">
                Sync Progress: {Math.round(syncStatus.progress * 100)}%
              </div>
              <div className="cache-sync-progress-bar">
                <div
                  className="cache-sync-progress-fill"
                  style={{ width: `${syncStatus.progress * 100}%` }}
                />
              </div>
            </div>
          )}

          {onClearCache && (
            <div className="cache-actions">
              <button
                className="cache-action-btn cache-clear-btn"
                onClick={handleClearCache}
                disabled={loading}
              >
                {loading ? 'Clearing...' : 'Clear Cache'}
              </button>
            </div>
          )}
        </div>
      )}

      <style jsx>{`
        .cache-status {
          background: var(--card-bg, #ffffff);
          border: 1px solid var(--border-color, #e5e7eb);
          border-radius: 8px;
          padding: 12px;
          font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
        }

        .cache-status-header {
          display: flex;
          align-items: center;
          gap: 12px;
          cursor: pointer;
          user-select: none;
        }

        .cache-status-icon {
          font-size: 24px;
          line-height: 1;
        }

        .cache-status-text {
          flex: 1;
        }

        .cache-status-title {
          font-weight: 600;
          font-size: 14px;
          color: var(--text-primary, #111827);
          margin-bottom: 2px;
        }

        .cache-status-subtitle {
          font-size: 12px;
          color: var(--text-secondary, #6b7280);
        }

        .cache-status-toggle {
          background: none;
          border: none;
          color: var(--text-secondary, #6b7280);
          cursor: pointer;
          padding: 4px;
          font-size: 12px;
        }

        .cache-sync-status {
          display: inline-block;
          padding: 2px 8px;
          border-radius: 4px;
          font-size: 11px;
          font-weight: 500;
        }

        .cache-sync-status.synced {
          background: #d1fae5;
          color: #065f46;
        }

        .cache-sync-status.syncing {
          background: #fef3c7;
          color: #92400e;
        }

        .cache-sync-status.error {
          background: #fee2e2;
          color: #991b1b;
        }

        .cache-sync-status.idle {
          background: #f3f4f6;
          color: #6b7280;
        }

        .cache-status-details {
          margin-top: 12px;
          padding-top: 12px;
          border-top: 1px solid var(--border-color, #e5e7eb);
        }

        .cache-stats {
          display: flex;
          flex-direction: column;
          gap: 6px;
          margin-bottom: 12px;
        }

        .cache-stat-row {
          display: flex;
          justify-content: space-between;
          font-size: 12px;
        }

        .cache-stat-label {
          color: var(--text-secondary, #6b7280);
        }

        .cache-stat-value {
          font-weight: 500;
          color: var(--text-primary, #111827);
        }

        .cache-sync-progress {
          margin-bottom: 12px;
        }

        .cache-sync-progress-label {
          font-size: 11px;
          color: var(--text-secondary, #6b7280);
          margin-bottom: 6px;
        }

        .cache-sync-progress-bar {
          height: 6px;
          background: var(--progress-bg, #e5e7eb);
          border-radius: 3px;
          overflow: hidden;
        }

        .cache-sync-progress-fill {
          height: 100%;
          background: linear-gradient(90deg, #3b82f6, #8b5cf6);
          transition: width 0.3s ease;
        }

        .cache-actions {
          display: flex;
          gap: 8px;
        }

        .cache-action-btn {
          flex: 1;
          padding: 8px 12px;
          border: 1px solid var(--border-color, #e5e7eb);
          border-radius: 6px;
          background: var(--button-bg, #ffffff);
          color: var(--text-primary, #111827);
          font-size: 12px;
          font-weight: 500;
          cursor: pointer;
          transition: all 0.2s;
        }

        .cache-action-btn:hover:not(:disabled) {
          background: var(--button-hover-bg, #f9fafb);
          border-color: var(--border-hover, #d1d5db);
        }

        .cache-action-btn:disabled {
          opacity: 0.5;
          cursor: not-allowed;
        }

        .cache-clear-btn {
          color: #dc2626;
          border-color: #fecaca;
        }

        .cache-clear-btn:hover:not(:disabled) {
          background: #fef2f2;
          border-color: #fca5a5;
        }

        .cache-unavailable {
          border-color: #fbbf24;
          background: #fffbeb;
        }

        @media (prefers-color-scheme: dark) {
          .cache-status {
            --card-bg: #1f2937;
            --border-color: #374151;
            --text-primary: #f9fafb;
            --text-secondary: #9ca3af;
            --button-bg: #374151;
            --button-hover-bg: #4b5563;
            --border-hover: #6b7280;
            --progress-bg: #374151;
          }

          .cache-sync-status.synced {
            background: #065f46;
            color: #d1fae5;
          }

          .cache-sync-status.syncing {
            background: #92400e;
            color: #fef3c7;
          }

          .cache-sync-status.error {
            background: #991b1b;
            color: #fee2e2;
          }

          .cache-unavailable {
            background: #422006;
            border-color: #78350f;
          }
        }
      `}</style>
    </div>
  );
}

export default CacheStatus;
