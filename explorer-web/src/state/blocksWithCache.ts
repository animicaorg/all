/**
 * Animica Explorer — Blocks with Persistent Cache
 * -----------------------------------------------------------------------------
 * Enhanced blocks state management with IndexedDB persistent cache.
 * - Cache-first strategy: check IndexedDB before RPC
 * - Fallback to RPC on cache miss
 * - Background sync keeps cache up-to-date
 * - Graceful degradation when RPC is unavailable
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useExplorerStore } from './store';
import { shallow } from './store';
import { getCache, isCacheAvailable, type ExplorerCache } from '../services/cache';
import { getSyncManager, type SyncManager, type SyncStatus } from '../services/sync';

// ----------------------------- Types ----------------------------------------

export interface BlockSummary {
  height: number;
  hash: string;
  parentHash?: string;
  timeISO: string;
  txCount: number;
  proposer?: string;
  daRoot?: string;
}

export interface RpcClient {
  getBlock(height: number): Promise<BlockSummary>;
  getBlocks?(fromHeightInclusive: number, limit: number): Promise<BlockSummary[]>;
  close?(): void;
}

type CreateRpcFn = (opts: { url: string }) => RpcClient;
let _createRpcAsync: Promise<CreateRpcFn> | null = null;
async function createRpc(rpcUrl: string): Promise<RpcClient> {
  if (!_createRpcAsync) {
    _createRpcAsync = import('../services/rpc').then(m => m.createRpc as unknown as CreateRpcFn);
  }
  const fn = await _createRpcAsync;
  return fn({ url: rpcUrl });
}

// ----------------------------- Hook API -------------------------------------

export type BlocksStatus = 'idle' | 'loading' | 'ready' | 'error';

export interface UseBlocksResult {
  status: BlocksStatus;
  error: string | null;
  newestHeight: number | null;
  pageSize: number;
  syncStatus: SyncStatus | null;
  cacheAvailable: boolean;
  // Get a page of blocks (descending). Page 0 is newest.
  getPage: (pageIndex: number) => Promise<BlockSummary[]>;
  // Force refresh newest page (e.g., user click)
  refreshLatest: () => Promise<void>;
  // Cache management
  clearCache: () => Promise<void>;
  getCacheStats: () => Promise<any>;
}

export function useBlocksWithCache(opts?: {
  pageSize?: number;
  autoRefresh?: boolean;
  enableSync?: boolean; // Enable background sync (default: true)
}): UseBlocksResult {
  const {
    pageSize = 20,
    autoRefresh = true,
    enableSync = true,
  } = opts ?? {};

  const { rpcUrl, head, addToast } = useExplorerStore(
    (s) => ({
      rpcUrl: s.network.rpcUrl as string | undefined,
      head: s.head as { height: number } | null,
      addToast: s.addToast as (t: { kind: 'info' | 'error' | 'success'; text: string }) => void,
    }),
    shallow
  );

  const [status, setStatus] = useState<BlocksStatus>('idle');
  const [error, setError] = useState<string | null>(null);
  const [syncStatus, setSyncStatus] = useState<SyncStatus | null>(null);
  const newestHeight = head?.height ?? null;

  const runtime = useRef<{
    client: RpcClient | null;
    cache: ExplorerCache | null;
    syncManager: SyncManager | null;
    inflight: Map<string, Promise<BlockSummary[]>>;
  }>({
    client: null,
    cache: null,
    syncManager: null,
    inflight: new Map(),
  });

  const cacheAvailable = isCacheAvailable();

  // Initialize cache
  useEffect(() => {
    if (!cacheAvailable) {
      console.warn('[blocksWithCache] IndexedDB not available, cache disabled');
      return;
    }

    let cancelled = false;

    async function initCache() {
      try {
        const cache = await getCache();
        if (cancelled) return;
        runtime.current.cache = cache;
        console.log('[blocksWithCache] Cache initialized');

        // Log cache stats
        const stats = await cache.getStats();
        console.log('[blocksWithCache] Cache stats:', stats);
      } catch (err: any) {
        if (cancelled) return;
        console.error('[blocksWithCache] Cache init error:', err);
      }
    }

    initCache();

    return () => {
      cancelled = true;
    };
  }, [cacheAvailable]);

  // Initialize RPC client
  useEffect(() => {
    let cancelled = false;

    async function connect() {
      cleanup();
      if (!rpcUrl) return;

      try {
        const client = await createRpc(rpcUrl);
        if (cancelled) return;
        runtime.current.client = client;
        setStatus('ready');
        setError(null);

        // Start background sync if enabled
        if (enableSync && cacheAvailable) {
          startSync(client);
        }
      } catch (e: any) {
        if (cancelled) return;
        const msg = `[blocksWithCache] failed to init RPC: ${e?.message || String(e)}`;
        setStatus('error');
        setError(msg);
        addToast?.({ kind: 'error', text: msg });
      }
    }
    connect();

    return () => {
      cancelled = true;
      cleanup();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rpcUrl, enableSync, cacheAvailable]);

  // Start background sync
  const startSync = useCallback((client: RpcClient) => {
    if (!cacheAvailable) return;

    const syncManager = getSyncManager({
      batchSize: 20,
      delayMs: 2000, // Check every 2 seconds
      catchupThreshold: 50,
    });

    syncManager.setRpcClient(client);
    runtime.current.syncManager = syncManager;

    // Subscribe to sync status
    const unsubscribe = syncManager.onStatusChange((status) => {
      setSyncStatus(status);
      if (status.error) {
        console.warn('[blocksWithCache] Sync error:', status.error);
      }
    });

    syncManager.start().catch((err) => {
      console.error('[blocksWithCache] Sync start error:', err);
    });

    // Return cleanup
    return () => {
      unsubscribe();
      syncManager.stop();
    };
  }, [cacheAvailable]);

  // Fetch blocks with cache-first strategy
  const fetchRange = useCallback(
    async (high: number, count: number): Promise<BlockSummary[]> => {
      const key = `${high}:${count}`;
      const rt = runtime.current;

      // Coalesce inflight requests
      const existing = rt.inflight.get(key);
      if (existing) return existing;

      const p = (async () => {
        try {
          // Try cache first
          if (rt.cache) {
            const low = Math.max(1, high - count + 1);
            const cached = await rt.cache.getBlocksRange(high, low);
            if (cached && cached.length === count) {
              console.debug(`[blocksWithCache] Cache hit: ${high}..${low}`);
              return cached;
            }
          }

          // Cache miss or incomplete - fetch from RPC
          if (!rt.client) {
            // No RPC available, return what we have from cache
            if (rt.cache) {
              const low = Math.max(1, high - count + 1);
              const partial = await rt.cache.getBlocksRange(high, low);
              if (partial.length > 0) {
                console.debug(`[blocksWithCache] RPC unavailable, using partial cache`);
                return partial;
              }
            }
            throw new Error('RPC not ready and cache empty');
          }

          console.debug(`[blocksWithCache] Cache miss, fetching from RPC: ${high}..${high - count + 1}`);

          let blocks: BlockSummary[] = [];
          if (typeof rt.client.getBlocks === 'function') {
            blocks = await rt.client.getBlocks!(high, count);
            blocks.sort((a, z) => z.height - a.height);
          } else {
            const req: Promise<BlockSummary>[] = [];
            for (let h = high; h > 0 && req.length < count; h--) {
              req.push(rt.client!.getBlock(h));
            }
            const res = await Promise.all(req);
            blocks = res.sort((a, z) => z.height - a.height);
          }

          // Store in cache (fire and forget)
          if (rt.cache && blocks.length > 0) {
            rt.cache.putBlocks(
              blocks.map((b) => ({ height: b.height, hash: b.hash, data: b }))
            ).catch((err) => {
              console.warn('[blocksWithCache] Cache write error:', err);
            });
          }

          return blocks;
        } finally {
          rt.inflight.delete(key);
        }
      })();

      rt.inflight.set(key, p);
      return p;
    },
    []
  );

  const getPage = useCallback(
    async (pageIndex: number): Promise<BlockSummary[]> => {
      if (pageIndex < 0) throw new Error('pageIndex must be >= 0');
      const headHeight = newestHeight;
      if (!headHeight) return [];

      setStatus('loading');
      setError(null);

      const high = Math.max(1, headHeight - pageIndex * pageSize);
      const low = Math.max(1, high - (pageSize - 1));
      const want = high - low + 1;

      try {
        const blocks = await fetchRange(high, want);
        setStatus('ready');
        return blocks;
      } catch (e: any) {
        const msg = `[blocksWithCache] fetch page ${pageIndex} failed: ${e?.message || String(e)}`;
        setStatus('error');
        setError(msg);
        addToast?.({ kind: 'error', text: msg });
        return [];
      }
    },
    [addToast, fetchRange, newestHeight, pageSize]
  );

  const refreshLatest = useCallback(async () => {
    const headHeight = newestHeight;
    if (!headHeight) return;

    try {
      const rt = runtime.current;
      if (!rt.client) return;

      const want = pageSize;
      let blocks: BlockSummary[] = [];

      if (typeof rt.client.getBlocks === 'function') {
        blocks = await rt.client.getBlocks(headHeight, want);
      } else {
        const req: Promise<BlockSummary>[] = [];
        for (let h = headHeight; h > 0 && req.length < want; h--) {
          req.push(rt.client.getBlock(h));
        }
        blocks = (await Promise.all(req)).sort((a, z) => z.height - a.height);
      }

      // Update cache
      if (rt.cache && blocks.length > 0) {
        await rt.cache.putBlocks(
          blocks.map((b) => ({ height: b.height, hash: b.hash, data: b }))
        );
      }
    } catch (e) {
      console.warn('[blocksWithCache] refreshLatest error:', e);
    }
  }, [newestHeight, pageSize]);

  const clearCache = useCallback(async () => {
    const rt = runtime.current;
    if (!rt.cache) {
      addToast?.({ kind: 'warning', text: 'Cache not available' });
      return;
    }

    try {
      await rt.cache.clearAll();
      addToast?.({ kind: 'success', text: 'Cache cleared successfully' });
      console.log('[blocksWithCache] Cache cleared');
    } catch (err: any) {
      const msg = `Failed to clear cache: ${err?.message || String(err)}`;
      addToast?.({ kind: 'error', text: msg });
      console.error('[blocksWithCache] Clear cache error:', err);
    }
  }, [addToast]);

  const getCacheStats = useCallback(async () => {
    const rt = runtime.current;
    if (!rt.cache) return null;

    try {
      return await rt.cache.getStats();
    } catch (err) {
      console.error('[blocksWithCache] Get stats error:', err);
      return null;
    }
  }, []);

  function cleanup() {
    const rt = runtime.current;
    if (rt.client && typeof rt.client.close === 'function') {
      try {
        rt.client.close();
      } catch {
        /* ignore */
      }
    }
    if (rt.syncManager) {
      rt.syncManager.stop();
    }
    rt.client = null;
    rt.syncManager = null;
    rt.inflight.clear();
    setStatus('idle');
    setError(null);
    setSyncStatus(null);
  }

  return {
    status,
    error,
    newestHeight,
    pageSize,
    syncStatus,
    cacheAvailable,
    getPage,
    refreshLatest,
    clearCache,
    getCacheStats,
  };
}

// Convenience hook for components that just want a given page
export function useBlocksPageWithCache(pageIndex: number, pageSize = 20) {
  const { getPage, status, error, newestHeight, syncStatus, cacheAvailable } = useBlocksWithCache({
    pageSize,
    autoRefresh: true,
    enableSync: true,
  });
  const [blocks, setBlocks] = useState<BlockSummary[] | null>(null);

  useEffect(() => {
    let mounted = true;
    (async () => {
      const b = await getPage(pageIndex);
      if (mounted) setBlocks(b);
    })();
    return () => {
      mounted = false;
    };
  }, [getPage, pageIndex]);

  return {
    blocks,
    status,
    error,
    newestHeight,
    syncStatus,
    cacheAvailable,
  };
}
