/**
 * Animica Explorer — AICF state
 * -----------------------------------------------------------------------------
 * Providers, Jobs, Settlements, and SLA statistics for the AI/Quantum Compute
 * Fabric (AICF). This module prefers studio-services HTTP endpoints when
 * available and falls back to node JSON-RPC methods if services are disabled.
 *
 * Integrations:
 *  - useExplorerStore() must expose:
 *      - network.rpcUrl?: string
 *      - network.servicesUrl?: string
 *      - head?: { height: number }
 *      - addToast?: (t: { kind: 'info'|'error'|'success'; text: string }) => void
 *
 * Endpoints expected (studio-services):
 *  - GET /aicf/providers
 *  - GET /aicf/jobs?address=&provider=&status=&page=&pageSize=
 *  - GET /aicf/jobs/{id}
 *  - GET /aicf/settlements?provider=&fromHeight=&toHeight=&page=&pageSize=
 *  - GET /aicf/stats?window=24h|7d|30d
 *
 * JSON-RPC fallbacks (node):
 *  - aicf_listProviders()
 *  - aicf_listJobs(filters, paging)
 *  - aicf_getJob(jobId)
 *  - aicf_listSettlements(filters, paging)
 *  - aicf_getStats(window)
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { shallow } from 'zustand/shallow';
import { useExplorerStore } from './store';

// --------------------------------- Types ------------------------------------

export type ProviderKind = 'ai' | 'quantum';

export interface ProviderInfo {
  id: string;                    // provider address or DID
  kind: ProviderKind;
  name?: string;
  models?: string[];             // for AI providers
  capabilities?: string[];       // freeform tags/features
  feeRate?: number;              // base fee per unit (e.g., token/ms/shot)
  lastSeenISO?: string;          // last heartbeat
  uptimePct24h?: number;         // convenience
  // passthrough
  [k: string]: unknown;
}

export type JobStatus = 'queued' | 'running' | 'settled' | 'failed' | 'canceled';

export interface JobRecord {
  id: string;
  kind: ProviderKind;
  requester: string;             // user address
  provider: string;              // provider id/address
  model?: string;                // AI model id
  promptHash?: string;           // hex for privacy-preserving prompt ref
  circuitHash?: string;          // for quantum jobs
  createdAtISO: string;
  updatedAtISO?: string;
  status: JobStatus;
  cost?: string;                 // stringified integer (native smallest unit)
  resultRef?: string | null;     // content address / DA ref
  receiptTx?: string | null;     // settlement tx hash
  // passthrough
  [k: string]: unknown;
}

export interface SettlementRecord {
  id: string;
  provider: string;
  jobId: string;
  amount: string;                // stringified integer
  timestampISO: string;
  txHash: string;
  blockHeight: number;
  status?: 'confirmed' | 'pending' | 'reverted';
  // passthrough
  [k: string]: unknown;
}

export interface SLAStats {
  window: '24h' | '7d' | '30d';
  totals: {
    jobs: number;
    settled: number;
    failed: number;
  };
  latencyMs: {
    avg: number;
    p95: number;
  };
  providers: Array<{
    id: string;
    successRate: number;       // 0..1
    avgLatencyMs: number;
    p95LatencyMs: number;
    settledJobs: number;
    failedJobs: number;
    uptimePct: number;         // 0..100
  }>;
}

export type LoadState = 'idle' | 'loading' | 'ready' | 'error';

export interface JobsQuery {
  address?: string;
  provider?: string;
  status?: JobStatus | 'all';
  page?: number;
  pageSize?: number;
}

export interface SettlementsQuery {
  provider?: string;
  fromHeight?: number;
  toHeight?: number;
  page?: number;
  pageSize?: number;
}

export interface UseAICFOptions {
  autoRefreshOnHead?: boolean;   // default true
  pollPendingJobs?: boolean;     // default true
  pollIntervalMs?: number;       // default 4000
}

export interface UseAICFResult {
  // Providers
  providersState: LoadState;
  providers: ProviderInfo[];
  refreshProviders: () => Promise<void>;

  // Jobs
  jobsState: LoadState;
  jobs: JobRecord[];
  jobsTotal: number;
  jobsQuery: JobsQuery;
  setJobsQuery: (q: JobsQuery) => void;
  refreshJobs: (q?: JobsQuery) => Promise<void>;
  getJob: (id: string) => Promise<JobRecord | null>;

  // Settlements
  settlementsState: LoadState;
  settlements: SettlementRecord[];
  settlementsTotal: number;
  settlementsQuery: SettlementsQuery;
  setSettlementsQuery: (q: SettlementsQuery) => void;
  refreshSettlements: (q?: SettlementsQuery) => Promise<void>;

  // SLA
  statsState: LoadState;
  stats: SLAStats | null;
  statsWindow: '24h' | '7d' | '30d';
  setStatsWindow: (w: '24h' | '7d' | '30d') => void;
  refreshStats: (w?: '24h' | '7d' | '30d') => Promise<void>;

  // Misc
  error: string | null;
  trackJobUntilTerminal: (id: string) => Promise<JobRecord | null>;
}

// ------------------------------ Internals -----------------------------------

function joinUrl(base: string, path: string): string {
  if (!base) return path;
  const b = base.endsWith('/') ? base.slice(0, -1) : base;
  const p = path.startsWith('/') ? path : `/${path}`;
  return `${b}${p}`;
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

function buildQuery(base: string, q: Record<string, any | undefined>): string {
  const url = new URL(base, 'http://x'); // dummy base to use URLSearchParams
  const params = url.searchParams;
  for (const [k, v] of Object.entries(q)) {
    if (v === undefined || v === null || v === '') continue;
    params.set(k, String(v));
  }
  return `${base}${params.toString() ? `?${params.toString()}` : ''}`;
}

function isTerminal(status: JobStatus): boolean {
  return status === 'settled' || status === 'failed' || status === 'canceled';
}

// --------------------------------- Hook -------------------------------------

export function useAICF(options?: UseAICFOptions): UseAICFResult {
  const {
    autoRefreshOnHead = true,
    pollPendingJobs = true,
    pollIntervalMs = 4000,
  } = options ?? {};

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

  // Providers
  const [providersState, setProvidersState] = useState<LoadState>('idle');
  const [providers, setProviders] = useState<ProviderInfo[]>([]);

  // Jobs
  const [jobsState, setJobsState] = useState<LoadState>('idle');
  const [jobs, setJobs] = useState<JobRecord[]>([]);
  const [jobsTotal, setJobsTotal] = useState<number>(0);
  const [jobsQuery, setJobsQuery] = useState<JobsQuery>({ status: 'all', page: 1, pageSize: 25 });

  // Settlements
  const [settlementsState, setSettlementsState] = useState<LoadState>('idle');
  const [settlements, setSettlements] = useState<SettlementRecord[]>([]);
  const [settlementsTotal, setSettlementsTotal] = useState<number>(0);
  const [settlementsQuery, setSettlementsQuery] = useState<SettlementsQuery>({ page: 1, pageSize: 25 });

  // SLA
  const [statsState, setStatsState] = useState<LoadState>('idle');
  const [stats, setStats] = useState<SLAStats | null>(null);
  const [statsWindow, setStatsWindow] = useState<'24h' | '7d' | '30d'>('24h');

  const [error, setError] = useState<string | null>(null);

  const pollTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const servicesEnabled = !!servicesUrl;
  const rpcEnabled = !!rpcUrl;

  // -------------------------- Fetchers (dual-path) ---------------------------

  const refreshProviders = useCallback(async () => {
    setProvidersState('loading');
    setError(null);
    try {
      let list: ProviderInfo[] = [];
      if (servicesEnabled) {
        const url = joinUrl(servicesUrl!, '/aicf/providers');
        const res = await httpGet<any>(url);
        list = (Array.isArray(res?.items) ? res.items : Array.isArray(res) ? res : []).map((p: any) => ({
          id: String(p?.id ?? ''),
          kind: (p?.kind === 'quantum' ? 'quantum' : 'ai') as ProviderKind,
          name: p?.name,
          models: Array.isArray(p?.models) ? p.models.map(String) : undefined,
          capabilities: Array.isArray(p?.capabilities) ? p.capabilities.map(String) : undefined,
          feeRate: typeof p?.feeRate === 'number' ? p.feeRate : undefined,
          lastSeenISO: p?.lastSeenISO,
          uptimePct24h: typeof p?.uptimePct24h === 'number' ? p.uptimePct24h : undefined,
          ...p,
        })).filter((p: ProviderInfo) => p.id);
      } else if (rpcEnabled) {
        const res = await jsonRpc<any[]>(rpcUrl!, 'aicf_listProviders', []);
        list = (res ?? []).map((p: any) => ({
          id: String(p?.id ?? ''),
          kind: (p?.kind === 'quantum' ? 'quantum' : 'ai') as ProviderKind,
          name: p?.name,
          models: Array.isArray(p?.models) ? p.models.map(String) : undefined,
          capabilities: Array.isArray(p?.capabilities) ? p.capabilities.map(String) : undefined,
          feeRate: typeof p?.feeRate === 'number' ? p.feeRate : undefined,
          lastSeenISO: p?.lastSeenISO,
          uptimePct24h: typeof p?.uptimePct24h === 'number' ? p.uptimePct24h : undefined,
          ...p,
        })).filter((p: ProviderInfo) => p.id);
      } else {
        list = [];
      }
      setProviders(list);
      setProvidersState('ready');
    } catch (e: any) {
      const msg = `[AICF] providers fetch failed: ${e?.message || String(e)}`;
      setProvidersState('error');
      setError(msg);
      addToast?.({ kind: 'error', text: msg });
    }
  }, [addToast, rpcEnabled, rpcUrl, servicesEnabled, servicesUrl]);

  const refreshJobs = useCallback(async (override?: JobsQuery) => {
    const q = { ...jobsQuery, ...(override ?? {}) };
    setJobsQuery(q);
    setJobsState('loading');
    setError(null);
    try {
      let items: JobRecord[] = [];
      let total = 0;

      if (servicesEnabled) {
        const base = joinUrl(servicesUrl!, '/aicf/jobs');
        const url = buildQuery(base, {
          address: q.address,
          provider: q.provider,
          status: q.status && q.status !== 'all' ? q.status : undefined,
          page: q.page,
          pageSize: q.pageSize,
        });
        const res = await httpGet<any>(url);
        const list = Array.isArray(res?.items) ? res.items : Array.isArray(res) ? res : [];
        items = list.map((j: any) => normalizeJob(j)).filter((j: JobRecord) => j.id);
        total = typeof res?.total === 'number' ? res.total : items.length;
      } else if (rpcEnabled) {
        const res = await jsonRpc<any>(rpcUrl!, 'aicf_listJobs', [
          {
            address: q.address,
            provider: q.provider,
            status: q.status && q.status !== 'all' ? q.status : undefined,
          },
          { page: q.page, pageSize: q.pageSize },
        ]);
        const list = Array.isArray(res?.items) ? res.items : Array.isArray(res) ? res : [];
        items = list.map((j: any) => normalizeJob(j)).filter((j: JobRecord) => j.id);
        total = typeof res?.total === 'number' ? res.total : items.length;
      } else {
        items = [];
        total = 0;
      }

      setJobs(items);
      setJobsTotal(total);
      setJobsState('ready');
    } catch (e: any) {
      const msg = `[AICF] jobs fetch failed: ${e?.message || String(e)}`;
      setJobsState('error');
      setError(msg);
      addToast?.({ kind: 'error', text: msg });
    }
  }, [addToast, jobsQuery, rpcEnabled, rpcUrl, servicesEnabled, servicesUrl]);

  const getJob = useCallback(async (id: string): Promise<JobRecord | null> => {
    if (!id) return null;
    try {
      if (servicesEnabled) {
        const url = joinUrl(servicesUrl!, `/aicf/jobs/${encodeURIComponent(id)}`);
        const res = await httpGet<any>(url);
        return normalizeJob(res);
      } else if (rpcEnabled) {
        const res = await jsonRpc<any>(rpcUrl!, 'aicf_getJob', [id]);
        return normalizeJob(res);
      }
      return null;
    } catch {
      return null;
    }
  }, [rpcEnabled, rpcUrl, servicesEnabled, servicesUrl]);

  const refreshSettlements = useCallback(async (override?: SettlementsQuery) => {
    const q = { ...settlementsQuery, ...(override ?? {}) };
    setSettlementsQuery(q);
    setSettlementsState('loading');
    setError(null);
    try {
      let items: SettlementRecord[] = [];
      let total = 0;

      if (servicesEnabled) {
        const base = joinUrl(servicesUrl!, '/aicf/settlements');
        const url = buildQuery(base, {
          provider: q.provider,
          fromHeight: q.fromHeight,
          toHeight: q.toHeight,
          page: q.page,
          pageSize: q.pageSize,
        });
        const res = await httpGet<any>(url);
        const list = Array.isArray(res?.items) ? res.items : Array.isArray(res) ? res : [];
        items = list.map((s: any) => normalizeSettlement(s)).filter((s: SettlementRecord) => s.id);
        total = typeof res?.total === 'number' ? res.total : items.length;
      } else if (rpcEnabled) {
        const res = await jsonRpc<any>(rpcUrl!, 'aicf_listSettlements', [
          {
            provider: q.provider,
            fromHeight: q.fromHeight,
            toHeight: q.toHeight,
          },
          { page: q.page, pageSize: q.pageSize },
        ]);
        const list = Array.isArray(res?.items) ? res.items : Array.isArray(res) ? res : [];
        items = list.map((s: any) => normalizeSettlement(s)).filter((s: SettlementRecord) => s.id);
        total = typeof res?.total === 'number' ? res.total : items.length;
      } else {
        items = [];
        total = 0;
      }

      setSettlements(items);
      setSettlementsTotal(total);
      setSettlementsState('ready');
    } catch (e: any) {
      const msg = `[AICF] settlements fetch failed: ${e?.message || String(e)}`;
      setSettlementsState('error');
      setError(msg);
      addToast?.({ kind: 'error', text: msg });
    }
  }, [addToast, rpcEnabled, rpcUrl, servicesEnabled, servicesUrl, settlementsQuery]);

  const refreshStats = useCallback(async (w?: '24h'|'7d'|'30d') => {
    const windowSel = w ?? statsWindow;
    if (w) setStatsWindow(w);
    setStatsState('loading');
    setError(null);
    try {
      let data: SLAStats | null = null;
      if (servicesEnabled) {
        const base = joinUrl(servicesUrl!, '/aicf/stats');
        const url = buildQuery(base, { window: windowSel });
        const res = await httpGet<any>(url);
        data = normalizeStats(windowSel, res);
      } else if (rpcEnabled) {
        const res = await jsonRpc<any>(rpcUrl!, 'aicf_getStats', [windowSel]);
        data = normalizeStats(windowSel, res);
      } else {
        data = null;
      }
      setStats(data);
      setStatsState('ready');
    } catch (e: any) {
      const msg = `[AICF] stats fetch failed: ${e?.message || String(e)}`;
      setStatsState('error');
      setError(msg);
      addToast?.({ kind: 'error', text: msg });
    }
  }, [addToast, rpcEnabled, rpcUrl, servicesEnabled, servicesUrl, statsWindow]);

  // ------------------------------- Polling ----------------------------------

  // Initial loads
  useEffect(() => {
    void refreshProviders();
    void refreshJobs();
    void refreshSettlements();
    void refreshStats();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [servicesUrl, rpcUrl]);

  // Auto-refresh on new head for lightweight, eventually-consistent views
  const lastHead = useRef<number | null>(null);
  useEffect(() => {
    if (!autoRefreshOnHead) return;
    const h = head?.height ?? null;
    if (h == null) return;
    if (lastHead.current == null) {
      lastHead.current = h;
      return;
    }
    if (h > lastHead.current) {
      // Refresh stats & settlements on head (jobs cheap too)
      void refreshStats();
      void refreshSettlements();
      // Providers rarely change, but head tick is a reasonable trigger in explorer
      void refreshProviders();
      lastHead.current = h;
    }
  }, [autoRefreshOnHead, head?.height, refreshProviders, refreshSettlements, refreshStats]);

  // Poll pending jobs
  useEffect(() => {
    if (!pollPendingJobs) return;
    if (pollTimerRef.current) {
      clearInterval(pollTimerRef.current);
      pollTimerRef.current = null;
    }
    pollTimerRef.current = setInterval(() => {
      if (!jobs.length) return;
      const anyPending = jobs.some((j) => !isTerminal(j.status));
      if (anyPending) void refreshJobs();
    }, Math.max(1000, pollIntervalMs));
    return () => {
      if (pollTimerRef.current) {
        clearInterval(pollTimerRef.current);
        pollTimerRef.current = null;
      }
    };
  }, [jobs, pollPendingJobs, pollIntervalMs, refreshJobs]);

  // Manual tracker for a single job id
  const trackJobUntilTerminal = useCallback(async (id: string): Promise<JobRecord | null> => {
    if (!id) return null;
    const maxMs = 5 * 60 * 1000; // 5 minutes cap
    const start = Date.now();
    let latest: JobRecord | null = null;
    // eslint-disable-next-line no-constant-condition
    while (true) {
      const j = await getJob(id);
      if (j) latest = j;
      if (j && isTerminal(j.status)) break;
      if (Date.now() - start > maxMs) break;
      await new Promise((r) => setTimeout(r, Math.max(1000, pollIntervalMs)));
    }
    // Refresh list to keep consistent with detail
    void refreshJobs();
    return latest;
  }, [getJob, pollIntervalMs, refreshJobs]);

  // -------------------------------- Result ----------------------------------

  return {
    // Providers
    providersState,
    providers,
    refreshProviders,

    // Jobs
    jobsState,
    jobs,
    jobsTotal,
    jobsQuery,
    setJobsQuery,
    refreshJobs,
    getJob,

    // Settlements
    settlementsState,
    settlements,
    settlementsTotal,
    settlementsQuery,
    setSettlementsQuery,
    refreshSettlements,

    // SLA
    statsState,
    stats,
    statsWindow,
    setStatsWindow,
    refreshStats,

    // Misc
    error,
    trackJobUntilTerminal,
  };
}

// ------------------------------ Normalizers ---------------------------------

function normalizeJob(j: any): JobRecord {
  const obj: JobRecord = {
    id: String(j?.id ?? ''),
    kind: (j?.kind === 'quantum' ? 'quantum' : 'ai') as ProviderKind,
    requester: String(j?.requester ?? j?.address ?? ''),
    provider: String(j?.provider ?? ''),
    model: j?.model ? String(j.model) : undefined,
    promptHash: j?.promptHash ? String(j.promptHash) : undefined,
    circuitHash: j?.circuitHash ? String(j.circuitHash) : undefined,
    createdAtISO: String(j?.createdAtISO ?? j?.createdAt ?? new Date().toISOString()),
    updatedAtISO: j?.updatedAtISO ? String(j.updatedAtISO) : undefined,
    status: (['queued','running','settled','failed','canceled'].includes(String(j?.status)) ? String(j.status) : 'queued') as JobStatus,
    cost: j?.cost != null ? String(j.cost) : undefined,
    resultRef: j?.resultRef ?? null,
    receiptTx: j?.receiptTx ?? null,
    ...j,
  };
  return obj;
}

function normalizeSettlement(s: any): SettlementRecord {
  const obj: SettlementRecord = {
    id: String(s?.id ?? ''),
    provider: String(s?.provider ?? ''),
    jobId: String(s?.jobId ?? ''),
    amount: String(s?.amount ?? '0'),
    timestampISO: String(s?.timestampISO ?? s?.timestamp ?? new Date().toISOString()),
    txHash: String(s?.txHash ?? ''),
    blockHeight: Number.isFinite(s?.blockHeight) ? Number(s.blockHeight) : 0,
    status: (s?.status === 'pending' || s?.status === 'reverted') ? s.status : 'confirmed',
    ...s,
  };
  return obj;
}

function normalizeStats(windowSel: '24h'|'7d'|'30d', x: any): SLAStats {
  const providers = Array.isArray(x?.providers) ? x.providers : [];
  return {
    window: windowSel,
    totals: {
      jobs: Number.isFinite(x?.totals?.jobs) ? Number(x.totals.jobs) : Number(x?.jobs ?? 0),
      settled: Number.isFinite(x?.totals?.settled) ? Number(x.totals.settled) : Number(x?.settled ?? 0),
      failed: Number.isFinite(x?.totals?.failed) ? Number(x.totals.failed) : Number(x?.failed ?? 0),
    },
    latencyMs: {
      avg: Number.isFinite(x?.latencyMs?.avg) ? Number(x.latencyMs.avg) : Number(x?.avgLatencyMs ?? 0),
      p95: Number.isFinite(x?.latencyMs?.p95) ? Number(x.latencyMs.p95) : Number(x?.p95LatencyMs ?? 0),
    },
    providers: providers.map((p: any) => ({
      id: String(p?.id ?? ''),
      successRate: Number.isFinite(p?.successRate) ? Number(p.successRate) : 0,
      avgLatencyMs: Number.isFinite(p?.avgLatencyMs) ? Number(p.avgLatencyMs) : 0,
      p95LatencyMs: Number.isFinite(p?.p95LatencyMs) ? Number(p.p95LatencyMs) : 0,
      settledJobs: Number.isFinite(p?.settledJobs) ? Number(p.settledJobs) : 0,
      failedJobs: Number.isFinite(p?.failedJobs) ? Number(p.failedJobs) : 0,
      uptimePct: Number.isFinite(p?.uptimePct) ? Number(p.uptimePct) : 0,
    })).filter((p: any) => p.id),
  };
}
