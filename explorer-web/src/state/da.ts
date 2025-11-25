/**
 * Animica Explorer — Data Availability (DA) state
 * -----------------------------------------------------------------------------
 * Lists DA blobs and commitments, supports paging/filters, and can retrieve
 * per-commitment inclusion proofs. Uses studio-services HTTP endpoints when
 * available, with JSON-RPC fallbacks to the node.
 *
 * Expected studio-services endpoints (if present):
 *  - GET /da/blobs?owner=&commitment=&namespace=&page=&pageSize=
 *  - GET /da/blobs/{id}
 *  - GET /da/commitments?namespace=&fromHeight=&toHeight=&page=&pageSize=
 *  - GET /da/commitments/{cid}
 *  - GET /da/proof?commitment={cid}
 *
 * JSON-RPC fallbacks (node):
 *  - da_listBlobs(filters, paging)
 *  - da_getBlob(id)
 *  - da_listCommitments(filters, paging)
 *  - da_getCommitment(commitment)
 *  - da_getProof(commitment)
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { shallow } from 'zustand/shallow';
import { useExplorerStore } from './store';

// --------------------------------- Types ------------------------------------

export type LoadState = 'idle' | 'loading' | 'ready' | 'error';

export interface BlobMeta {
  id: string;                 // unique blob id (content address or tx hash)
  size: number;               // bytes
  owner?: string;             // optional owner address
  commitment: string;         // hex commitment (e.g., NMT root or hash)
  namespace?: string;         // optional namespace / app id
  blockHeight?: number;       // posted block
  txHash?: string | null;     // posting tx hash, if applicable
  createdAtISO?: string;      // server timestamp
  dataRef?: string | null;    // optional URL or DA ref
  [k: string]: unknown;       // passthrough for explorer details pane
}

export interface CommitmentMeta {
  commitment: string;         // hex
  namespace?: string;
  size?: number;              // bytes covered
  leaves?: number;            // number of leaves, if merkle-ish
  root?: string;              // equals commitment in many schemes
  blockHeight?: number;
  postedAtISO?: string;
  included?: boolean;         // convenience flag
  [k: string]: unknown;
}

export interface ProofRecord {
  commitment: string;
  proof: unknown;             // opaque; UI can pretty-print JSON
  verified?: boolean;         // optional verification hint
}

export interface BlobsQuery {
  owner?: string;
  commitment?: string;
  namespace?: string;
  page?: number;
  pageSize?: number;
}

export interface CommitmentsQuery {
  namespace?: string;
  fromHeight?: number;
  toHeight?: number;
  page?: number;
  pageSize?: number;
}

export interface UseDAOptions {
  autoRefreshOnHead?: boolean;  // default true
}

export interface UseDAResult {
  // Blobs list
  blobsState: LoadState;
  blobs: BlobMeta[];
  blobsTotal: number;
  blobsQuery: BlobsQuery;
  setBlobsQuery: (q: BlobsQuery) => void;
  refreshBlobs: (override?: BlobsQuery) => Promise<void>;
  getBlob: (id: string) => Promise<BlobMeta | null>;

  // Commitments list
  commitmentsState: LoadState;
  commitments: CommitmentMeta[];
  commitmentsTotal: number;
  commitmentsQuery: CommitmentsQuery;
  setCommitmentsQuery: (q: CommitmentsQuery) => void;
  refreshCommitments: (override?: CommitmentsQuery) => Promise<void>;
  getCommitment: (cid: string) => Promise<CommitmentMeta | null>;

  // Proofs
  getProof: (cid: string) => Promise<ProofRecord | null>;

  // Error (last)
  error: string | null;
}

// ------------------------------ Internals -----------------------------------

function joinUrl(base: string, path: string): string {
  if (!base) return path;
  const b = base.endsWith('/') ? base.slice(0, -1) : base;
  const p = path.startsWith('/') ? path : `/${path}`;
  return `${b}${p}`;
}

function buildQuery(base: string, q: Record<string, any | undefined>): string {
  const url = new URL(base, 'http://x'); // dummy origin to use URL API
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

// ------------------------------- Normalizers --------------------------------

function normBlob(x: any): BlobMeta {
  return {
    id: String(x?.id ?? x?.hash ?? ''),
    size: Number.isFinite(x?.size) ? Number(x.size) : Number(x?.length ?? 0),
    owner: x?.owner ? String(x.owner) : undefined,
    commitment: String(x?.commitment ?? x?.root ?? x?.cid ?? ''),
    namespace: x?.namespace ? String(x.namespace) : undefined,
    blockHeight: Number.isFinite(x?.blockHeight) ? Number(x.blockHeight) : undefined,
    txHash: x?.txHash ? String(x.txHash) : null,
    createdAtISO: x?.createdAtISO ? String(x.createdAtISO) : undefined,
    dataRef: x?.dataRef ?? null,
    ...x,
  };
}

function normCommitment(x: any): CommitmentMeta {
  return {
    commitment: String(x?.commitment ?? x?.cid ?? x?.root ?? ''),
    namespace: x?.namespace ? String(x.namespace) : undefined,
    size: Number.isFinite(x?.size) ? Number(x.size) : undefined,
    leaves: Number.isFinite(x?.leaves) ? Number(x.leaves) : undefined,
    root: x?.root ? String(x.root) : undefined,
    blockHeight: Number.isFinite(x?.blockHeight) ? Number(x.blockHeight) : undefined,
    postedAtISO: x?.postedAtISO ? String(x.postedAtISO) : undefined,
    included: x?.included === true || x?.verified === true ? true : x?.included === false ? false : undefined,
    ...x,
  };
}

// --------------------------------- Hook -------------------------------------

export function useDA(options?: UseDAOptions): UseDAResult {
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

  const servicesEnabled = !!servicesUrl;
  const rpcEnabled = !!rpcUrl;

  // Blobs
  const [blobsState, setBlobsState] = useState<LoadState>('idle');
  const [blobs, setBlobs] = useState<BlobMeta[]>([]);
  const [blobsTotal, setBlobsTotal] = useState<number>(0);
  const [blobsQuery, setBlobsQuery] = useState<BlobsQuery>({ page: 1, pageSize: 25 });

  // Commitments
  const [commitmentsState, setCommitmentsState] = useState<LoadState>('idle');
  const [commitments, setCommitments] = useState<CommitmentMeta[]>([]);
  const [commitmentsTotal, setCommitmentsTotal] = useState<number>(0);
  const [commitmentsQuery, setCommitmentsQuery] = useState<CommitmentsQuery>({ page: 1, pageSize: 25 });

  const [error, setError] = useState<string | null>(null);

  // ------------------------------- Fetchers ----------------------------------

  const refreshBlobs = useCallback(async (override?: BlobsQuery) => {
    const q = { ...blobsQuery, ...(override ?? {}) };
    setBlobsQuery(q);
    setBlobsState('loading');
    setError(null);
    try {
      let list: any[] = [];
      let total = 0;
      if (servicesEnabled) {
        const base = joinUrl(servicesUrl!, '/da/blobs');
        const url = buildQuery(base, {
          owner: q.owner,
          commitment: q.commitment,
          namespace: q.namespace,
          page: q.page,
          pageSize: q.pageSize,
        });
        const res = await httpGet<any>(url);
        const items = Array.isArray(res?.items) ? res.items : Array.isArray(res) ? res : [];
        list = items;
        total = typeof res?.total === 'number' ? res.total : items.length;
      } else if (rpcEnabled) {
        const res = await jsonRpc<any>(rpcUrl!, 'da_listBlobs', [
          { owner: q.owner, commitment: q.commitment, namespace: q.namespace },
          { page: q.page, pageSize: q.pageSize },
        ]);
        const items = Array.isArray(res?.items) ? res.items : Array.isArray(res) ? res : [];
        list = items;
        total = typeof res?.total === 'number' ? res.total : items.length;
      }
      const normalized = list.map((x) => normBlob(x)).filter((b) => b.id && b.commitment);
      setBlobs(normalized);
      setBlobsTotal(total);
      setBlobsState('ready');
    } catch (e: any) {
      const msg = `[DA] blobs fetch failed: ${e?.message || String(e)}`;
      setBlobsState('error');
      setError(msg);
      addToast?.({ kind: 'error', text: msg });
    }
  }, [addToast, blobsQuery, rpcEnabled, rpcUrl, servicesEnabled, servicesUrl]);

  const getBlob = useCallback(async (id: string): Promise<BlobMeta | null> => {
    if (!id) return null;
    try {
      if (servicesEnabled) {
        const url = joinUrl(servicesUrl!, `/da/blobs/${encodeURIComponent(id)}`);
        const res = await httpGet<any>(url);
        return normBlob(res);
      }
      if (rpcEnabled) {
        const res = await jsonRpc<any>(rpcUrl!, 'da_getBlob', [id]);
        return normBlob(res);
      }
      return null;
    } catch {
      return null;
    }
  }, [rpcEnabled, rpcUrl, servicesEnabled, servicesUrl]);

  const refreshCommitments = useCallback(async (override?: CommitmentsQuery) => {
    const q = { ...commitmentsQuery, ...(override ?? {}) };
    setCommitmentsQuery(q);
    setCommitmentsState('loading');
    setError(null);
    try {
      let list: any[] = [];
      let total = 0;
      if (servicesEnabled) {
        const base = joinUrl(servicesUrl!, '/da/commitments');
        const url = buildQuery(base, {
          namespace: q.namespace,
          fromHeight: q.fromHeight,
          toHeight: q.toHeight,
          page: q.page,
          pageSize: q.pageSize,
        });
        const res = await httpGet<any>(url);
        const items = Array.isArray(res?.items) ? res.items : Array.isArray(res) ? res : [];
        list = items;
        total = typeof res?.total === 'number' ? res.total : items.length;
      } else if (rpcEnabled) {
        const res = await jsonRpc<any>(rpcUrl!, 'da_listCommitments', [
          { namespace: q.namespace, fromHeight: q.fromHeight, toHeight: q.toHeight },
          { page: q.page, pageSize: q.pageSize },
        ]);
        const items = Array.isArray(res?.items) ? res.items : Array.isArray(res) ? res : [];
        list = items;
        total = typeof res?.total === 'number' ? res.total : items.length;
      }
      const normalized = list.map((x) => normCommitment(x)).filter((c) => c.commitment);
      setCommitments(normalized);
      setCommitmentsTotal(total);
      setCommitmentsState('ready');
    } catch (e: any) {
      const msg = `[DA] commitments fetch failed: ${e?.message || String(e)}`;
      setCommitmentsState('error');
      setError(msg);
      addToast?.({ kind: 'error', text: msg });
    }
  }, [addToast, commitmentsQuery, rpcEnabled, rpcUrl, servicesEnabled, servicesUrl]);

  const getCommitment = useCallback(async (cid: string): Promise<CommitmentMeta | null> => {
    if (!cid) return null;
    try {
      if (servicesEnabled) {
        const url = joinUrl(servicesUrl!, `/da/commitments/${encodeURIComponent(cid)}`);
        const res = await httpGet<any>(url);
        return normCommitment(res);
      }
      if (rpcEnabled) {
        const res = await jsonRpc<any>(rpcUrl!, 'da_getCommitment', [cid]);
        return normCommitment(res);
      }
      return null;
    } catch {
      return null;
    }
  }, [rpcEnabled, rpcUrl, servicesEnabled, servicesUrl]);

  const getProof = useCallback(async (cid: string): Promise<ProofRecord | null> => {
    if (!cid) return null;
    try {
      if (servicesEnabled) {
        const base = joinUrl(servicesUrl!, '/da/proof');
        const url = buildQuery(base, { commitment: cid });
        const res = await httpGet<any>(url);
        return {
          commitment: String(res?.commitment ?? cid),
          proof: res?.proof ?? res ?? null,
          verified: typeof res?.verified === 'boolean' ? res.verified : undefined,
        };
      }
      if (rpcEnabled) {
        const res = await jsonRpc<any>(rpcUrl!, 'da_getProof', [cid]);
        return {
          commitment: String(res?.commitment ?? cid),
          proof: res?.proof ?? res ?? null,
          verified: typeof res?.verified === 'boolean' ? res.verified : undefined,
        };
      }
      return null;
    } catch (e: any) {
      const msg = `[DA] proof fetch failed: ${e?.message || String(e)}`;
      setError(msg);
      addToast?.({ kind: 'error', text: msg });
      return null;
    }
  }, [addToast, rpcEnabled, rpcUrl, servicesEnabled, servicesUrl]);

  // --------------------------- Auto-refresh on head --------------------------

  const lastHead = useRef<number | null>(null);
  useEffect(() => {
    // Initial loads on mount / endpoint change
    void refreshBlobs();
    void refreshCommitments();
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
      void refreshBlobs();
      void refreshCommitments();
      lastHead.current = h;
    }
  }, [autoRefreshOnHead, head?.height, refreshBlobs, refreshCommitments]);

  // -------------------------------- Result ----------------------------------

  return {
    // blobs
    blobsState,
    blobs,
    blobsTotal,
    blobsQuery,
    setBlobsQuery,
    refreshBlobs,
    getBlob,

    // commitments
    commitmentsState,
    commitments,
    commitmentsTotal,
    commitmentsQuery,
    setCommitmentsQuery,
    refreshCommitments,
    getCommitment,

    // proofs
    getProof,

    // last error
    error,
  };
}
