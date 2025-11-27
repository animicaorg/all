/**
 * Environment helpers for discovering RPC/WS endpoints when the explorer is
 * packaged alongside a node. We try, in order:
 *   1. Vite env vars (VITE_RPC_URL / VITE_RPC_WS / VITE_WS_URL / VITE_CHAIN_ID)
 *   2. Window globals injected by the hosting node
 *   3. Same-origin inference (use the page origin)
 *   4. Local defaults (127.0.0.1)
 */

export type EnvLike = {
  VITE_RPC_URL?: string;
  VITE_RPC_WS?: string;
  VITE_WS_URL?: string;
  VITE_CHAIN_ID?: string | number;
};

const DEFAULT_RPC = "http://127.0.0.1:8545";
const DEFAULT_WS = "ws://127.0.0.1:8546";

function resolveEnv(env?: Partial<EnvLike>): Partial<EnvLike> {
  if (env) return env;
  try {
    return (import.meta as any).env ?? {};
  } catch {
    return {};
  }
}

/** Infer an RPC HTTP URL with sensible fallbacks. */
export function inferRpcUrl(env?: Partial<EnvLike>): string {
  const e = resolveEnv(env);
  if (e?.VITE_RPC_URL) return e.VITE_RPC_URL;

  if (typeof window !== "undefined") {
    const anyWin = window as any;
    const injected =
      anyWin.__ANIMICA_RPC_URL__ ??
      anyWin.__ANIMICA_RPC_HTTP__ ??
      anyWin.__ANIMICA_HTTP_URL__;
    if (typeof injected === "string" && injected.length > 0) return injected;

    try {
      return new URL(window.location.origin).toString();
    } catch {
      /* noop */
    }
  }

  return DEFAULT_RPC;
}

/** Infer a WS URL from env/globals or by converting the RPC HTTP base. */
export function inferWsUrl(env?: Partial<EnvLike>): string {
  const e = resolveEnv(env);
  const ws = e?.VITE_RPC_WS ?? e?.VITE_WS_URL;
  if (ws) return ws;

  if (typeof window !== "undefined") {
    const anyWin = window as any;
    const injected =
      anyWin.__ANIMICA_WS_URL__ ??
      anyWin.__ANIMICA_RPC_WS__ ??
      anyWin.__ANIMICA_WS__;
    if (typeof injected === "string" && injected.length > 0) return injected;
  }

  const rpc = inferRpcUrl(e);
  try {
    const u = new URL(rpc);
    // Heuristic: JSON-RPC WS commonly lives on the next port. If we see the
    // default 8545, prefer 8546 rather than flipping protocol only.
    if (u.port === "8545") {
      u.port = "8546";
    }
    u.protocol = u.protocol === "https:" ? "wss:" : "ws:";
    return u.toString();
  } catch {
    return DEFAULT_WS;
  }
}

/**
 * Infer the chain id from env or window injection. Returns an empty string
 * when unavailable to keep existing UI defaults.
 */
export function inferChainId(env?: Partial<EnvLike>): string {
  const e = resolveEnv(env);
  if (e?.VITE_CHAIN_ID) return String(e.VITE_CHAIN_ID);

  if (typeof window !== "undefined") {
    const anyWin = window as any;
    const injected = anyWin.__ANIMICA_CHAIN_ID__ ?? anyWin.__CHAIN_ID__;
    if (injected !== undefined && injected !== null) return String(injected);
  }

  return "";
}

export { DEFAULT_RPC, DEFAULT_WS };
