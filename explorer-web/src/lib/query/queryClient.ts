/**
 * TanStack Query (React Query) setup for Explorer
 * 
 * Provides configured QueryClient with appropriate defaults
 * for blockchain data fetching and caching.
 */

import { QueryClient } from '@tanstack/react-query';

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // Blockchain data is relatively stable once confirmed
      staleTime: 5000, // Consider data fresh for 5 seconds
      gcTime: 300000, // Keep unused data in cache for 5 minutes (was cacheTime in v4)
      
      // Retry configuration
      retry: 2,
      retryDelay: (attemptIndex) => Math.min(1000 * 2 ** attemptIndex, 5000),
      
      // Error handling
      throwOnError: false,
      
      // Refetch configuration
      refetchOnWindowFocus: false, // Don't auto-refetch on window focus (we use subscriptions)
      refetchOnReconnect: true, // Do refetch when network reconnects
      refetchOnMount: true, // Refetch on component mount if data is stale
    },
    mutations: {
      // Mutations will be rare in a read-only explorer, but set defaults anyway
      retry: 0,
    },
  },
});

/**
 * Query keys factory for consistent key generation
 * 
 * This ensures all queries use consistent keys for proper caching and invalidation.
 */
export const queryKeys = {
  // Chain status
  chainId: ['chainId'] as const,
  head: ['head'] as const,
  chainStatus: ['chainStatus'] as const,
  
  // Blocks
  block: (heightOrHash: number | string) => ['block', heightOrHash] as const,
  blocks: (filters?: { fromHeight?: number; limit?: number }) => 
    ['blocks', filters] as const,
  blocksList: () => ['blocks'] as const,
  
  // Transactions
  tx: (hash: string) => ['tx', hash] as const,
  txsList: (filters?: { blockHeight?: number; address?: string }) => 
    ['txs', filters] as const,
  
  // Addresses
  address: (addr: string) => ['address', addr] as const,
  addressTxs: (addr: string, filters?: { page?: number; limit?: number }) => 
    ['address', addr, 'txs', filters] as const,
  
  // Mempool
  mempool: () => ['mempool'] as const,
  mempoolTx: (hash: string) => ['mempool', hash] as const,
  
  // Peers
  peers: () => ['peers'] as const,
  
  // Features
  features: () => ['features'] as const,
};

/**
 * Invalidate queries related to chain head updates
 * 
 * Call this when a new head is detected to refresh relevant queries.
 */
export function invalidateHeadRelatedQueries() {
  queryClient.invalidateQueries({ queryKey: queryKeys.head });
  queryClient.invalidateQueries({ queryKey: queryKeys.chainStatus });
  queryClient.invalidateQueries({ queryKey: queryKeys.blocksList() });
  queryClient.invalidateQueries({ queryKey: queryKeys.mempool() });
}

/**
 * Invalidate queries related to a specific block
 * 
 * Call this when a block reorg is detected.
 */
export function invalidateBlockQueries(heightOrHash: number | string) {
  queryClient.invalidateQueries({ queryKey: queryKeys.block(heightOrHash) });
  // Also invalidate blocks list since it might contain this block
  queryClient.invalidateQueries({ queryKey: queryKeys.blocksList() });
}

/**
 * Clear all cached data
 * 
 * Use sparingly - typically only when switching networks.
 */
export function clearAllQueries() {
  queryClient.clear();
}
