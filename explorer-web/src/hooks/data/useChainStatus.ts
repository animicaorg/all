/**
 * useChainStatus hook - Fetches comprehensive chain status
 */

import { useQuery } from '@tanstack/react-query';
import { getEnhancedRpcClient } from '../../lib/rpc/client';
import { queryKeys } from '../../lib/query/queryClient';
import type { ChainStatus } from '../../lib/rpc/schemas';

export interface UseChainStatusOptions {
  rpcUrl: string;
  enabled?: boolean;
  refetchInterval?: number;
}

export function useChainStatus(options: UseChainStatusOptions) {
  const { rpcUrl, enabled = true, refetchInterval = 10000 } = options;

  return useQuery({
    queryKey: queryKeys.chainStatus,
    queryFn: async (): Promise<ChainStatus> => {
      const client = getEnhancedRpcClient(rpcUrl);
      return client.getChainStatus();
    },
    enabled,
    staleTime: 5000,
    refetchInterval, // Auto-refresh status
  });
}

/**
 * useChainId hook - Fetches chain ID
 */
export interface UseChainIdOptions {
  rpcUrl: string;
  enabled?: boolean;
}

export function useChainId(options: UseChainIdOptions) {
  const { rpcUrl, enabled = true } = options;

  return useQuery({
    queryKey: queryKeys.chainId,
    queryFn: async (): Promise<string> => {
      const client = getEnhancedRpcClient(rpcUrl);
      return client.getChainId();
    },
    enabled,
    staleTime: Infinity, // Chain ID never changes
    gcTime: Infinity, // Keep forever
  });
}
