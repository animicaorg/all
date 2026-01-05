/**
 * useAddress hook - Fetches address/account data
 */

import { useQuery } from '@tanstack/react-query';
import { getEnhancedRpcClient } from '../../lib/rpc/client';
import { queryKeys } from '../../lib/query/queryClient';
import type { AddressDetail } from '../../lib/rpc/schemas';

export interface UseAddressOptions {
  rpcUrl: string;
  address: string | null | undefined;
  enabled?: boolean;
}

export function useAddress(options: UseAddressOptions) {
  const { rpcUrl, address, enabled = true } = options;

  return useQuery({
    queryKey: queryKeys.address(address!),
    queryFn: async (): Promise<AddressDetail> => {
      if (!address) {
        throw new Error('Address is required');
      }

      const client = getEnhancedRpcClient(rpcUrl);
      return client.getAddress(address);
    },
    enabled: enabled && !!address,
    staleTime: 5000, // Address balances can change frequently
  });
}
