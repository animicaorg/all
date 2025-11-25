/**
 * Animica Explorer — Transactions feed & filters
 * -----------------------------------------------------------------------------
 * Responsibilities
 * - Paged, newest-first transaction feed with lightweight client-side filters
 * - Prefer server-side paging (rpc.getTxs). Fallback: aggregate from blocks.
 * - Cache per-(filters, page) with auto-refresh on new heads or live tx stream
 *
 * Integrates with:
 *  - state/store.ts   (useExplorerStore: rpcUrl/head/toasts)
 *  - services/rpc.ts  (createRpc: { getTxs?, getBlockTxs?, subscribeNewTxs? })
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { shallow } from 'zustand/shallow';
import { useExplorerStore } from './store';

// ----------------------------- Types ----------------------------------------

export type TxStatus = 'success' | 'failed' | 'pending';

export interface TxSummary {
  hash: string;
  height: number;          // block height (>= 0). 0 for pending
  index?: number;          // position within block (0-based)
  from: string;
  to?: string | null;      // null/undefined for contract deploy
  method?: string | null;  // decoded method name when available
  status: TxStatus;
  fee?: string;            // human-readable or wei-string (opaque for UI)
  gasUsed?: number;
  timestampISO?: string;   // ISO-8601
  contractAddress?: string; // set if deploy
}

export interface TxPageMeta {
  total?: number; // total matching (approximate)
}

export interface TxFilters {
  address?: string;      // matches from OR to
  method?: string;       // exact or prefix match
  status?: TxStatus | 'all';
  minHeight?: number;
  maxHeight?: number;
  query?: string;        // free text (hash prefix, etc.)
}

// ----------------------------- RPC interface --------------------------------

export interface RpcClient {
  getTxs?(params: {
    limit: number;
    offset: number;
    address?: string;
    method?: string;
    status?: TxStatus | 'all';
    minHeight?: number;
    maxHeight?: number;
    query?: string;
  }): Promise<{ items: TxSummary[]; meta?: TxPageMeta }>;

  getBlockTxs?(height: number): Promise<TxSummary[]>;

  subscribeNewTxs?(
    onTx: (tx: TxSummary) => void,
    onError?: (err: Error) => void
  ): () => void;

  close?(): void;
}

// Lazy loader to avoid import cycles during SSR/tests
type CreateRpcFn = (url: string) => RpcClient;
let _createRpcAsync: Promise<CreateRpcFn> | null = null;
async function createRpc(rpcUrl: string): Promise<RpcClient> {
  if (!_createRpcAsync) {
    _createRpcAsync = import('../services/rpc').then(
      (m) => m.createRpc as unknown as CreateRpcFn
    );
  }
  const fn = await _createRpcAsync;
  return fn(rpcUrl);
}

// ----------------------------- Cache layer ----------------------------------

class TxPageCache {
  private pages = new Map<string, TxSummary[]>(); // key = `${fkey}:${page}`
  private heads = new Map<string, TxSummary[]>(); // realtime buffer (per fkey)

  constructor(private maxPagesPerKey = 50) {}

  makeKey(fkey: string, pageIndex: number) {
    return `${fkey}:${pageIndex}`;
  }

  get(fkey: string, pageIndex: number): TxSummary[] | undefined {
    return this.pages.get(this.makeKey(fkey, pageIndex));
  }

  set(fkey: string, pageIndex: number, items: TxSummary[]) {
    const k = this.makeKey(fkey, pageIndex);
    this.pages.set(k, items);

    // Trim oldest pages if too many for this filter key
    const keys = [...this.pages.keys()].filter((s) => s.startsWith(`${fkey}:`));
    if (keys.length > this.maxPagesPerKey) {
      const sorted = keys
        .map((k) => ({ k, idx: parseInt(k.split(':').pop() || '0', 10) }))
        .sort((a, z) => z.idx - a.idx);
      for (let i = this.maxPagesPerKey; i < sorted.length; i++) {
        this.pages.delete(sorted[i].k);
      }
    }
  }

  pushHead(fkey: string, tx: TxSummary, cap = 100) {
    const list = this.heads.get(fkey) ?? [];
    if (!list.find((t) => t.hash === tx.hash)) {
      list.unshift(tx);
      if (list.length > cap) list.length = cap;
      this.heads.set(fkey, list);
    }
  }

  takeHead(fkey: string): TxSummary[] {
    return this.heads.get(fkey) ?? [];
  }

  clear(fkey?: string) {
    if (!fkey) {
      this.pages.clear();
      this.heads.clear();
      return;
    }
    for (const k of [...this.pages.keys()]) {
      if (k.startsWith(`${fkey}:`)) this.pages.delete(k);
    }
    this.heads.delete(fkey);
  }
}

// ----------------------------- Helpers --------------------------------------

function normalizeFilters(f?: TxFilters): Required<TxFilters> {
  const trim = (s?: string | null) => (s ? s.trim() : '');
  return {
    address: trim(f?.address).toLowerCase(),
    method: trim(f?.method),
    status: (f?.status ?? 'all') as TxStatus | 'all',
    minHeight: f?.minHeight ?? 0,
    maxHeight: f?.maxHeight ?? Number.MAX_SAFE_INTEGER,
    query: trim(f?.query),
  };
}

function keyForFilters(f: Required<TxFilters>): string {
  return [
    `addr=${f.address || '*'}`,
    `method=${f.method || '*'}`,
    `status=${f.status}`,
    `min=${f.minHeight}`,
    `max=${Number.isFinite(f.maxHeight) ? f.maxHeight : 'inf'}`,
    `q=${f.query || '*'}`,
  ].join('&');
}

function txMatchesFilters(tx: TxSummary, f: Required<TxFilters>): boolean {
  if (tx.height < f.minHeight) return false;
  if (tx.height > f.maxHeight) return false;
  if (f.status !== 'all' && tx.status !== f.status) return false;

  const addr = f.address;
  if (addr) {
    const fromOk = tx.from?.toLowerCase() === addr;
    const toOk =
      (tx.to ?? '').toLowerCase() === addr ||
      (tx.contractAddress ?? '').toLowerCase() === addr;
    if (!fromOk && !toOk) return false;
  }
  if (f.method) {
    const m = (tx.method ?? '').toLowerCase();
    if (!m || !m.startsWith(f.method.toLowerCase())) return false;
  }
  if (f.query) {
    const q = f.query.toLowerCase();
    const any =
      tx.hash.toLowerCase().includes(q) ||
      tx.from.toLowerCase().includes(q) ||
      (tx.to ?? '').toLowerCase().includes(q) ||
      (tx.contractAddress ?? '').toLowerCase().includes(q) ||
      (tx.method ?? '').toLowerCase().includes(q);
    if (!any) return false;
  }
  return true;
}

// ----------------------------- Hook API -------------------------------------

export type TxsStatus = 'idle' | 'loading' | 'ready' | 'error';

export interface UseTxFeedResult {
  status: TxsStatus;
  error: string | null;
  newestHeight: number | null;
  pageSize: number;
  filters: Required<TxFilters>;
  setFilters: (next: TxFilters) => void;

  // Page 0 is newest. Returns descending txs (by (height, index)).
  getPage: (pageIndex: number) => Promise<TxSummary[]>;

  // Force refresh of newest page (e.g. manual refresh)
  refreshLatest: () => Promise<void>;
}

export function useTxFeed(opts?: {
  pageSize?: number;
  cachePages?: number;
  initialFilters?: TxFilters;
  autoRefresh?: boolean; // refresh page 0 on new head or websocket tick
}): UseTxFeedResult {
  const {
    pageSize = 25,
    cachePages = 50,
    initialFilters = {},
    autoRefresh = true,
  } = opts ?? {};

  const { rpcUrl, head, addToast } = useExplorerStore(
    (s) => ({
      rpcUrl: s.network.rpcUrl as string | undefined,
      head: s.head as { height: number } | null,
      addToast: s.addToast as (t: {
        kind: 'info' | 'error' | 'success';
        text: string;
      }) => void,
    }),
    shallow
  );

  const newestHeight = head?.height ?? null;

  const [status, setStatus] = useState<TxsStatus>('idle');
  const [error, setError] = useState<string | null>(null);
  const [filters, setFiltersState] = useState<Required<TxFilters>>(
    normalizeFilters(initialFilters)
  );

  const runtime = useRef<{
    client: RpcClient | null;
    cache: TxPageCache;
    inflight: Map<string, Promise<TxSummary[]>>;
    unsub?: () => void;
  }>({
    client: null,
    cache: new TxPageCache(cachePages),
    inflight: new Map(),
  });

  // Connect / reconnect on RPC URL changes
  useEffect(() => {
    let cancelled = false;

    function cleanup() {
      const rt = runtime.current;
      try {
        rt.unsub?.();
      } catch {}
      if (rt.client && typeof rt.client.close === 'function') {
        try {
          rt.client.close();
        } catch {}
      }
      rt.client = null;
      rt.cache.clear();
      rt.inflight.clear();
      setStatus('idle');
      setError(null);
    }

    async function connect() {
      cleanup();
      if (!rpcUrl) return;

      try {
        const client = await createRpc(rpcUrl);
        if (cancelled) return;
        runtime.current.client = client;
        setStatus('ready');
        setError(null);

        if (autoRefresh && typeof client.subscribeNewTxs === 'function') {
          runtime.current.unsub = client.subscribeNewTxs!(
            (tx) => {
              const f = runtime.current ? filters : filters; // capture latest
              if (txMatchesFilters(tx, f)) {
                const fkey = keyForFilters(f);
                runtime.current.cache.pushHead(fkey, tx);
              }
            },
            (err) => {
              addToast?.({
                kind: 'error',
                text: `[txs] live feed error: ${err.message}`,
              });
            }
          );
        }
      } catch (e: any) {
        if (cancelled) return;
        const msg = `[txs] failed to init RPC: ${e?.message || String(e)}`;
        setStatus('error');
        setError(msg);
        addToast?.({ kind: 'error', text: msg });
      }
    }

    void connect();
    return () => {
      cancelled = true;
      // call cleanup to close connections
      const rt = runtime.current;
      try {
        rt.unsub?.();
      } catch {}
      try {
        rt.client?.close?.();
      } catch {}
      rt.client = null;
      rt.cache.clear();
      rt.inflight.clear();
      setStatus('idle');
      setError(null);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rpcUrl, autoRefresh]);

  // Auto-refresh on new head
  const lastSeenHead = useRef<number | null>(null);
  useEffect(() => {
    if (!autoRefresh) return;
    if (!newestHeight) return;
    if (lastSeenHead.current === null) {
      lastSeenHead.current = newestHeight;
      return;
    }
    if (newestHeight > lastSeenHead.current) {
      void refreshLatest();
    }
    lastSeenHead.current = newestHeight;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [newestHeight, autoRefresh]);

  // Invalidate cache on filters change
  useEffect(() => {
    runtime.current.cache.clear();
  }, [filters]);

  const setFilters = useCallback((next: TxFilters) => {
    setFiltersState(normalizeFilters(next));
  }, []);

  const fetchPageServer = useCallback(
    async (
      fkey: string,
      pageIndex: number,
      f: Required<TxFilters>
    ): Promise<TxSummary[]> => {
      const rt = runtime.current;
      if (!rt.client || typeof rt.client.getTxs !== 'function') {
        throw new Error('RPC getTxs not available');
      }
      const limit = pageSize;
      const offset = pageIndex * pageSize;
      const { items } = await rt.client.getTxs({
        limit,
        offset,
        address: f.address || undefined,
        method: f.method || undefined,
        status: f.status,
        minHeight: f.minHeight || undefined,
        maxHeight: Number.isFinite(f.maxHeight) ? f.maxHeight : undefined,
        query: f.query || undefined,
      });

      let out = items;
      if (pageIndex === 0) {
        const headBuf = runtime.current.cache.takeHead(fkey);
        if (headBuf.length) {
          const seen = new Set(out.map((t) => t.hash));
          out = [...headBuf.filter((t) => !seen.has(t.hash)), ...out];
          if (out.length > pageSize) out = out.slice(0, pageSize);
        }
      }
      return out;
    },
    [pageSize]
  );

  const fetchPageFallback = useCallback(
    async (
      fkey: string,
      pageIndex: number,
      f: Required<TxFilters>
    ): Promise<TxSummary[]> => {
      const rt = runtime.current;
      if (!rt.client) throw new Error('RPC not ready');
      if (typeof rt.client.getBlockTxs !== 'function') {
        throw new Error('Neither getTxs nor getBlockTxs available on RPC');
      }

      const headHeight =
        newestHeight ?? f.maxHeight ?? Number.MAX_SAFE_INTEGER;
      const start = Math.min(
        headHeight,
        Number.isFinite(f.maxHeight) ? f.maxHeight : headHeight
      );
      const minH = Math.max(0, f.minHeight);

      const targetStart = pageIndex * pageSize;
      const targetEnd = targetStart + pageSize;

      const collected: TxSummary[] = [];
      let matchedCount = 0;

      for (let h = start; h >= minH && matchedCount < targetEnd; h--) {
        // eslint-disable-next-line no-await-in-loop
        const txs = await rt.client.getBlockTxs(h);
        for (const tx of txs) {
          if (txMatchesFilters(tx, f)) {
            if (matchedCount >= targetStart && collected.length < pageSize) {
              collected.push(tx);
            }
            matchedCount++;
            if (collected.length >= pageSize) break;
          }
        }
      }

      if (pageIndex === 0) {
        const headBuf = runtime.current.cache.takeHead(fkey);
        if (headBuf.length) {
          const seen = new Set(collected.map((t) => t.hash));
          const merged = [
            ...headBuf.filter((t) => !seen.has(t.hash)),
            ...collected,
          ];
          return merged.slice(0, pageSize);
        }
      }

      return collected;
    },
    [newestHeight, pageSize]
  );

  const getPage = useCallback(
    async (pageIndex: number): Promise<TxSummary[]> => {
      if (pageIndex < 0) throw new Error('pageIndex must be >= 0');
      const rt = runtime.current;
      if (!rt.client) return [];

      setStatus('loading');
      setError(null);

      const f = filters;
      const fkey = keyForFilters(f);
      const cacheHit = rt.cache.get(fkey, pageIndex);
      if (cacheHit) {
        setStatus('ready');
        return cacheHit;
      }

      // Coalesce inflight requests
      const inflightKey = `${fkey}:${pageIndex}`;
      const existing = rt.inflight.get(inflightKey);
      if (existing) return existing;

      const p = (async () => {
        try {
          let items: TxSummary[];
          if (typeof rt.client!.getTxs === 'function') {
            items = await fetchPageServer(fkey, pageIndex, f);
          } else {
            items = await fetchPageFallback(fkey, pageIndex, f);
          }
          rt.cache.set(fkey, pageIndex, items);
          setStatus('ready');
          return items;
        } catch (e: any) {
          const msg = `[txs] fetch page ${pageIndex} failed: ${
            e?.message || String(e)
          }`;
          setStatus('error');
          setError(msg);
          useExplorerStore.getState().addToast?.({ kind: 'error', text: msg });
          return [];
        } finally {
          rt.inflight.delete(inflightKey);
        }
      })();

      rt.inflight.set(inflightKey, p);
      return p;
    },
    [filters, fetchPageServer, fetchPageFallback]
  );

  const refreshLatest = useCallback(async () => {
    const rt = runtime.current;
    if (!rt.client) return;
    const fkey = keyForFilters(filters);
    // Reset cached page 0 and re-fetch (head buffer retained)
    rt.cache.set(fkey, 0, []);
    try {
      await getPage(0);
    } catch {
      /* ignore */
    }
  }, [filters, getPage]);

  return {
    status,
    error,
    newestHeight,
    pageSize,
    filters,
    setFilters,
    getPage,
    refreshLatest,
  };
}

// Convenience hook binding a specific page to component state.
export function useTxsPage(
  pageIndex: number,
  opts?: {
    pageSize?: number;
    initialFilters?: TxFilters;
    autoRefresh?: boolean;
  }
) {
  const { pageSize = 25, initialFilters, autoRefresh } = opts ?? {};
  const {
    getPage,
    status,
    error,
    newestHeight,
    filters,
    setFilters,
    refreshLatest,
  } = useTxFeed({
    pageSize,
    initialFilters,
    autoRefresh,
  });

  const [txs, setTxs] = useState<TxSummary[] | null>(null);

  useEffect(() => {
    let mounted = true;
    (async () => {
      const res = await getPage(pageIndex);
      if (mounted) setTxs(res);
    })();
    return () => {
      mounted = false;
    };
  }, [getPage, pageIndex]);

  return {
    txs,
    status,
    error,
    newestHeight,
    filters,
    setFilters,
    refreshLatest,
  };
}
