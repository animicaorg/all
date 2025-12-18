/**
 * RPC URL resolution (single source of truth)
 * ------------------------------------------
 * Priority:
 *   1) Query param `rpc` (e.g., ?rpc=https://alt.rpc.example/rpc)
 *   2) import.meta.env.VITE_RPC_URL (or VITE_RPC_HTTP)
 *   3) Fallback to same-origin proxy path `/rpc` (dev + prod)
 */

type EnvShape = {
  VITE_RPC_URL?: string;
  VITE_RPC_HTTP?: string;
  PROD?: boolean;
};

const DEFAULT_RPC_PATH = '/rpc';
let cachedRpcUrl: string | null = null;
let loggedRpcUrl: string | null = null;

function readQueryRpc(): string | null {
  if (typeof window === 'undefined') return null;
  try {
    const params = new URLSearchParams(window.location.search);
    const raw = params.get('rpc');
    return raw ? raw.trim() : null;
  } catch {
    return null;
  }
}

function readEnvRpc(env: Partial<EnvShape> | undefined): string | null {
  if (!env) return null;
  return env.VITE_RPC_URL?.trim() ?? env.VITE_RPC_HTTP?.trim() ?? null;
}

function readWindowRpc(): string | null {
  if (typeof window === 'undefined') return null;
  const anyWin = window as any;
  const injected =
    anyWin.__ANIMICA_RPC_URL__ ??
    anyWin.__ANIMICA_RPC_HTTP__ ??
    anyWin.__ANIMICA_HTTP_URL__;
  if (typeof injected === 'string' && injected.trim().length > 0) {
    return injected.trim();
  }
  return null;
}

export function resolveRpcUrl(envOverride?: Partial<EnvShape>): string {
  if (cachedRpcUrl) return cachedRpcUrl;

  const metaEnv = (import.meta as any)?.env as Partial<EnvShape> | undefined;
  const envRpc = readEnvRpc(envOverride ?? metaEnv);
  const queryRpc = readQueryRpc();
  const injectedRpc = readWindowRpc();

  const resolved =
    queryRpc ||
    envRpc ||
    injectedRpc ||
    DEFAULT_RPC_PATH;

  cachedRpcUrl = resolved;

  if (typeof window !== 'undefined' && loggedRpcUrl !== resolved) {
    console.log('[config] RPC URL resolved:', resolved);
    loggedRpcUrl = resolved;
  }

  return resolved;
}

export function resetResolvedRpcUrl() {
  cachedRpcUrl = null;
}
