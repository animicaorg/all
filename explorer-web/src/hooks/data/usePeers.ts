/**
 * usePeers hook - Fetches network peers information
 */

import { useQuery } from '@tanstack/react-query';
import { getEnhancedRpcClient } from '../../lib/rpc/client';
import { queryKeys } from '../../lib/query/queryClient';
import type { PeersStatus } from '../../lib/rpc/schemas';

export interface UsePeersOptions {
  rpcUrl: string;
  enabled?: boolean;
  refetchInterval?: number;
}

export function usePeers(options: UsePeersOptions) {
  const { rpcUrl, enabled = true, refetchInterval = 10000 } = options;

  return useQuery({
    queryKey: queryKeys.peers(),
    queryFn: async (): Promise<PeersStatus | null> => {
      const client = getEnhancedRpcClient(rpcUrl);
      return client.getPeers();
    },
    enabled,
    staleTime: 5000, // Peers change occasionally
    refetchInterval, // Auto-refresh peers
  });
}
