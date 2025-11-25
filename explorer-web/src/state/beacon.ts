/**
 * Animica Explorer — Randomness Beacon state
 * -----------------------------------------------------------------------------
 * Tracks the latest beacon and lists historical rounds for the on-chain DRB.
 * Prefers studio-services HTTP endpoints when configured, and falls back to
 * node JSON-RPC methods if not.
 *
 * Expected studio-services endpoints (if present):
 *  - GET /randomness/latest
 *  - GET /randomness/rounds?fromRound=&toRound=&page=&pageSize=
 *  - GET /randomness/rounds/{round}
 *
 * JSON-RPC fallbacks (node):
 *  - randomness_getLatestBeacon()
 *  - randomness_listBeacons(filters, paging)
 *  - randomness_getBeacon(round)
 *
 * Compatibility: we'll also try alternative RPC names used in some builds:
 *  - rand_getLatest, rand_getRounds, rand_getRound
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { shallow } from 'zustand/shallow';
import { useExplorerStore } from './store';

// --------------------------------- Types ------------------------------------

export type LoadState = 'idle' | 'loading' | 'ready' | 'error';

export interface BeaconRound {
  round: number;              // monotonic round number
  randomness: string;         // hex-encoded output (e.g., 0x...)
  signature?: string;         // optional group/BLS signature or VRF proof
  prevSignature?: string;     // optional chaining material
  blockHeight?: number;       // block height where beacon was finalized
  producedAtISO?: string;     // RFC3339 timestamp (if provided by API)
  participants?: string[];    // optional committee set identities
  proofs?: unknown;           // optional inclusion/DA proofs
  [k: string]: unknown;       // passthrough for explorer detail panes
}

export interface RoundsQuery {
  fromRound?: number;
  toRound?: number;
  page?: number;              // 1-based
  pageSize?: number;          // default 25
}

export interface UseBeaconOptions {
  autoRefreshOnHead?: boolean;  // default true
}

export interface UseBeaconResult {
  state: LoadState;
  latest: BeaconRound | null;

  roundsState: LoadState;
  rounds: BeaconRound[];
  total: number;
  query: RoundsQuery;

  setQuery: (q: RoundsQuery) => void;

  refreshLatest: () => Promise<void>;
  refreshRounds: (override?: RoundsQuery) => Promise<void>;

  getRound: (round: number) => Promise<BeaconRound | null>;

  error: string | null;
}

// ------------------------------- Utilities ----------------------------------

function joinUrl(base: string, path: string): string {
  if (!base) return path;
  const b = base.endsWith('/') ? base.slice(0, -1) : base;
  const p = path.startsWith('/') ? path : `/${path}`;
  return `${b}${p}`;
}

function buildQuery(base: string, q: Record<string, any | undefined>): string {
  const url = new URL(base, 'http://x'); // dummy origin for relative URL
  const params = url.searchParams;
  for (const [k, v] of Object.entries(q)) {
    if (v === undefined || v === null || v === '') continue;
    params.set(k, String(v));
  }
  const qs = params.toString();
  return `${base}${qs ? `?${qs}` : ''}`;
}

async function httpGet<T>(url: string): Promise<T> {
  const res = await fetch(url, { headers: { accept: 'application/json' } });
  if (!res.ok) {
    const txt = await res.text().catch(() => '');
    throw new Error(`GET ${url} -> ${res.status}: ${txt || res.statusText}`);
  }
  return (await res.json()) as T;
}

let rpcCounter = 0;
async function jsonRpc<T = unknown>(rpcUrl: string, method: string, params: any[] = []): Promise<T> {
  const body = JSON.stringify({ jsonrpc: '2.0', id: ++rpcCounter, method, params });
  const res = await fetch(rpcUrl, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body,
  });
  if (!res.ok) {
    const txt = await res.text().catch(() => '');
    throw new Error(`RPC ${method} -> ${res.status}: ${txt || res.statusText}`);
  }
  const json = await res.json().catch(() => null);
  if (!json || json.error) {
    throw new Error(`RPC ${method} error: ${json?.error?.message ?? 'unknown error'}`);
  }
  return json.result as T;
}

async function tryRpcMethods<T>(
  rpcUrl: string,
  candidates: string[],
  params: any[] = []
): Promise<T> {
  let lastErr: unknown = null;
  for (const m of candidates) {
    try {
      return await jsonRpc<T>(rpcUrl, m, params);
    } catch (e) {
      lastErr = e;
    }
  }
  throw lastErr instanceof Error ? lastErr : new Error(String(lastErr));
}

// ------------------------------- Normalizers --------------------------------

function toInt(x: any): number | undefined {
  if (typeof x === 'number' && Number.isFinite(x)) return x | 0;
  const n = Number(x);
  return Number.isFinite(n) ? (n | 0) : undefined;
}

function normBeacon(x: any): BeaconRound {
  // Accept a variety of shapes from services or RPC
  const round = toInt(x?.round) ?? toInt(x?.height) ?? toInt(x?.epoch) ?? 0;
  const randomness = String(x?.randomness ?? x?.output ?? x?.beacon ?? '');
  const signature = x?.signature ? String(x.signature) : x?.sig ? String(x.sig) : undefined;
  const prevSignature =
    x?.prevSignature ? String(x.prevSignature) : x?.previousSignature ? String(x.previousSignature) : undefined;
  const blockHeight = toInt(x?.blockHeight ?? x?.finalizedHeight);
  const producedAtISO =
    typeof x?.producedAtISO === 'string'
      ? x.producedAtISO
      : typeof x?.timestamp === 'string'
      ? x.timestamp
      : undefined;

  return {
    round,
    randomness,
    signature,
    prevSignature,
    blockHeight,
    producedAtISO,
    participants: Array.isArray(x?.participants) ? x.participants.map((s: any) => String(s)) : undefined,
    proofs: x?.proofs ?? x?.proof ?? undefined,
    ...x,
  };
}

// --------------------------------- Hook -------------------------------------

export function useBeacon(options?: UseBeaconOptions): UseBeaconResult {
  const { autoRefreshOnHead = true } = options ?? {};

  const { servicesUrl, rpcUrl, head, addToast } = useExplorerStore(
    (s) => ({
      servicesUrl: (s as any).network?.servicesUrl as string | undefined,
      rpcUrl: (s as any).network?.rpcUrl as string | undefined,
      head: (s as any).head as { height: number } | null,
      addToast: (s as any).addToast as (t: {
        kind: 'info' | 'error' | 'success';
        text: string;
      }) => void,
    }),
    shallow
  );

  const [state, setState] = useState<LoadState>('idle');
  const [latest, setLatest] = useState<BeaconRound | null>(null);

  const [roundsState, setRoundsState] = useState<LoadState>('idle');
  const [rounds, setRounds] = useState<BeaconRound[]>([]);
  const [total, setTotal] = useState<number>(0);
  const [query, setQuery] = useState<RoundsQuery>({ page: 1, pageSize: 25 });

  const [error, setError] = useState<string | null>(null);

  const servicesEnabled = !!servicesUrl;
  const rpcEnabled = !!rpcUrl;

  const refreshLatest = useCallback(async () => {
    setState('loading');
    setError(null);
    try {
      let res: any = null;
      if (servicesEnabled) {
        const url = joinUrl(servicesUrl!, '/randomness/latest');
        res = await httpGet<any>(url);
      } else if (rpcEnabled) {
        res = await tryRpcMethods<any>(rpcUrl!, ['randomness_getLatestBeacon', 'rand_getLatest']);
      }
      const b = normBeacon(res);
      if (!b.randomness || !Number.isFinite(b.round)) {
        throw new Error('Latest beacon payload malformed');
      }
      setLatest(b);
      setState('ready');
    } catch (e: any) {
      const msg = `[Beacon] latest fetch failed: ${e?.message || String(e)}`;
      setState('error');
      setError(msg);
      addToast?.({ kind: 'error', text: msg });
    }
  }, [addToast, rpcEnabled, rpcUrl, servicesEnabled, servicesUrl]);

  const refreshRounds = useCallback(async (override?: RoundsQuery) => {
    const q = { ...query, ...(override ?? {}) };
    setQuery(q);
    setRoundsState('loading');
    setError(null);
    try {
      let items: any[] = [];
      let totalCount = 0;
      if (servicesEnabled) {
        const base = joinUrl(servicesUrl!, '/randomness/rounds');
        const url = buildQuery(base, {
          fromRound: q.fromRound,
          toRound: q.toRound,
          page: q.page ?? 1,
          pageSize: q.pageSize ?? 25,
        });
        const res = await httpGet<any>(url);
        items = Array.isArray(res?.items) ? res.items : Array.isArray(res) ? res : [];
        totalCount = typeof res?.total === 'number' ? res.total : items.length;
      } else if (rpcEnabled) {
        const res = await tryRpcMethods<any>(rpcUrl!, ['randomness_listBeacons', 'rand_getRounds'], [
          { fromRound: q.fromRound, toRound: q.toRound },
          { page: q.page ?? 1, pageSize: q.pageSize ?? 25 },
        ]);
        items = Array.isArray(res?.items) ? res.items : Array.isArray(res) ? res : [];
        totalCount = typeof res?.total === 'number' ? res.total : items.length;
      }

      const list = items.map((x) => normBeacon(x)).filter((b) => Number.isFinite(b.round) && !!b.randomness);
      // sort desc by round for explorer UX
      list.sort((a, b) => b.round - a.round);

      setRounds(list);
      setTotal(totalCount);
      setRoundsState('ready');
    } catch (e: any) {
      const msg = `[Beacon] rounds fetch failed: ${e?.message || String(e)}`;
      setRoundsState('error');
      setError(msg);
      addToast?.({ kind: 'error', text: msg });
    }
  }, [addToast, query, rpcEnabled, rpcUrl, servicesEnabled, servicesUrl]);

  const getRound = useCallback(async (round: number): Promise<BeaconRound | null> => {
    if (!Number.isFinite(round)) return null;
    try {
      if (servicesEnabled) {
        const url = joinUrl(servicesUrl!, `/randomness/rounds/${round}`);
        const res = await httpGet<any>(url);
        return normBeacon(res);
      }
      if (rpcEnabled) {
        const res = await tryRpcMethods<any>(rpcUrl!, ['randomness_getBeacon', 'rand_getRound'], [round]);
        return normBeacon(res);
      }
      return null;
    } catch {
      return null;
    }
  }, [rpcEnabled, rpcUrl, servicesEnabled, servicesUrl]);

  // --------------------------- Auto-refresh on head --------------------------

  const lastHead = useRef<number | null>(null);
  useEffect(() => {
    // Initial fetch on mount / endpoint change
    void refreshLatest();
    void refreshRounds();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [servicesUrl, rpcUrl]);

  useEffect(() => {
    if (!autoRefreshOnHead) return;
    const h = head?.height ?? null;
    if (h == null) return;
    if (lastHead.current == null) {
      lastHead.current = h;
      return;
    }
    if (h > lastHead.current) {
      void refreshLatest();
      // Only refresh the first page on head advance to keep it snappy
      void refreshRounds({ page: 1 });
      lastHead.current = h;
    }
  }, [autoRefreshOnHead, head?.height, refreshLatest, refreshRounds]);

  return {
    state,
    latest,

    roundsState,
    rounds,
    total,
    query,

    setQuery,
    refreshLatest,
    refreshRounds,
    getRound,

    error,
  };
}
