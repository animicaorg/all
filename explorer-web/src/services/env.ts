/**
 * Environment helpers for discovering RPC/WS endpoints when the explorer is
 * packaged alongside a node. We try, in order:
 *   1. Vite env vars (VITE_RPC_URL / VITE_RPC_WS / VITE_WS_URL / VITE_CHAIN_ID)
 *   2. Window globals injected by the hosting node
 *   3. Same-origin inference (use the page origin)
 *   4. Local defaults (127.0.0.1)
 */

import { PRIMARY_RPC_URL, resolveRpcUrl } from '../config/rpcUrl';

export type EnvLike = {
  VITE_RPC_URL?: string;
  VITE_RPC_HTTP?: string;  // Alternative name for RPC URL
  VITE_RPC_WS?: string;
  VITE_WS_URL?: string;
  VITE_CHAIN_ID?: string | number;
};

const DEFAULT_RPC = PRIMARY_RPC_URL;
const DEFAULT_WS = "ws://127.0.0.1:8546/ws";
const LEGACY_MAINNET_CHAIN_IDS = new Set(["659658", "659914", "0xa11ca"]);
const CANONICAL_MAINNET_CHAIN_ID = "1";

function normalizeChainIdValue(chainId: string | number): string {
  const raw = String(chainId).trim();
  let normalized = raw;

  // Normalize hex chain IDs to decimal strings
  if (raw.toLowerCase().startsWith("0x")) {
    try {
      const parsed = parseInt(raw, 16);
      if (!Number.isNaN(parsed)) {
        normalized = String(parsed);
      } else {
        return raw; // invalid hex, return as-is
      }
    } catch {
      return raw; // Return as-is if parsing fails
    }
  }

  // Migrate legacy prelaunch IDs to the canonical mainnet chain ID
  const rawLower = raw.toLowerCase();
  if (LEGACY_MAINNET_CHAIN_IDS.has(normalized) || LEGACY_MAINNET_CHAIN_IDS.has(rawLower)) {
    if (typeof console !== "undefined" && console?.warn) {
      console.warn(
        `[env] Legacy Animica mainnet chain ID detected (${raw}); normalizing to ${CANONICAL_MAINNET_CHAIN_ID}. ` +
          'Update VITE_CHAIN_ID to "1" to avoid this fallback.'
      );
    }
    return CANONICAL_MAINNET_CHAIN_ID;
  }

  return normalized;
}

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
  const envRpc = e?.VITE_RPC_URL ?? e?.VITE_RPC_HTTP;

  if (envRpc) return envRpc;

  if (typeof window !== "undefined") {
    return resolveRpcUrl(e);
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
    if (typeof window !== "undefined" && rpc?.startsWith?.("/")) {
      const base = window.location.origin.replace(/^http/, "ws");
      return `${base}${rpc}`;
    }
    return DEFAULT_WS;
  }
}

/**
 * Infer the chain id from env or window injection. Returns an empty string
 * when unavailable to keep existing UI defaults.
 * Normalizes hex chain IDs (0x...) to decimal strings for consistency.
 */
export function inferChainId(env?: Partial<EnvLike>): string {
  const e = resolveEnv(env);
  let chainId: string | number | undefined;

  if (e?.VITE_CHAIN_ID) {
    chainId = e.VITE_CHAIN_ID;
  } else if (typeof window !== "undefined") {
    const anyWin = window as any;
    chainId = anyWin.__ANIMICA_CHAIN_ID__ ?? anyWin.__CHAIN_ID__;
  }

  if (chainId === undefined || chainId === null) {
    return "";
  }

  return normalizeChainIdValue(chainId);
}

export { DEFAULT_RPC, DEFAULT_WS };
