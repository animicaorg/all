/**
 * useMempool hook - Fetches mempool status and transactions
 */

import { useQuery } from '@tanstack/react-query';
import { getEnhancedRpcClient } from '../../lib/rpc/client';
import { queryKeys } from '../../lib/query/queryClient';
import type { MempoolStatus } from '../../lib/rpc/schemas';

export interface UseMempoolOptions {
  rpcUrl: string;
  enabled?: boolean;
  refetchInterval?: number;
}

export function useMempool(options: UseMempoolOptions) {
  const { rpcUrl, enabled = true, refetchInterval = 5000 } = options;

  return useQuery({
    queryKey: queryKeys.mempool(),
    queryFn: async (): Promise<MempoolStatus | null> => {
      const client = getEnhancedRpcClient(rpcUrl);
      return client.getMempool();
    },
    enabled,
    staleTime: 2000, // Mempool changes frequently
    refetchInterval, // Auto-refresh mempool
  });
}
