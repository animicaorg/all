/**
 * Animica Explorer — Address state (snapshot, recent txs, contracts)
 * -----------------------------------------------------------------------------
 * Provides a high-level hook to load an account snapshot plus a paged,
 * newest-first feed of transactions that involve the address, and a derived
 * list of recently deployed contracts by that address.
 *
 * Integrates with:
 *  - state/store.ts   (useExplorerStore: rpcUrl/head/toasts)
 *  - services/rpc.ts  (createRpc: { getAccount, getTxs?, getBlockTxs?, getBlockHeader? })
 *  - state/txs.ts     (TxSummary type)
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { shallow } from 'zustand/shallow';
import { useExplorerStore } from './store';
import type { TxSummary, TxStatus } from './txs';

// ----------------------------- Types ----------------------------------------

export interface AccountSnapshot {
  address: string;
  balance: string;          // hex-wei or decimal (opaque for UI)
  nonce: number;
  isContract: boolean;
  codeHash?: string | null; // if contract
  storageRoot?: string | null;
  txCount?: number;         // optional (RPC may provide)
  lastUpdatedISO?: string;
}

export interface ContractsDeployed {
  address: string;       // contract address
  deployTx: string;      // tx hash
  codeHash?: string | null;
  verified?: boolean;    // if explorer/services knows verification status
  timestampISO?: string; // from tx or block header
}

export type AddressStatus = 'idle' | 'loading' | 'ready' | 'error';

// ----------------------------- RPC interface --------------------------------

export interface RpcClient {
  getAccount(address: string): Promise<{
    address: string;
    balance: string;
    nonce: number;
    isContract: boolean;
    codeHash?: string | null;
    storageRoot?: string | null;
    txCount?: number;
    lastUpdatedISO?: string;
  }>;

  // Generic tx listing (preferred). Should return newest-first.
  getTxs?(params: {
    limit: number;
    offset: number;
    address?: string;               // matches from OR to OR contractAddress
    status?: TxStatus | 'all';
    minHeight?: number;
    maxHeight?: number;
    query?: string;
  }): Promise<{ items: TxSummary[]; meta?: { total?: number } }>;

  // Fallbacks when getTxs is not offered:
  getBlockTxs?(height: number): Promise<TxSummary[]>;
  getBlockHeader?(height: number): Promise<{ timeISO?: string }>;

  // Optional convenience if node/explorer supports it:
  getContractsByCreator?(
    address: string,
    limit: number,
    offset: number
  ): Promise<ContractsDeployed[]>;

  close?(): void;
}

// Lazy import to avoid cycles during SSR/tests
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

interface AddressCacheEntry {
  snapshot: AccountSnapshot | null;
  txs: TxSummary[];
  txTotalApprox?: number;
  txPagesLoaded: number; // number of pages loaded (0-based inclusive count)
  contracts: ContractsDeployed[];
  contractsPagesLoaded: number;
}

class AddressCache {
  private map = new Map<string, AddressCacheEntry>();

  get(addr: string): AddressCacheEntry {
    const k = addr.toLowerCase();
    let v = this.map.get(k);
    if (!v) {
      v = {
        snapshot: null,
        txs: [],
        txPagesLoaded: 0,
        contracts: [],
        contractsPagesLoaded: 0,
      };
      this.map.set(k, v);
    }
    return v;
  }

  clear(addr?: string) {
    if (!addr) {
      this.map.clear();
      return;
    }
    this.map.delete(addr.toLowerCase());
  }
}

// ----------------------------- Helpers --------------------------------------

function norm(addr?: string | null) {
  return (addr ?? '').trim().toLowerCase();
}

// Merge newest-first, dedup by hash
function mergeTxs(existing: TxSummary[], incoming: TxSummary[]): TxSummary[] {
  const seen = new Set<string>(existing.map((t) => t.hash));
  const merged = [...existing];
  for (const t of incoming) {
    if (!seen.has(t.hash)) {
      merged.push(t);
      seen.add(t.hash);
    }
  }
  // Ensure newest-first by (height desc, index desc)
  merged.sort((a, b) => {
    if (a.height !== b.height) return b.height - a.height;
    const ai = a.index ?? 0;
    const bi = b.index ?? 0;
    return (bi - ai) || a.hash.localeCompare(b.hash);
  });
  return merged;
}

async function deriveContractsFromTxs(
  txs: TxSummary[],
  limit: number
): Promise<ContractsDeployed[]> {
  const out: ContractsDeployed[] = [];
  for (const t of txs) {
    if (t.contractAddress) {
      out.push({
        address: t.contractAddress,
        deployTx: t.hash,
        timestampISO: t.timestampISO,
      });
      if (out.length >= limit) break;
    }
  }
  return out;
}

// ----------------------------- Hook API -------------------------------------

export interface UseAddressOptions {
  txPageSize?: number;
  contractsPageSize?: number;
  autoRefresh?: boolean; // refresh snapshot + top txs on new head
}

export interface UseAddressResult {
  status: AddressStatus;
  error: string | null;

  snapshot: AccountSnapshot | null;
  // Newest first
  recentTxs: TxSummary[];
  // Contracts deployed by this address (recent first)
  recentContracts: ContractsDeployed[];

  txHasMore: boolean;
  contractsHasMore: boolean;

  loadMoreTxs: () => Promise<void>;
  loadMoreContracts: () => Promise<void>;
  refreshNow: () => Promise<void>;
}

export function useAddressState(
  address: string | undefined,
  opts?: UseAddressOptions
): UseAddressResult {
  const {
    txPageSize = 25,
    contractsPageSize = 10,
    autoRefresh = true,
  } = opts ?? {};

  const addr = useMemo(() => norm(address), [address]);

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

  const [status, setStatus] = useState<AddressStatus>('idle');
  const [error, setError] = useState<string | null>(null);

  const [snapshot, setSnapshot] = useState<AccountSnapshot | null>(null);
  const [recentTxs, setRecentTxs] = useState<TxSummary[]>([]);
  const [recentContracts, setRecentContracts] = useState<ContractsDeployed[]>(
    []
  );
  const [txHasMore, setTxHasMore] = useState<boolean>(false);
  const [contractsHasMore, setContractsHasMore] = useState<boolean>(false);

  const runtime = useRef<{
    client: RpcClient | null;
    cache: AddressCache;
    inflightSnapshot: Map<string, Promise<AccountSnapshot>>;
    inflightTxPage: Map<string, Promise<TxSummary[]>>;
    inflightContracts: Map<string, Promise<ContractsDeployed[]>>;
  }>({
    client: null,
    cache: new AddressCache(),
    inflightSnapshot: new Map(),
    inflightTxPage: new Map(),
    inflightContracts: new Map(),
  });

  // Connect / cleanup on rpcUrl change
  useEffect(() => {
    const rt = runtime.current;
    // cleanup existing
    try {
      rt.client?.close?.();
    } catch {}
    rt.client = null;
    rt.cache.clear();
    rt.inflightSnapshot.clear();
    rt.inflightTxPage.clear();
    rt.inflightContracts.clear();
    setStatus('idle');
    setError(null);
    setSnapshot(null);
    setRecentTxs([]);
    setRecentContracts([]);
    setTxHasMore(false);
    setContractsHasMore(false);

    let cancelled = false;
    (async () => {
      if (!rpcUrl) return;
      try {
        const client = await createRpc(rpcUrl);
        if (cancelled) return;
        runtime.current.client = client;
        setStatus('ready');
      } catch (e: any) {
        if (cancelled) return;
        const msg = `[address] failed to init RPC: ${e?.message || String(e)}`;
        setStatus('error');
        setError(msg);
        addToast?.({ kind: 'error', text: msg });
      }
    })();

    return () => {
      cancelled = true;
      try {
        runtime.current.client?.close?.();
      } catch {}
      runtime.current.client = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rpcUrl]);

  // Initial load and on address change
  useEffect(() => {
    if (!addr || !runtime.current.client) return;
    void refreshNow();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [addr, runtime.current.client]);

  // Auto-refresh snapshot + newest txs on new head
  const lastSeenHead = useRef<number | null>(null);
  useEffect(() => {
    if (!autoRefresh) return;
    if (!addr || !runtime.current.client) return;
    if (newestHeight === null) return;
    if (lastSeenHead.current === null) {
      lastSeenHead.current = newestHeight;
      return;
    }
    if (newestHeight > lastSeenHead.current) {
      // Refresh snapshot and page 0
      void refreshTop();
    }
    lastSeenHead.current = newestHeight;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [newestHeight, autoRefresh, addr, runtime.current.client]);

  const fetchSnapshot = useCallback(async (): Promise<AccountSnapshot> => {
    const rt = runtime.current;
    if (!rt.client) throw new Error('RPC not ready');
    if (!addr) throw new Error('No address provided');

    const inflightKey = addr;
    const existing = rt.inflightSnapshot.get(inflightKey);
    if (existing) return existing;

    const p = (async () => {
      const res = await rt.client!.getAccount(addr);
      const snap: AccountSnapshot = {
        address: res.address || addr,
        balance: res.balance,
        nonce: res.nonce,
        isContract: !!res.isContract,
        codeHash: res.codeHash ?? null,
        storageRoot: res.storageRoot ?? null,
        txCount: res.txCount,
        lastUpdatedISO: res.lastUpdatedISO,
      };
      rt.cache.get(addr).snapshot = snap;
      return snap;
    })();

    rt.inflightSnapshot.set(inflightKey, p);
    try {
      return await p;
    } finally {
      rt.inflightSnapshot.delete(inflightKey);
    }
  }, [addr]);

  const fetchTxPageServer = useCallback(
    async (pageIndex: number): Promise<TxSummary[]> => {
      const rt = runtime.current;
      if (!rt.client?.getTxs) throw new Error('RPC.getTxs not available');
      if (!addr) return [];
      const limit = txPageSize;
      const offset = pageIndex * txPageSize;
      const { items, meta } = await rt.client.getTxs({
        limit,
        offset,
        address: addr,
        status: 'all',
      });
      const entry = rt.cache.get(addr);
      entry.txPagesLoaded = Math.max(entry.txPagesLoaded, pageIndex + 1);
      if (typeof meta?.total === 'number') {
        entry.txTotalApprox = meta.total;
      }
      return items;
    },
    [addr, txPageSize]
  );

  const fetchTxPageFallback = useCallback(
    async (pageIndex: number): Promise<TxSummary[]> => {
      const rt = runtime.current;
      if (!rt.client?.getBlockTxs) throw new Error('Fallback not available');
      if (!addr) return [];

      // Walk blocks from head down, collect matches until we fill [offset, offset+limit)
      const limit = txPageSize;
      const offset = pageIndex * txPageSize;

      // We need head height; best effort: use store.head or derive from txs seen so far
      const headHeight =
        (useExplorerStore.getState().head?.height as number | undefined) ??
        Number.MAX_SAFE_INTEGER;

      const matches: TxSummary[] = [];
      let matchedCount = 0;
      for (let h = headHeight; h >= 0 && matches.length < limit; h--) {
        // eslint-disable-next-line no-await-in-loop
        const blockTxs = await rt.client.getBlockTxs(h);
        for (const t of blockTxs) {
          const a = t.from?.toLowerCase?.() ?? '';
          const b = t.to?.toLowerCase?.() ?? '';
          const c = t.contractAddress?.toLowerCase?.() ?? '';
          if (a === addr || b === addr || c === addr) {
            if (matchedCount >= offset && matches.length < limit) {
              matches.push(t);
            }
            matchedCount++;
            if (matches.length >= limit) break;
          }
        }
      }
      const entry = rt.cache.get(addr);
      entry.txPagesLoaded = Math.max(entry.txPagesLoaded, pageIndex + 1);
      entry.txTotalApprox = matchedCount; // rough bound
      return matches;
    },
    [addr, txPageSize]
  );

  const fetchTxPage = useCallback(
    async (pageIndex: number): Promise<TxSummary[]> => {
      const rt = runtime.current;
      if (!rt.client) throw new Error('RPC not ready');
      if (!addr) return [];

      const inflightKey = `${addr}:tx:${pageIndex}`;
      const existing = rt.inflightTxPage.get(inflightKey);
      if (existing) return existing;

      const p = (async () => {
        try {
          if (typeof rt.client!.getTxs === 'function') {
            return await fetchTxPageServer(pageIndex);
          }
          return await fetchTxPageFallback(pageIndex);
        } catch (e: any) {
          const msg = `[address] load tx page ${pageIndex} failed: ${
            e?.message || String(e)
          }`;
          setStatus('error');
          setError(msg);
          addToast?.({ kind: 'error', text: msg });
          return [];
        }
      })();

      rt.inflightTxPage.set(inflightKey, p);
      try {
        return await p;
      } finally {
        rt.inflightTxPage.delete(inflightKey);
      }
    },
    [addr, addToast, fetchTxPageServer, fetchTxPageFallback]
  );

  const fetchContractsPage = useCallback(
    async (pageIndex: number): Promise<ContractsDeployed[]> => {
      const rt = runtime.current;
      if (!rt.client) throw new Error('RPC not ready');
      if (!addr) return [];

      const inflightKey = `${addr}:contracts:${pageIndex}`;
      const existing = rt.inflightContracts.get(inflightKey);
      if (existing) return existing;

      const p = (async () => {
        try {
          const limit = contractsPageSize;
          const offset = pageIndex * contractsPageSize;

          if (typeof rt.client.getContractsByCreator === 'function') {
            const list = await rt.client.getContractsByCreator(
              addr,
              limit,
              offset
            );
            rt.cache.get(addr).contractsPagesLoaded = Math.max(
              rt.cache.get(addr).contractsPagesLoaded,
              pageIndex + 1
            );
            return list;
          }

          // Fallback: derive from tx pages already loaded + load more tx pages if needed
          const entry = rt.cache.get(addr);
          // Ensure we have txs up to the range that could contain enough contract deploys
          // (best-effort: load up to (pageIndex+1) pages of txs)
          const neededTxPages = Math.max(entry.txPagesLoaded, pageIndex + 1);
          for (let p = entry.txPagesLoaded; p < neededTxPages; p++) {
            // eslint-disable-next-line no-await-in-loop
            const newTxs = await fetchTxPage(p);
            entry.txs = mergeTxs(entry.txs, newTxs);
          }

          const derived = (await deriveContractsFromTxs(entry.txs, (pageIndex + 1) * limit)) || [];
          const slice = derived.slice(offset, offset + limit);

          entry.contractsPagesLoaded = Math.max(entry.contractsPagesLoaded, pageIndex + 1);
          return slice;
        } catch (e: any) {
          const msg = `[address] load contracts page ${pageIndex} failed: ${
            e?.message || String(e)
          }`;
          setStatus('error');
          setError(msg);
          addToast?.({ kind: 'error', text: msg });
          return [];
        }
      })();

      rt.inflightContracts.set(inflightKey, p);
      try {
        return await p;
      } finally {
        rt.inflightContracts.delete(inflightKey);
      }
    },
    [addr, addToast, contractsPageSize, fetchTxPage]
  );

  const refreshTop = useCallback(async () => {
    // Refresh snapshot + newest tx page (page 0)
    if (!addr) return;
    setStatus('loading');
    setError(null);
    try {
      const [snap, tx0] = await Promise.all([fetchSnapshot(), fetchTxPage(0)]);
      setSnapshot(snap);

      // Merge with existing recent txs; ensure newest-first and dedup
      setRecentTxs((prev) => {
        const merged = mergeTxs(tx0, prev);
        const entry = runtime.current.cache.get(addr);
        entry.txs = mergeTxs(entry.txs, tx0);
        // Approximate "has more"
        const total = entry.txTotalApprox ?? merged.length;
        setTxHasMore(merged.length < total);
        return merged.slice(0, entry.txTotalApprox || merged.length);
      });

      // Refresh contracts (first page)
      const c0 = await fetchContractsPage(0);
      setRecentContracts((prev) => {
        const seen = new Set(prev.map((c) => c.address));
        const merged = [...c0, ...prev.filter((c) => !seen.has(c.address))];
        setContractsHasMore(c0.length === contractsPageSize);
        return merged.slice(0, contractsPageSize);
      });

      setStatus('ready');
    } catch (e: any) {
      const msg = `[address] refresh failed: ${e?.message || String(e)}`;
      setStatus('error');
      setError(msg);
      useExplorerStore.getState().addToast?.({ kind: 'error', text: msg });
    }
  }, [addr, contractsPageSize, fetchContractsPage, fetchSnapshot, fetchTxPage]);

  const refreshNow = useCallback(async () => {
    if (!addr) return;
    // Reset local state & cache entry for this address, then refresh top
    runtime.current.cache.clear(addr);
    setRecentTxs([]);
    setRecentContracts([]);
    setTxHasMore(false);
    setContractsHasMore(false);
    await refreshTop();
  }, [addr, refreshTop]);

  const loadMoreTxs = useCallback(async () => {
    const entry = runtime.current.cache.get(addr);
    const nextPageIndex = entry.txPagesLoaded; // zero-based; we loaded 0..(n-1); next is n
    const items = await fetchTxPage(nextPageIndex);
    entry.txs = mergeTxs(entry.txs, items);
    entry.txPagesLoaded = nextPageIndex + 1;

    setRecentTxs((prev) => {
      const merged = mergeTxs(prev, items);
      const total = entry.txTotalApprox ?? merged.length;
      setTxHasMore(merged.length < total);
      return merged;
    });
  }, [addr, fetchTxPage]);

  const loadMoreContracts = useCallback(async () => {
    const entry = runtime.current.cache.get(addr);
    const nextPageIndex = entry.contractsPagesLoaded;
    const items = await fetchContractsPage(nextPageIndex);
    entry.contracts = [...entry.contracts, ...items];
    entry.contractsPagesLoaded = nextPageIndex + 1;

    setRecentContracts((prev) => {
      const seen = new Set(prev.map((c) => `${c.address}:${c.deployTx}`));
      const merged = [...prev];
      for (const c of items) {
        const k = `${c.address}:${c.deployTx}`;
        if (!seen.has(k)) {
          merged.push(c);
          seen.add(k);
        }
      }
      setContractsHasMore(items.length === (opts?.contractsPageSize ?? 10));
      return merged;
    });
  }, [addr, fetchContractsPage, opts?.contractsPageSize]);

  return {
    status,
    error,
    snapshot,
    recentTxs,
    recentContracts,
    txHasMore,
    contractsHasMore,
    loadMoreTxs,
    loadMoreContracts,
    refreshNow,
  };
}
