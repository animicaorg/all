/**
 * useTx hook - Fetches transaction data by hash
 */

import { useQuery } from '@tanstack/react-query';
import { getEnhancedRpcClient } from '../../lib/rpc/client';
import { queryKeys } from '../../lib/query/queryClient';
import type { TxDetail } from '../../lib/rpc/schemas';

export interface UseTxOptions {
  rpcUrl: string;
  hash: string | null | undefined;
  enabled?: boolean;
}

export function useTx(options: UseTxOptions) {
  const { rpcUrl, hash, enabled = true } = options;

  return useQuery({
    queryKey: queryKeys.tx(hash!),
    queryFn: async (): Promise<TxDetail> => {
      if (!hash) {
        throw new Error('Transaction hash is required');
      }

      const client = getEnhancedRpcClient(rpcUrl);
      return client.getTx(hash);
    },
    enabled: enabled && !!hash,
    staleTime: 60000, // Transactions are immutable once confirmed
  });
}
