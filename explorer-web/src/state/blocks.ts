/**
 * Animica Explorer — Blocks cache & paging helpers
 * -----------------------------------------------------------------------------
 * Responsibilities
 * - Maintain an in-memory cache of recent blocks (descending by height)
 * - Provide a paging API (page 0 = newest) with a configurable page size
 * - Opportunistically refresh the newest page when new heads arrive
 * - Minimize RPC round-trips by fetching ranges when possible
 *
 * Integrates with:
 *  - state/store.ts (for network/head/toasts)
 *  - services/rpc.ts (createRpc, getBlock, getBlocks)
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useExplorerStore } from './store';
import { shallow } from './store';

// ----------------------------- RPC types ------------------------------------

export interface BlockSummary {
  height: number;
  hash: string;
  parentHash?: string;
  timeISO: string;
  txCount: number;
  proposer?: string;
  daRoot?: string;
}

// Minimal client contract we rely upon.
// Implemented in explorer-web/src/services/rpc.ts
export interface RpcClient {
  getBlock(height: number): Promise<BlockSummary>;
  getBlocks?(fromHeightInclusive: number, limit: number): Promise<BlockSummary[]>; // descending preferred
  close?(): void;
}

// Dynamic loader (avoids import cycles in SSR/build)
type CreateRpcFn = (url: string) => RpcClient;
let _createRpcAsync: Promise<CreateRpcFn> | null = null;
async function createRpc(rpcUrl: string): Promise<RpcClient> {
  if (!_createRpcAsync) {
    _createRpcAsync = import('../services/rpc').then(m => m.createRpc as unknown as CreateRpcFn);
  }
  const fn = await _createRpcAsync;
  return fn(rpcUrl);
}

// ----------------------------- Local cache ----------------------------------

// Simple LRU-ish cache keyed by height. We cap by MAX_CACHE.
class BlockCache {
  private map = new Map<number, BlockSummary>();
  private order: number[] = []; // heights, newest first
  constructor(private MAX_CACHE = 2000) {}

  get(height: number): BlockSummary | undefined {
    return this.map.get(height);
  }

  put(b: BlockSummary) {
    if (!this.map.has(b.height)) {
      this.order.unshift(b.height);
    }
    this.map.set(b.height, b);
    // enforce capacity
    if (this.order.length > this.MAX_CACHE) {
      const dropHeights = this.order.splice(this.MAX_CACHE);
      for (const h of dropHeights) this.map.delete(h);
    }
  }

  putMany(blocks: BlockSummary[]) {
    // Assume mostly descending (newest first)
    for (const b of blocks) this.put(b);
    // Re-sort order just in case — cheap for page-sized batches
    this.order.sort((a, z) => z - a);
  }

  // Return contiguous slice (descending) if fully present
  takeRangeDesc(high: number, count: number): BlockSummary[] | null {
    const out: BlockSummary[] = [];
    for (let h = high; h > 0 && out.length < count; h--) {
      const b = this.map.get(h);
      if (!b) return null; // missing -> not contiguous in cache
      out.push(b);
    }
    return out;
  }
}

// ----------------------------- Hook API -------------------------------------

export type BlocksStatus = 'idle' | 'loading' | 'ready' | 'error';

export interface UseBlocksResult {
  status: BlocksStatus;
  error: string | null;
  newestHeight: number | null;
  pageSize: number;
  // Get a page of blocks (descending). Page 0 is newest.
  getPage: (pageIndex: number) => Promise<BlockSummary[]>;
  // Force refresh newest page (e.g., user click)
  refreshLatest: () => Promise<void>;
}

export function useBlocks(opts?: {
  pageSize?: number;
  cacheSize?: number;
  autoRefresh?: boolean; // refresh on new head
}): UseBlocksResult {
  const {
    pageSize = 20,
    cacheSize = 2000,
    autoRefresh = true,
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
  const newestHeight = head?.height ?? null;

  const runtime = useRef<{
    client: RpcClient | null;
    cache: BlockCache;
    inflight: Map<string, Promise<BlockSummary[]>>;
  }>({
    client: null,
    cache: new BlockCache(cacheSize),
    inflight: new Map(),
  });

  // Recreate client when RPC URL changes
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
      } catch (e: any) {
        if (cancelled) return;
        const msg = `[blocks] failed to init RPC: ${e?.message || String(e)}`;
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
  }, [rpcUrl]);

  // Auto-refresh newest page when head grows
  const lastSeenHead = useRef<number | null>(null);
  useEffect(() => {
    if (!autoRefresh) return;
    if (!newestHeight) return;
    if (lastSeenHead.current === null) {
      lastSeenHead.current = newestHeight;
      return;
    }
    if (newestHeight > lastSeenHead.current) {
      // Fire-and-forget; UI will reflect when user opens page 0
      void refreshLatest();
    }
    lastSeenHead.current = newestHeight;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [newestHeight, autoRefresh]);

  const fetchRange = useCallback(
    async (high: number, count: number): Promise<BlockSummary[]> => {
      const key = `${high}:${count}`;
      const rt = runtime.current;
      if (!rt.client) throw new Error('RPC not ready');

      // Cache hit (contiguous)
      const cached = rt.cache.takeRangeDesc(high, count);
      if (cached) return cached;

      // Coalesce inflight
      const existing = rt.inflight.get(key);
      if (existing) return existing;

      const p = (async () => {
        try {
          let blocks: BlockSummary[] = [];
          if (typeof rt.client.getBlocks === 'function') {
            blocks = await rt.client.getBlocks!(high, count);
            // Ensure descending heights
            blocks.sort((a, z) => z.height - a.height);
          } else {
            // Fallback to per-height fetch
            const req: Promise<BlockSummary>[] = [];
            for (let h = high; h > 0 && req.length < count; h--) {
              req.push(rt.client!.getBlock(h));
            }
            const res = await Promise.all(req);
            // Ensure descending
            blocks = res.sort((a, z) => z.height - a.height);
          }
          // Populate cache
          rt.cache.putMany(blocks);
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

      // Page 0 covers [headHeight .. headHeight-(pageSize-1)]
      const high = Math.max(1, headHeight - pageIndex * pageSize);
      const low = Math.max(1, high - (pageSize - 1));
      const want = high - low + 1;

      try {
        const blocks = await fetchRange(high, want);
        setStatus('ready');
        return blocks;
      } catch (e: any) {
        const msg = `[blocks] fetch page ${pageIndex} failed: ${e?.message || String(e)}`;
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
      // Fetch latest page again; this will refill cache from top
      const want = pageSize;
      if (typeof rt.client.getBlocks === 'function') {
        const blocks = await rt.client.getBlocks(headHeight, want);
        rt.cache.putMany(blocks);
      } else {
        const req: Promise<BlockSummary>[] = [];
        for (let h = headHeight; h > 0 && req.length < want; h--) {
          req.push(rt.client.getBlock(h));
        }
        const blocks = (await Promise.all(req)).sort((a, z) => z.height - a.height);
        rt.cache.putMany(blocks);
      }
    } catch (e) {
      // Ignore transient errors on background refresh
    }
  }, [newestHeight, pageSize]);

  function cleanup() {
    const rt = runtime.current;
    if (rt.client && typeof rt.client.close === 'function') {
      try {
        rt.client.close();
      } catch {
        /* ignore */
      }
    }
    rt.client = null;
    rt.cache = new BlockCache(cacheSize);
    rt.inflight.clear();
    setStatus('idle');
    setError(null);
  }

  return {
    status,
    error,
    newestHeight,
    pageSize,
    getPage,
    refreshLatest,
  };
}

// Convenience data hook for components that just want a given page bound to UI.
export function useBlocksPage(pageIndex: number, pageSize = 20) {
  const { getPage, status, error, newestHeight } = useBlocks({ pageSize, autoRefresh: true });
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
  };
}
