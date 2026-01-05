/**
 * useBlock hook - Fetches block data by height or hash
 */

import { useQuery } from '@tanstack/react-query';
import { getEnhancedRpcClient } from '../../lib/rpc/client';
import { queryKeys } from '../../lib/query/queryClient';
import type { BlockDetail } from '../../lib/rpc/schemas';

export interface UseBlockOptions {
  rpcUrl: string;
  heightOrHash: number | string | null | undefined;
  enabled?: boolean;
}

export function useBlock(options: UseBlockOptions) {
  const { rpcUrl, heightOrHash, enabled = true } = options;

  return useQuery({
    queryKey: queryKeys.block(heightOrHash!),
    queryFn: async (): Promise<BlockDetail> => {
      if (heightOrHash === null || heightOrHash === undefined) {
        throw new Error('Block height or hash is required');
      }

      const client = getEnhancedRpcClient(rpcUrl);
      return client.getBlock(heightOrHash);
    },
    enabled: enabled && heightOrHash !== null && heightOrHash !== undefined,
    staleTime: 30000, // Blocks are relatively stable after a few confirmations
  });
}

/**
 * useBlocks hook - Fetches multiple blocks in a range
 */
export interface UseBlocksOptions {
  rpcUrl: string;
  fromHeight?: number;
  limit?: number;
  enabled?: boolean;
}

export function useBlocks(options: UseBlocksOptions) {
  const { rpcUrl, fromHeight, limit = 20, enabled = true } = options;

  return useQuery({
    queryKey: queryKeys.blocks({ fromHeight, limit }),
    queryFn: async (): Promise<BlockDetail[]> => {
      if (fromHeight === undefined) {
        throw new Error('fromHeight is required');
      }

      const client = getEnhancedRpcClient(rpcUrl);
      return client.getBlocks(fromHeight, limit);
    },
    enabled: enabled && fromHeight !== undefined,
    staleTime: 10000, // Recent blocks change frequently
  });
}
