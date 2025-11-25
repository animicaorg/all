/**
 * Animica Explorer — Contracts: verified source / artifacts link state
 * -----------------------------------------------------------------------------
 * This module keeps a small cache mapping contract addresses to their current
 * verification status (as provided by studio-services) and any linked
 * artifacts (manifest, ABI, source, bytecode, etc.).
 *
 * It integrates with:
 *  - state/store.ts       (useExplorerStore: servicesUrl, toasts)
 *  - .env / network.ts    (VITE_SERVICES_URL optional)
 *
 * If servicesUrl is not configured, the hook still works but returns
 * "unknown" verification with empty artifacts.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { shallow } from 'zustand/shallow';
import { useExplorerStore } from './store';

// ----------------------------- Types ----------------------------------------

export type VerifyState =
  | 'unknown'     // services disabled or not queried yet
  | 'unverified'  // no matching verify record
  | 'pending'     // a verify job is in progress
  | 'verified'    // code hash matched; artifacts linked
  | 'mismatch'    // compiled hash != on-chain
  | 'failed';     // verify pipeline errored

export interface ArtifactMeta {
  id: string;
  kind?: 'abi' | 'manifest' | 'bytecode' | 'source' | 'other';
  contentType?: string;
  size?: number;                 // bytes
  sha256?: string;               // hex (0x...)
  createdAtISO?: string;
  url?: string;                  // resolved from servicesUrl + /artifacts/{id}
  // Extra fields tolerated from server:
  [k: string]: unknown;
}

export interface VerificationRecord {
  address: string;
  status: VerifyState;
  codeHash?: string | null;      // hex (0x..)
  verifiedAtISO?: string | null;
  artifacts?: ArtifactMeta[];    // convenience: merged with /address/{addr}/artifacts
  // Pass-through for UI richness:
  result?: Record<string, unknown> | null;
  message?: string | null;
}

export type ContractsStatus = 'idle' | 'loading' | 'ready' | 'error';

export interface UseContractsOptions {
  pollPending?: boolean;     // default true
  pollIntervalMs?: number;   // default 5000
  autoRefreshOnHead?: boolean; // default true (re-check when new head arrives)
}

export interface UseContractsResult {
  status: ContractsStatus;
  error: string | null;

  verification: VerificationRecord | null;
  artifacts: ArtifactMeta[];

  refreshNow: () => Promise<void>;
  isVerified: boolean;

  linkForArtifact: (a: ArtifactMeta) => string | null;
}

// ----------------------------- Cache ----------------------------------------

interface CacheEntry {
  verification: VerificationRecord | null;
  artifacts: ArtifactMeta[];
  lastFetchedAt: number; // ms since epoch
}

class ContractsCache {
  private map = new Map<string, CacheEntry>();

  get(addr: string): CacheEntry {
    const k = addr.toLowerCase();
    let v = this.map.get(k);
    if (!v) {
      v = { verification: null, artifacts: [], lastFetchedAt: 0 };
      this.map.set(k, v);
    }
    return v;
  }
  set(addr: string, entry: CacheEntry) {
    this.map.set(addr.toLowerCase(), entry);
  }
  clear(addr?: string) {
    if (!addr) this.map.clear();
    else this.map.delete(addr.toLowerCase());
  }
}

// ----------------------------- HTTP helpers ---------------------------------

function joinUrl(base: string, path: string): string {
  if (!base) return path;
  const b = base.endsWith('/') ? base.slice(0, -1) : base;
  const p = path.startsWith('/') ? path : `/${path}`;
  return `${b}${p}`;
}

async function httpGet<T>(url: string): Promise<T> {
  const res = await fetch(url, {
    method: 'GET',
    headers: {
      'accept': 'application/json',
    },
  });
  if (!res.ok) {
    const txt = await res.text().catch(() => '');
    throw new Error(`GET ${url} -> ${res.status}: ${txt || res.statusText}`);
  }
  return (await res.json()) as T;
}

// ----------------------------- Hook -----------------------------------------

export function useContractVerification(
  address: string | undefined,
  opts?: UseContractsOptions
): UseContractsResult {
  const {
    pollPending = true,
    pollIntervalMs = 5000,
    autoRefreshOnHead = true,
  } = opts ?? {};

  const addr = useMemo(
    () => (address ?? '').trim().toLowerCase(),
    [address]
  );

  const { servicesUrl, head, addToast } = useExplorerStore(
    (s) => ({
      servicesUrl: (s as any).network?.servicesUrl as string | undefined,
      head: (s as any).head as { height: number } | null,
      addToast: (s as any).addToast as (t: {
        kind: 'info' | 'error' | 'success';
        text: string;
      }) => void,
    }),
    shallow
  );

  const [status, setStatus] = useState<ContractsStatus>('idle');
  const [error, setError] = useState<string | null>(null);
  const [verification, setVerification] = useState<VerificationRecord | null>(
    null
  );
  const [artifacts, setArtifacts] = useState<ArtifactMeta[]>([]);

  const runtime = useRef<{
    cache: ContractsCache;
    pollTimer: ReturnType<typeof setInterval> | null;
    inflightVerify: Map<string, Promise<VerificationRecord>>;
    inflightArtifacts: Map<string, Promise<ArtifactMeta[]>>;
  }>({
    cache: new ContractsCache(),
    pollTimer: null,
    inflightVerify: new Map(),
    inflightArtifacts: new Map(),
  });

  const servicesEnabled = !!servicesUrl;

  // Reset on address or servicesUrl change
  useEffect(() => {
    const rt = runtime.current;
    if (rt.pollTimer) {
      clearInterval(rt.pollTimer);
      rt.pollTimer = null;
    }
    setStatus('idle');
    setError(null);
    setVerification(null);
    setArtifacts([]);
    if (!addr) return;
    void refreshNow();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [addr, servicesUrl]);

  // Auto-refresh on new head (lightweight re-check)
  const lastHead = useRef<number | null>(null);
  useEffect(() => {
    if (!autoRefreshOnHead) return;
    if (!addr) return;
    if (!servicesEnabled) return;
    const h = head?.height ?? null;
    if (h == null) return;
    if (lastHead.current == null) {
      lastHead.current = h;
      return;
    }
    if (h > lastHead.current) {
      // Best-effort: just re-check verification state (cheap GET)
      void refreshVerify();
      lastHead.current = h;
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [head?.height, autoRefreshOnHead, servicesEnabled, addr]);

  const linkForArtifact = useCallback(
    (a: ArtifactMeta) => {
      if (a.url) return a.url;
      if (!servicesEnabled || !a.id) return null;
      return joinUrl(servicesUrl!, `/artifacts/${encodeURIComponent(a.id)}`);
    },
    [servicesEnabled, servicesUrl]
  );

  const fetchVerify = useCallback(async (): Promise<VerificationRecord> => {
    if (!addr) throw new Error('No address provided');
    const key = addr;
    const rt = runtime.current;

    const existing = rt.inflightVerify.get(key);
    if (existing) return existing;

    const p = (async () => {
      if (!servicesEnabled) {
        return {
          address: addr,
          status: 'unknown' as VerifyState,
          codeHash: null,
          verifiedAtISO: null,
          artifacts: [],
        };
      }

      type VerifyResponse = {
        address?: string;
        status: VerifyState | string;
        result?: {
          codeHash?: string | null;
          verifiedAtISO?: string | null;
          artifactIds?: string[] | null;
          [k: string]: unknown;
        } | null;
        message?: string | null;
        [k: string]: unknown;
      };

      const url = joinUrl(servicesUrl!, `/verify/${encodeURIComponent(addr)}`);
      const res = await httpGet<VerifyResponse>(url).catch((e) => {
        // If 404, consider unverified; otherwise bubble up
        const msg = String(e?.message || e);
        if (/404/.test(msg)) {
          return {
            address: addr,
            status: 'unverified' as VerifyState,
            result: null,
            message: null,
          } as VerifyResponse;
        }
        throw e;
      });

      const rec: VerificationRecord = {
        address: res.address || addr,
        status: (['unknown','unverified','pending','verified','mismatch','failed'].includes(String(res.status))
          ? (res.status as VerifyState)
          : 'unknown'
        ),
        codeHash: res.result?.codeHash ?? null,
        verifiedAtISO: res.result?.verifiedAtISO ?? null,
        result: res.result ?? null,
        message: res.message ?? null,
        artifacts: [], // filled by artifacts fetch
      };
      return rec;
    })();

    rt.inflightVerify.set(key, p);
    try {
      return await p;
    } finally {
      rt.inflightVerify.delete(key);
    }
  }, [addr, servicesEnabled, servicesUrl]);

  const fetchArtifacts = useCallback(async (): Promise<ArtifactMeta[]> => {
    if (!addr) throw new Error('No address provided');
    const key = addr;
    const rt = runtime.current;

    const existing = rt.inflightArtifacts.get(key);
    if (existing) return existing;

    const p = (async () => {
      if (!servicesEnabled) return [];
      type ListResp = { items?: any[] } | any[];
      const url = joinUrl(
        servicesUrl!,
        `/address/${encodeURIComponent(addr)}/artifacts`
      );
      const data = await httpGet<ListResp>(url);
      const items = Array.isArray(data) ? data : (data?.items ?? []);
      const metas: ArtifactMeta[] = items.map((x: any) => ({
        id: String(x?.id ?? x?.artifactId ?? ''),
        kind: x?.kind,
        contentType: x?.contentType,
        size: typeof x?.size === 'number' ? x.size : undefined,
        sha256: x?.sha256,
        createdAtISO: x?.createdAtISO ?? x?.createdAt ?? undefined,
      })).filter(a => a.id);
      // enrich with URLs
      for (const m of metas) {
        m.url = linkForArtifact(m) ?? undefined;
      }
      return metas;
    })();

    rt.inflightArtifacts.set(key, p);
    try {
      return await p;
    } finally {
      rt.inflightArtifacts.delete(key);
    }
  }, [addr, linkForArtifact, servicesEnabled, servicesUrl]);

  const refreshVerify = useCallback(async () => {
    if (!addr) return;
    setStatus('loading');
    setError(null);
    try {
      const [rec, arts] = await Promise.all([fetchVerify(), fetchArtifacts()]);
      // Merge artifacts into record (dedupe by id)
      const seen = new Set<string>();
      const mergedArts: ArtifactMeta[] = [];
      for (const a of [...(rec.artifacts ?? []), ...arts]) {
        if (!a?.id) continue;
        const k = a.id;
        if (seen.has(k)) continue;
        seen.add(k);
        mergedArts.push({
          ...a,
          url: linkForArtifact(a) ?? undefined,
        });
      }
      const withArts: VerificationRecord = { ...rec, artifacts: mergedArts };

      runtime.current.cache.set(addr, {
        verification: withArts,
        artifacts: mergedArts,
        lastFetchedAt: Date.now(),
      });

      setVerification(withArts);
      setArtifacts(mergedArts);
      setStatus('ready');
    } catch (e: any) {
      const msg = `[contracts] verify refresh failed: ${e?.message || String(e)}`;
      setStatus('error');
      setError(msg);
      addToast?.({ kind: 'error', text: msg });
    }
  }, [addr, addToast, fetchArtifacts, fetchVerify, linkForArtifact]);

  const refreshNow = useCallback(async () => {
    // Force re-fetch and bypass cache
    if (!addr) return;
    runtime.current.cache.clear(addr);
    await refreshVerify();
  }, [addr, refreshVerify]);

  // Initial fetch
  useEffect(() => {
    if (!addr) return;
    void refreshVerify();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [addr]);

  // Poll while pending
  useEffect(() => {
    if (!pollPending) return;
    if (!addr) return;

    // manage interval
    const rt = runtime.current;
    if (rt.pollTimer) {
      clearInterval(rt.pollTimer);
      rt.pollTimer = null;
    }
    // Small runner that checks current state and refetches if pending
    rt.pollTimer = setInterval(() => {
      const entry = rt.cache.get(addr);
      const isPending = entry.verification?.status === 'pending';
      if (!isPending) return;
      void refreshVerify();
    }, Math.max(1000, pollIntervalMs));

    return () => {
      if (rt.pollTimer) {
        clearInterval(rt.pollTimer);
        rt.pollTimer = null;
      }
    };
  }, [addr, pollPending, pollIntervalMs, refreshVerify]);

  const isVerified = verification?.status === 'verified';

  return {
    status,
    error,
    verification,
    artifacts,
    refreshNow,
    isVerified,
    linkForArtifact,
  };
}
