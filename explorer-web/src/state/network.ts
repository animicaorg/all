/**
 * Animica Explorer — Network state & lifecycle
 * -----------------------------------------------------------------------------
 * Responsibilities
 * - Hold RPC URL & chainId (backed by global store)
 * - Establish/teardown RPC connections
 * - Track latest head (height/hash/time)
 * - Measure/track latency (ms)
 * - Detect chainId mismatch
 *
 * Integrates with:
 *  - store.ts (useExplorerStore + actions)
 *  - services/rpc.ts (createRpc, type RpcClient)
 *
 * This module exposes:
 *  - useNetworkManager(): hook that wires everything up
 *  - setRpcUrl(url), setChainId(id): convenient setters
 *  - select helpers: selectNetwork(), selectHead(), selectLatency()
 */

import { useEffect, useMemo, useSyncExternalStore } from 'react';
import { useExplorerStore, selectors } from './store';
import type { ExplorerState } from './store';
import { shallow } from './store';
import type { ExplorerRpcClient } from '../services/rpc';
import { getRpcClient, releaseRpcClient } from '../services/rpc';
import { DEFAULT_RPC } from '../services/env';

// Thin client interface expected from ../services/rpc
// The explorer-web/services/rpc.ts should provide a compatible client.
export interface RpcHead {
  height: number;
  hash: string;
  timeISO: string;
}

// ------------------------- Simple selectors ---------------------------------

export const selectNetwork = (s: ExplorerState) => s.network;
export const selectHead = (s: ExplorerState) => s.head;

// ------------------------- Shared lifecycle state ---------------------------

export type NetworkStatus = 'disconnected' | 'connecting' | 'connected' | 'error';

type ManagerSnapshot = {
  status: NetworkStatus;
  latencyMs: number | null;
  error: string | null;
  rpcUrl?: string;
  expectedChainId?: string;
};

let snapshot: ManagerSnapshot = {
  status: 'disconnected',
  latencyMs: null,
  error: null,
};
const listeners = new Set<() => void>();

function emit(patch?: Partial<ManagerSnapshot>) {
  if (patch) {
    snapshot = { ...snapshot, ...patch };
  }
  listeners.forEach((fn) => fn());
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function getSnapshot() {
  return snapshot;
}

const runtime: {
  client: ExplorerRpcClient | null;
  stopFns: (() => void)[];
  bootKey: string;
  bootPromise: Promise<void> | null;
  abortController: AbortController | null;
} = {
  client: null,
  stopFns: [],
  bootKey: '',
  bootPromise: null,
  abortController: null,
};

let subscriberCount = 0;
let cleanupTimer: number | null = null;

const bindings: {
  setNetwork?: ExplorerState['setNetwork'];
  setHead?: ExplorerState['setHead'];
  addToast?: ExplorerState['addToast'];
} = {};

function isCorsLikeError(message: string) {
  const lower = message.toLowerCase();
  return lower.includes('cors') || lower.includes('cross-origin');
}

function inferSameOriginRpcUrl(currentRpc: string): string | null {
  if (typeof window === 'undefined') return null;

  try {
    const resolved = new URL(currentRpc, window.location.href);
    if (resolved.origin === window.location.origin) return null; // already same-origin

    const safePath = DEFAULT_RPC.startsWith('/') ? DEFAULT_RPC : `/${DEFAULT_RPC}`;
    return `${window.location.origin}${safePath}`;
  } catch {
    return null;
  }
}

// ------------------------- Public setters -----------------------------------

export function setRpcUrl(url: string) {
  console.warn('[network] setRpcUrl should be called from within a React component using useExplorerStore');
}

export function setChainId(id: string) {
  console.warn('[network] setChainId should be called from within a React component using useExplorerStore');
}

function retainSubscriber() {
  subscriberCount += 1;
  if (cleanupTimer) {
    clearTimeout(cleanupTimer);
    cleanupTimer = null;
  }
}

function releaseSubscriber() {
  subscriberCount = Math.max(0, subscriberCount - 1);
  if (subscriberCount === 0 && typeof window !== 'undefined') {
    cleanupTimer = window.setTimeout(() => {
      cleanupTimer = null;
      cleanup();
    }, 60);
  }
}

function cleanup() {
  runtime.abortController?.abort();
  runtime.abortController = null;

  runtime.stopFns.splice(0).forEach((fn) => {
    try {
      fn();
    } catch {
      /* ignore */
    }
  });

  if (runtime.client && typeof runtime.client.close === 'function') {
    try {
      runtime.client.close();
    } catch {
      /* ignore */
    }
  }
  runtime.client = null;
  runtime.bootPromise = null;
  runtime.bootKey = '';
  if (snapshot.rpcUrl) {
    releaseRpcClient(snapshot.rpcUrl);
  }

  emit({ status: 'disconnected', latencyMs: null, error: null });
  bindings.setNetwork?.({ connected: false, status: 'disconnected', latencyMs: null, error: null });
}

function setLatency(latencyMs: number | null) {
  emit({ latencyMs });
  bindings.setNetwork?.({ latencyMs });
}

function setStatus(status: NetworkStatus, error: string | null = null) {
  emit({ status, error });
  const connected = status === 'connected';
  bindings.setNetwork?.({ status, error, connected });
}

function ensureBoot(params: {
  rpcUrl?: string;
  expectedChainId?: string;
  pollIntervalMs: number;
  pingIntervalMs: number;
  enforceChainId: boolean;
}) {
  const { rpcUrl, expectedChainId } = params;
  const key = `${rpcUrl || ''}|${expectedChainId || ''}`;

  if (!rpcUrl) {
    setStatus('disconnected', 'No RPC URL configured');
    bindings.setNetwork?.({ connected: false });
    return;
  }

  if (runtime.bootPromise && runtime.bootKey === key) {
    return runtime.bootPromise;
  }

  runtime.abortController?.abort();
  cleanup();

  const controller = new AbortController();
  runtime.abortController = controller;
  runtime.bootKey = key;

  snapshot = { ...snapshot, rpcUrl, expectedChainId };
  emit();

  const promise = boot({ ...params, rpcUrl, expectedChainId, signal: controller.signal, triedCorsFallback: false });
  runtime.bootPromise = promise;
  return promise;
}

async function boot(params: {
  rpcUrl: string;
  expectedChainId?: string;
  pollIntervalMs: number;
  pingIntervalMs: number;
  enforceChainId: boolean;
  signal: AbortSignal;
  triedCorsFallback?: boolean;
}) {
  const { rpcUrl, expectedChainId, pollIntervalMs, pingIntervalMs, enforceChainId, signal, triedCorsFallback } = params;
  if (signal.aborted) return;

  setStatus('connecting', null);
  bindings.setNetwork?.({ rpcUrl, chainId: expectedChainId, connected: false });

  try {
    console.log('[network] Connecting to RPC:', rpcUrl);
    const client = await getRpcClient(rpcUrl);
    if (signal.aborted) return;
    runtime.client = client;
    console.log('[network] RPC client created successfully');

    let actualChainId = '';
    try {
      console.log('[network] Fetching chain ID...');
      actualChainId = await client.getChainId();
      if (signal.aborted) return;
      console.log('[network] Chain ID:', actualChainId);
    } catch (e: any) {
      console.warn('[network] Failed to fetch chain ID:', e);
      if (enforceChainId && expectedChainId) {
        const errorMsg = e?.message || String(e);
        const detailedMsg = `Failed to fetch chain ID from ${rpcUrl}: ${errorMsg}`;

        setStatus('error', detailedMsg);
        bindings.setNetwork?.({ connected: false });
        bindings.addToast?.({
          kind: 'error',
          text: `${detailedMsg}\n\n💡 Check:\n• RPC server is reachable\n• Use the built-in /rpc proxy or enable CORS\n• Verify the endpoint path`,
          ttl: 12000,
        });
        return;
      }
      actualChainId = expectedChainId || '';
    }

    if (enforceChainId && expectedChainId && actualChainId && expectedChainId !== actualChainId) {
      const msg = `Chain ID mismatch: expected ${expectedChainId}, got ${actualChainId}`;
      console.error('[network]', msg);
      setStatus('error', msg);
      bindings.setNetwork?.({ connected: false });
      bindings.addToast?.({
        kind: 'error',
        text: `${msg}\n\n💡 Update VITE_CHAIN_ID in your .env to match the node's chain ID`,
        ttl: 10000,
      });
      return;
    }

    try {
      console.log('[network] Fetching initial head...');
      const head = await client.getHead();
      if (!signal.aborted) {
        console.log('[network] Initial head:', head);
        bindings.setHead?.(head);
      }
    } catch (e: any) {
      console.warn('[network] Failed to fetch initial head:', e);
    }

    if (signal.aborted) return;

    if (typeof client.subscribeNewHeads === 'function') {
      try {
        const sub = client.subscribeNewHeads!((h) => {
          if (signal.aborted) return;
          bindings.setHead?.(h);
        });
        runtime.stopFns.push(() => {
          try {
            sub.unsubscribe();
          } catch (e) {
            console.debug('[network] Error during unsubscribe:', e);
          }
        });
        console.log('[network] WebSocket subscription established');
      } catch (e: any) {
        console.warn('[network] Failed to establish WebSocket subscription, falling back to polling:', e);
        const pollId = window.setInterval(async () => {
          if (signal.aborted) return;
          try {
            const h = await client.getHead();
            if (!signal.aborted) {
              bindings.setHead?.(h);
            }
          } catch (pollError) {
            console.debug('[network] Head poll error:', pollError);
          }
        }, pollIntervalMs);
        runtime.stopFns.push(() => window.clearInterval(pollId));
      }
    } else {
      console.log('[network] No WebSocket support, using HTTP polling');
      const pollId = window.setInterval(async () => {
        if (signal.aborted) return;
        try {
          const h = await client.getHead();
          if (!signal.aborted) {
            bindings.setHead?.(h);
          }
        } catch (pollError) {
          console.debug('[network] Head poll error:', pollError);
        }
      }, pollIntervalMs);
      runtime.stopFns.push(() => window.clearInterval(pollId));
    }

    const pingTick = async () => {
      if (signal.aborted) return;
      try {
        const start = performance.now();
        if (client.ping) {
          await client.ping();
        } else {
          await fetch(rpcUrl, {
            method: 'POST',
            headers: { 'content-type': 'application/json' },
            body: JSON.stringify({
              jsonrpc: '2.0',
              id: 1,
              method: 'animica_ping',
              params: [],
            }),
            keepalive: false,
            signal,
          }).catch(() => undefined);
        }
        const ms = Math.max(0, Math.round(performance.now() - start));
        if (!signal.aborted) {
          setLatency(ms);
        }
      } catch {
        if (!signal.aborted) {
          setLatency(null);
        }
      }
    };
    pingTick().catch(() => {/* ignore */});
    const pingId = window.setInterval(pingTick, pingIntervalMs);
    runtime.stopFns.push(() => window.clearInterval(pingId));

    setStatus('connected', null);
    bindings.setNetwork?.({ connected: true });
    console.log('[network] Connection established successfully');
  } catch (e: any) {
    if (signal.aborted) return;

    const errorName = e?.name || 'Error';
    const errorMsg = e?.message || String(e);

    const sameOriginRpc = inferSameOriginRpcUrl(rpcUrl);
    const isCorsError = isCorsLikeError(errorMsg);
    if (isCorsError && !triedCorsFallback && sameOriginRpc) {
      console.warn(`[network] CORS detected for ${rpcUrl}; retrying via same-origin proxy ${sameOriginRpc}`);

      releaseRpcClient(rpcUrl);
      runtime.client = null;

      runtime.bootKey = `${sameOriginRpc}|${expectedChainId || ''}`;
      snapshot = { ...snapshot, rpcUrl: sameOriginRpc, status: 'connecting', error: null };
      emit();
      bindings.setNetwork?.({ rpcUrl: sameOriginRpc, status: 'connecting', error: null, connected: false });

      const nextPromise = boot({
        rpcUrl: sameOriginRpc,
        expectedChainId,
        pollIntervalMs,
        pingIntervalMs,
        enforceChainId,
        signal,
        triedCorsFallback: true,
      });
      runtime.bootPromise = nextPromise;
      return nextPromise;
    }

    let userMessage = 'Failed to connect to RPC server';
    let troubleshooting = '';

    if (isCorsError) {
      userMessage = 'RPC blocked by CORS. Use the built-in /rpc proxy or enable CORS on the RPC server.';
      troubleshooting = '\n\n💡 The RPC server must allow this origin or be accessed via a same-origin proxy.';
    } else if (errorName === 'NetworkError' || errorMsg.includes('fetch failed') || errorMsg.toLowerCase().includes('network')) {
      userMessage = `Network error: Unable to reach RPC server at ${rpcUrl}`;
      troubleshooting = '\n\n💡 Troubleshooting:\n• Check that the RPC server is running\n• Verify the URL is correct\n• Ensure your internet connection is stable\n• Check firewall settings';
    } else if (errorMsg.includes('timeout') || errorMsg.includes('timed out')) {
      userMessage = `Timeout: RPC server at ${rpcUrl} is not responding`;
      troubleshooting = '\n\n💡 The server may be slow or overloaded. Try again in a few moments.';
    } else {
      userMessage = `Connection failed: ${errorMsg}`;
      troubleshooting = `\n\nRPC URL: ${rpcUrl}\nChain ID: ${expectedChainId || 'not configured'}`;
    }

    console.error('[network] Connection error:', e);
    console.error('[network] RPC URL:', rpcUrl);
    console.error('[network] Expected Chain ID:', expectedChainId);
    console.error('[network] Error details:', { name: errorName, message: errorMsg, stack: e?.stack });

    setStatus('error', userMessage);
    bindings.setNetwork?.({ connected: false });
    bindings.addToast?.({ kind: 'error', text: userMessage + troubleshooting, ttl: 12000 });
  }
}

// ------------------------- Hook: Network Manager ----------------------------

export function useNetworkManager(opts?: {
  pollIntervalMs?: number;  // head poll fallback when WS absent
  pingIntervalMs?: number;  // latency sampling interval
  enforceChainId?: boolean; // default true — mismatch -> error
}) {
  const {
    pollIntervalMs = 4000,
    pingIntervalMs = 15000,
    enforceChainId = true,
  } = opts ?? {};

  const { network, setNetwork, setHead, addToast } = useExplorerStore(
    (s) => ({
      network: s.network,
      setNetwork: s.setNetwork,
      setHead: s.setHead,
      addToast: s.addToast,
    }),
    shallow
  );

  bindings.setNetwork = setNetwork;
  bindings.setHead = setHead;
  bindings.addToast = addToast;

  // Override the module-level convenience setters so callers can import & use them
  (setRpcUrl as unknown as (url: string) => void) = (url: string) => setNetwork({ rpcUrl: url });
  (setChainId as unknown as (id: string) => void) = (id: string) => setNetwork({ chainId: id });

  const rpcUrl = network.rpcUrl?.trim();
  const expectedChainId = network.chainId?.trim();

  const initKey = useMemo(
    () => `${rpcUrl || ''}|${expectedChainId || ''}|${pollIntervalMs}|${pingIntervalMs}|${enforceChainId}`,
    [rpcUrl, expectedChainId, pollIntervalMs, pingIntervalMs, enforceChainId]
  );

  useEffect(() => {
    retainSubscriber();
    ensureBoot({ rpcUrl, expectedChainId, pollIntervalMs, pingIntervalMs, enforceChainId });

    return () => {
      releaseSubscriber();
    };
  }, [initKey]);

  const state = useSyncExternalStore(subscribe, getSnapshot, getSnapshot);

  return {
    ...state,
    rpcUrl,
    expectedChainId,
  };
}

// ------------------------- Convenience state readers ------------------------

export function useNetworkInfo() {
  const network = useExplorerStore(selectors.network);
  const head = useExplorerStore(selectors.head);
  const { status, latencyMs, error } = useNetworkManager();
  return {
    ...network,
    head,
    status,
    latencyMs,
    error,
  };
}

// Optional latency is local to this hook, but we expose a lightweight hook/selector pair.
export function useLatency(): number | null {
  return useNetworkManager().latencyMs;
}
