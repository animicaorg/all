/**
 * Enhanced HomePage with TanStack Query hooks
 * 
 * This is a demo/partial implementation showing how to integrate the new data hooks.
 * The full HomePage integration would replace all Zustand store usage with these hooks.
 */

import React from 'react';
import { Link } from 'react-router-dom';
import { cn } from '../../utils/classnames';
import { shortHash } from '../../utils/format';
import { ago } from '../../utils/time';
import { useHead, useChainStatus } from '../../hooks/data';
import { useReorgHandler } from '../../components/sync/ReorgHandler';
import { SyncBanner } from '../../components/sync/SyncBanner';
import { resolveRpcUrl } from '../../config/rpcUrl';

const StatCard: React.FC<{
  label: string;
  value: React.ReactNode;
  hint?: React.ReactNode;
  className?: string;
  icon?: React.ReactNode;
}> = ({ label, value, hint, className, icon }) => (
  <div className={cn("card", "p-5 space-y-2 hover:shadow-md transition-shadow duration-200", className)}>
    <div className="flex items-center justify-between">
      <div className="text-xs uppercase tracking-wide text-muted font-semibold">{label}</div>
      {icon && <div className="text-accent opacity-70">{icon}</div>}
    </div>
    <div className="text-2xl font-bold tabular-nums">{value}</div>
    {hint ? <div className="text-xs text-muted">{hint}</div> : null}
  </div>
);

/**
 * Example enhanced component showing how to use the new hooks.
 * This should eventually replace the full HomePage.tsx
 */
export function EnhancedHomeStats() {
  const rpcUrl = resolveRpcUrl();
  const { handleReorg } = useReorgHandler();
  
  // Use new data hooks
  const { data: head, isSubscribed, isLoading: headLoading } = useHead({
    rpcUrl,
    onReorg: handleReorg,
  });
  
  const { data: chainStatus, isLoading: statusLoading } = useChainStatus({
    rpcUrl,
  });

  const isLoading = headLoading || statusLoading;

  return (
    <div className="space-y-6">
      {/* Sync Banner - only shows if syncing */}
      <SyncBanner
        syncPhase={chainStatus?.syncPhase}
        syncProgress={chainStatus?.syncProgress}
        peers={chainStatus?.peers}
      />

      <div className="space-y-4">
        <div className="flex items-center justify-between">
          <h2 className="text-xl font-bold tracking-tight">Network Status</h2>
          {isSubscribed && (
            <div className="flex items-center gap-2 text-sm text-green-600 dark:text-green-400">
              <span className="flex h-2 w-2 rounded-full bg-green-600 dark:bg-green-400 animate-pulse"></span>
              <span>Live</span>
            </div>
          )}
        </div>

        <div className="grid gap-4 grid-cols-1 sm:grid-cols-2 lg:grid-cols-4">
          <StatCard
            label="Head Height"
            icon={
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <rect x="3" y="3" width="7" height="7" rx="1" />
                <rect x="14" y="3" width="7" height="7" rx="1" />
                <rect x="14" y="14" width="7" height="7" rx="1" />
                <rect x="3" y="14" width="7" height="7" rx="1" />
              </svg>
            }
            value={
              isLoading ? (
                <div className="animate-pulse bg-gray-200 dark:bg-gray-700 h-8 w-24 rounded"></div>
              ) : head?.height !== undefined ? (
                <Link
                  to={`/blocks/${head.height}`}
                  className="hover:text-accent transition-colors"
                >
                  {head.height.toLocaleString()}
                </Link>
              ) : (
                <span className="text-muted">—</span>
              )
            }
            hint={
              head?.timeISO
                ? `${ago(new Date(head.timeISO).getTime())} ago`
                : "Waiting for data..."
            }
          />

          <StatCard
            label="Latest Hash"
            icon={
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M4 7h16M4 12h16M4 17h16" />
              </svg>
            }
            value={
              isLoading ? (
                <div className="animate-pulse bg-gray-200 dark:bg-gray-700 h-8 w-32 rounded"></div>
              ) : head?.hash ? (
                <span className="font-mono text-sm">
                  {shortHash(head.hash)}
                </span>
              ) : (
                <span className="text-muted">—</span>
              )
            }
            hint="Block hash"
          />

          <StatCard
            label="Chain ID"
            icon={
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M12 2L2 7l10 5 10-5-10-5z" />
                <path d="M2 17l10 5 10-5M2 12l10 5 10-5" />
              </svg>
            }
            value={
              isLoading ? (
                <div className="animate-pulse bg-gray-200 dark:bg-gray-700 h-8 w-16 rounded"></div>
              ) : chainStatus?.chainId ? (
                <span className="font-mono">{chainStatus.chainId}</span>
              ) : (
                <span className="text-muted">—</span>
              )
            }
            hint={chainStatus?.networkName || 'Network identifier'}
          />

          <StatCard
            label="Sync Status"
            icon={
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <circle cx="12" cy="12" r="10" />
                <polyline points="12 6 12 12 16 14" />
              </svg>
            }
            value={
              isLoading ? (
                <div className="animate-pulse bg-gray-200 dark:bg-gray-700 h-8 w-24 rounded"></div>
              ) : chainStatus?.syncPhase ? (
                <span className={cn(
                  'capitalize',
                  chainStatus.syncPhase === 'fully-synced' ? 'text-green-600 dark:text-green-400' : 'text-yellow-600 dark:text-yellow-400'
                )}>
                  {chainStatus.syncPhase.replace(/-/g, ' ')}
                </span>
              ) : (
                <span className="text-muted">Unknown</span>
              )
            }
            hint={
              chainStatus?.syncProgress !== undefined
                ? `${Math.round(chainStatus.syncProgress * 100)}% complete`
                : 'Sync phase'
            }
          />
        </div>
      </div>
    </div>
  );
}
