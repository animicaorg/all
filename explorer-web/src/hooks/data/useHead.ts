/**
 * useHead hook - Fetches and subscribes to chain head updates
 * 
 * Features:
 * - Auto-subscribes to new heads via WebSocket when available
 * - Falls back to polling if WebSocket not available
 * - Detects reorgs by comparing head hash at same height
 * - Invalidates related queries on head updates
 */

import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';
import { getEnhancedRpcClient } from '../../lib/rpc/client';
import { queryKeys, invalidateHeadRelatedQueries, invalidateBlockQueries } from '../../lib/query/queryClient';
import type { ChainHead } from '../../lib/rpc/schemas';

export interface UseHeadOptions {
  rpcUrl: string;
  enabled?: boolean;
  pollingInterval?: number; // Fallback polling interval in ms (default: 4000)
  onReorg?: (oldHead: ChainHead, newHead: ChainHead) => void;
}

export function useHead(options: UseHeadOptions) {
  const { rpcUrl, enabled = true, pollingInterval = 4000, onReorg } = options;
  const queryClient = useQueryClient();
  const [isSubscribed, setIsSubscribed] = useState(false);
  const previousHeadRef = useRef<ChainHead | null>(null);

  // Query for fetching head
  const query = useQuery({
    queryKey: queryKeys.head,
    queryFn: async () => {
      const client = getEnhancedRpcClient(rpcUrl);
      return client.getHead();
    },
    enabled,
    refetchInterval: isSubscribed ? false : pollingInterval, // Only poll if not subscribed
    refetchIntervalInBackground: true,
  });

  // Set up WebSocket subscription for live updates
  useEffect(() => {
    if (!enabled || !rpcUrl) {
      return;
    }

    const client = getEnhancedRpcClient(rpcUrl);
    let unsubscribe: (() => void) | null = null;

    try {
      const subscription = client.subscribeNewHeads(
        (newHead) => {
          // Check for reorg
          const previousHead = previousHeadRef.current;
          if (
            previousHead &&
            previousHead.height === newHead.height &&
            previousHead.hash !== newHead.hash
          ) {
            console.warn('[useHead] Reorg detected!', {
              old: previousHead,
              new: newHead,
            });
            onReorg?.(previousHead, newHead);
            
            // Invalidate recent blocks on reorg
            for (let i = 0; i < 10; i++) {
              invalidateBlockQueries(newHead.height - i);
            }
          }

          previousHeadRef.current = newHead;

          // Update the query data
          queryClient.setQueryData(queryKeys.head, newHead);

          // Invalidate related queries
          invalidateHeadRelatedQueries();

          setIsSubscribed(true);
        },
        (error) => {
          console.error('[useHead] Subscription error:', error);
          setIsSubscribed(false);
        }
      );

      unsubscribe = subscription.unsubscribe;

      console.log('[useHead] WebSocket subscription active');
    } catch (error) {
      console.warn('[useHead] Failed to subscribe, falling back to polling:', error);
      setIsSubscribed(false);
    }

    return () => {
      if (unsubscribe) {
        unsubscribe();
        setIsSubscribed(false);
      }
    };
  }, [rpcUrl, enabled, queryClient, onReorg]);

  // Update previous head ref when query data changes
  useEffect(() => {
    if (query.data) {
      previousHeadRef.current = query.data;
    }
  }, [query.data]);

  return {
    ...query,
    isSubscribed,
  };
}
