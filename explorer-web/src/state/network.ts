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

import { useEffect, useMemo, useRef, useState } from 'react';
import { useExplorerStore, selectors } from './store';
import type { ExplorerState } from './store';
import { shallow } from './store';

// Thin client interface expected from ../services/rpc
// The explorer-web/services/rpc.ts should provide a compatible client.
export interface RpcHead {
  height: number;
  hash: string;
  timeISO: string;
}
export interface RpcClient {
  getChainId(): Promise<string>;
  getHead(): Promise<RpcHead>;
  subscribeNewHeads?(onHead: (h: RpcHead) => void): { unsubscribe: () => void };
  ping?(): Promise<void>; // optional; we'll fallback to a JSON-RPC ping via fetch if absent
  close?(): void;
}
// Factory — implemented in explorer-web/src/services/rpc.ts
// eslint-disable-next-line @typescript-eslint/consistent-type-imports
import type { createRpc as CreateRpcFn } from '../services/rpc';
// Dynamic import to avoid hard-coupling during build-time tree-shaking.
let _createRpcAsync: Promise<CreateRpcFn> | null = null;
async function createRpc(rpcUrl: string): Promise<RpcClient> {
  if (!_createRpcAsync) {
    _createRpcAsync = import('../services/rpc').then(m => m.createRpc as CreateRpcFn);
  }
  const fn = await _createRpcAsync;
  console.log('[network] Creating RPC client with URL:', rpcUrl);
  return fn({ url: rpcUrl }) as unknown as RpcClient;
}

// ------------------------- Simple selectors ---------------------------------

export const selectNetwork = (s: ExplorerState) => s.network;
export const selectHead = (s: ExplorerState) => s.head;

// Optional latency is local to this hook, but we expose a lightweight hook/selector pair.
export function useLatency(): number | null {
  return useNetworkManager().latencyMs;
}

// ------------------------- Public setters -----------------------------------

export function setRpcUrl(url: string) {
  // Safe direct store access via the provider hook
  // (components typically call useExplorerStore, but utility setters are handy)
  // We do it via a one-off trick: a temporary subscription cycle isn't needed; just
  // use window.__ANIMICA_EXPLORER_STORE if you decide to expose it. For now,
  // rely on a component-level call path using useExplorerStore in callers.
  // This function is kept for API symmetry and can be used inside components:
  //   const setUrl = () => setRpcUrl(inputUrl)
  // At runtime within React, this will be overridden by the hook-managed version.
  console.warn('[network] setRpcUrl should be called from within a React component using useExplorerStore');
}

export function setChainId(id: string) {
  console.warn('[network] setChainId should be called from within a React component using useExplorerStore');
}

// ------------------------- Hook: Network Manager ----------------------------

export type NetworkStatus = 'disconnected' | 'connecting' | 'connected' | 'error';

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

  // Bind into global store
  const { network, setNetwork, setHead, addToast } = useExplorerStore(
    (s) => ({
      network: s.network,
      setNetwork: s.setNetwork,
      setHead: s.setHead,
      addToast: s.addToast,
    }),
    shallow
  );

  // Override the module-level convenience setters so callers can import & use them
  (setRpcUrl as unknown as (url: string) => void) = (url: string) => setNetwork({ rpcUrl: url });
  (setChainId as unknown as (id: string) => void) = (id: string) => setNetwork({ chainId: id });

  const [status, setStatus] = useState<NetworkStatus>('disconnected');
  const [latencyMs, setLatencyMs] = useState<number | null>(null);
  const [errMsg, setErrMsg] = useState<string | null>(null);

  const runtime = useRef<{
    client: RpcClient | null;
    stopFns: (() => void)[];
  }>({ client: null, stopFns: [] });

  const rpcUrl = network.rpcUrl?.trim();
  const expectedChainId = network.chainId?.trim();

  // Memo key to re-init networking when URL or expected chainId changes
  const initKey = useMemo(() => `${rpcUrl || ''}|${expectedChainId || ''}`, [rpcUrl, expectedChainId]);

  useEffect(() => {
    let cancelled = false;

    async function boot() {
      cleanup(); // ensure clean start
      if (!rpcUrl) {
        setStatus('disconnected');
        setErrMsg('No RPC URL configured');
        return;
      }

      setStatus('connecting');
      setErrMsg(null);
      setNetwork({ connected: false });

      try {
        console.log('[network] Connecting to RPC:', rpcUrl);
        const client = await createRpc(rpcUrl);
        if (cancelled) return;

        runtime.current.client = client;
        console.log('[network] RPC client created successfully');

        // Resolve actual chainId
        let actualChainId: string;
        try {
          console.log('[network] Fetching chain ID...');
          actualChainId = await client.getChainId();
          console.log('[network] Chain ID:', actualChainId);
        } catch (e: any) {
          console.warn('[network] Failed to fetch chain ID:', e);
          
          // Provide detailed error message
          const errorMsg = e?.message || String(e);
          const detailedMsg = `Failed to fetch chain ID from ${rpcUrl}: ${errorMsg}`;
          
          // If we can't get chain ID, this is a critical error
          if (enforceChainId && expectedChainId) {
            setStatus('error');
            setErrMsg(detailedMsg);
            setNetwork({ connected: false });
            addToast({ 
              kind: 'error', 
              text: `${detailedMsg}\n\n💡 Check:\n• RPC server is running\n• CORS is enabled\n• Network connectivity`, 
              ttl: 10000 
            });
            return;
          }
          
          // Fallback if chain ID validation is not enforced
          actualChainId = expectedChainId || '';
        }

        if (enforceChainId && expectedChainId && actualChainId && expectedChainId !== actualChainId) {
          const msg = `Chain ID mismatch: expected ${expectedChainId}, got ${actualChainId}`;
          console.error('[network]', msg);
          setStatus('error');
          setErrMsg(msg);
          setNetwork({ connected: false });
          addToast({ 
            kind: 'error', 
            text: `${msg}\n\n💡 Update VITE_CHAIN_ID in your .env.local to match the node's chain ID`, 
            ttl: 10000 
          });
          // Do not proceed further
          return;
        }

        // Prime head
        try {
          console.log('[network] Fetching initial head...');
          const head = await client.getHead();
          if (!cancelled) {
            console.log('[network] Initial head:', head);
            setHead(head);
          }
        } catch (e: any) {
          console.warn('[network] Failed to fetch initial head:', e);
          // Non-fatal; we'll rely on subsequent updates
          // But log a warning for the user
          const errorMsg = e?.message || String(e);
          console.warn('[network] Could not fetch initial head:', errorMsg);
        }

        // WS subscribe if available, else poll
        if (typeof client.subscribeNewHeads === 'function') {
          try {
            const sub = client.subscribeNewHeads!((h) => {
              if (!cancelled) {
                setHead(h);
              }
            });
            runtime.current.stopFns.push(() => {
              try {
                sub.unsubscribe();
              } catch (e) {
                console.debug('[network] Error during unsubscribe:', e);
              }
            });
            console.log('[network] WebSocket subscription established');
          } catch (e: any) {
            console.warn('[network] Failed to establish WebSocket subscription, falling back to polling:', e);
            // Fall back to polling
            const pollId = window.setInterval(async () => {
              if (cancelled) return;
              try {
                const h = await client.getHead();
                if (!cancelled) {
                  setHead(h);
                }
              } catch (pollError) {
                console.debug('[network] Head poll error:', pollError);
              }
            }, pollIntervalMs);
            runtime.current.stopFns.push(() => window.clearInterval(pollId));
          }
        } else {
          console.log('[network] No WebSocket support, using HTTP polling');
          const pollId = window.setInterval(async () => {
            if (cancelled) return;
            try {
              const h = await client.getHead();
              if (!cancelled) {
                setHead(h);
              }
            } catch (pollError) {
              console.debug('[network] Head poll error:', pollError);
            }
          }, pollIntervalMs);
          runtime.current.stopFns.push(() => window.clearInterval(pollId));
        }

        // Latency sampler
        const pingTick = async () => {
          if (cancelled) return;
          try {
            const start = performance.now();
            if (client.ping) {
              await client.ping();
            } else {
              // Fallback JSON-RPC "animica_ping" (servers may ignore; timing still useful)
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
              }).catch(() => undefined);
            }
            const ms = Math.max(0, Math.round(performance.now() - start));
            if (!cancelled) {
              setLatencyMs(ms);
            }
          } catch {
            if (!cancelled) {
              setLatencyMs(null);
            }
          }
        };
        // Prime & schedule
        pingTick().catch(() => {/* ignore */});
        const pingId = window.setInterval(pingTick, pingIntervalMs);
        runtime.current.stopFns.push(() => window.clearInterval(pingId));

        setStatus('connected');
        setNetwork({ connected: true });
        console.log('[network] Connection established successfully');
      } catch (e: any) {
        if (cancelled) return;
        
        // Categorize and provide detailed error message
        const errorName = e?.name || 'Error';
        const errorMsg = e?.message || String(e);
        
        let userMessage = `Failed to connect to RPC server`;
        let troubleshooting = '';
        
        if (errorName === 'NetworkError' || errorMsg.includes('fetch failed') || errorMsg.includes('network')) {
          userMessage = `Network error: Unable to reach RPC server at ${rpcUrl}`;
          troubleshooting = '\n\n💡 Troubleshooting:\n• Check that the RPC server is running\n• Verify the URL is correct\n• Ensure your internet connection is stable\n• Check firewall settings';
        } else if (errorMsg.includes('CORS')) {
          userMessage = `CORS error: RPC server at ${rpcUrl} is blocking this origin`;
          troubleshooting = '\n\n💡 The RPC server needs to allow requests from this origin.\nCheck the server\'s CORS configuration.';
        } else if (errorMsg.includes('timeout') || errorMsg.includes('timed out')) {
          userMessage = `Timeout: RPC server at ${rpcUrl} is not responding`;
          troubleshooting = '\n\n💡 The server may be slow or overloaded.\nTry again in a few moments.';
        } else {
          userMessage = `Connection failed: ${errorMsg}`;
          troubleshooting = `\n\nRPC URL: ${rpcUrl}\nChain ID: ${expectedChainId || 'not configured'}`;
        }
        
        console.error('[network] Connection error:', e);
        console.error('[network] RPC URL:', rpcUrl);
        console.error('[network] Expected Chain ID:', expectedChainId);
        console.error('[network] Error details:', { name: errorName, message: errorMsg, stack: e?.stack });
        
        setStatus('error');
        setErrMsg(userMessage);
        setNetwork({ connected: false });
        addToast({ kind: 'error', text: userMessage + troubleshooting, ttl: 12000 });
      }
    }

    boot().catch((e) => {
      if (!cancelled) {
        console.error('[network] Unexpected error in boot():', e);
        setStatus('error');
        setErrMsg(`Unexpected error: ${e?.message || String(e)}`);
      }
    });

    return () => {
      cancelled = true;
      cleanup();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initKey]);

  function cleanup() {
    const rt = runtime.current;
    // Stop timers/subscriptions
    rt.stopFns.splice(0).forEach((fn) => {
      try {
        fn();
      } catch {
        /* ignore */
      }
    });
    // Close client
    if (rt.client && typeof rt.client.close === 'function') {
      try {
        rt.client.close();
      } catch {
        /* ignore */
      }
    }
    rt.client = null;
    setLatencyMs(null);
    setStatus('disconnected');
    setNetwork({ connected: false });
  }

  return {
    status,
    latencyMs,
    error: errMsg,
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

