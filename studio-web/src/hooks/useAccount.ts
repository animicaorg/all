import { useCallback, useEffect, useMemo, useRef, useState } from "react";

/**
 * useAccount — connect/disconnect to the Animica wallet (window.animica),
 * manage permissions, and react to provider events.
 *
 * The provider is AIP-1193-like (EIP-1193 adjacent). We try a few standard
 * method names for broad compatibility:
 *  - animica_requestAccounts / requestAccounts / wallet_requestPermissions
 *  - wallet_getPermissions / wallet_revokePermissions
 *  - wallet_switchChain / animica_switchChain
 *
 * Returned API:
 *  - status: "unavailable" | "disconnected" | "connecting" | "connected" | "error"
 *  - account: connected address (bech32 or hex), or null
 *  - chainId: number | null
 *  - connect(): Promise<void>
 *  - disconnect(): Promise<void>
 *  - ensureChain(targetChainId: number): Promise<boolean>
 *  - hasAccountsPermission(): Promise<boolean>
 *  - error: string | null
 */

export type Account = {
  address: string;
  /** Optional algorithm id for PQ addresses if surfaced by the provider */
  algId?: number;
};

type Status = "unavailable" | "disconnected" | "connecting" | "connected" | "error";

export interface UseAccountOptions {
  /** If true, attempt to restore a previous session on mount (default true) */
  autoConnect?: boolean;
  /** Chain id the app expects; ensureChain will try to switch to this */
  expectedChainId?: number;
  /** Storage key for session persistence */
  storageKey?: string;
}

type RequestArgs = { method: string; params?: any[] | Record<string, any> };

export interface AnimicaProvider {
  isAnimica?: boolean;
  request<T = any>(args: RequestArgs): Promise<T>;
  on?(event: string, fn: (...args: any[]) => void): void;
  removeListener?(event: string, fn: (...args: any[]) => void): void;
  // Some providers implement enable() legacy
  enable?(): Promise<string[]>;
}

declare global {
  interface Window {
    animica?: AnimicaProvider;
    __ANIMICA_CHAIN_ID__?: number;
    __ANIMICA_RPC_URL__?: string;
  }
}

const SESSION_KEY_DEFAULT = "animica:studio:autoconnect";

function getProvider(): AnimicaProvider | undefined {
  if (typeof window === "undefined") return undefined;
  return window.animica;
}

async function tryRequestAccounts(provider: AnimicaProvider): Promise<string[] | undefined> {
  // Preferred
  try {
    const out = await provider.request<string[]>({ method: "animica_requestAccounts" });
    if (Array.isArray(out)) return out;
  } catch {}
  // EIP-1193-esque
  try {
    // Permissions API: request accounts permission
    const out = await provider.request<any>({
      method: "wallet_requestPermissions",
      params: [{ accounts: {} }],
    });
    // Some wallets return granted perms; follow with accounts.get
    try {
      const accs = await provider.request<string[]>({ method: "requestAccounts" });
      if (Array.isArray(accs)) return accs;
    } catch {}
    // Or animica_accounts
    try {
      const accs = await provider.request<string[]>({ method: "animica_accounts" });
      if (Array.isArray(accs)) return accs;
    } catch {}
    // Or result contains accounts
    if (Array.isArray(out?.accounts)) return out.accounts;
  } catch {}
  // Legacy enable()
  try {
    if (typeof provider.enable === "function") {
      const out = await provider.enable();
      if (Array.isArray(out)) return out;
    }
  } catch {}
  // Fallback simple
  try {
    const out = await provider.request<string[]>({ method: "requestAccounts" });
    if (Array.isArray(out)) return out;
  } catch {}
  return undefined;
}

async function getChainId(provider: AnimicaProvider): Promise<number | null> {
  // Prefer animica_chainId
  try {
    const id = await provider.request<number | string>({ method: "animica_chainId" });
    if (typeof id === "number") return id;
    if (typeof id === "string") return parseInt(id, 10);
  } catch {}
  // EVM-like methods sometimes return hex strings; try both parse styles
  try {
    const id = await provider.request<number | string>({ method: "eth_chainId" });
    if (typeof id === "number") return id;
    if (typeof id === "string") {
      if (id.startsWith("0x")) return parseInt(id, 16);
      return parseInt(id, 10);
    }
  } catch {}
  // Global injection fallback (dev tools)
  if (typeof window !== "undefined" && typeof window.__ANIMICA_CHAIN_ID__ === "number") {
    return window.__ANIMICA_CHAIN_ID__!;
  }
  return null;
}

async function switchChain(provider: AnimicaProvider, chainId: number): Promise<boolean> {
  // Preferred
  try {
    await provider.request({ method: "animica_switchChain", params: [{ chainId }] });
    return true;
  } catch {}
  // EVM-like
  try {
    await provider.request({ method: "wallet_switchEthereumChain", params: [{ chainId: "0x" + chainId.toString(16) }] });
    return true;
  } catch {}
  // Generic
  try {
    await provider.request({ method: "wallet_switchChain", params: [{ chainId }] });
    return true;
  } catch {}
  return false;
}

async function getPermissions(provider: AnimicaProvider): Promise<any[] | null> {
  try {
    const perms = await provider.request<any[]>({ method: "wallet_getPermissions" });
    return Array.isArray(perms) ? perms : null;
  } catch {
    return null;
  }
}

function hasAccountsPerm(perms: any[] | null): boolean {
  if (!perms) return false;
  return perms.some((p: any) => p?.parentCapability === "wallet_accounts" || p?.caveats?.some((c: any) => c?.type === "restrictReturnedAccounts"));
}

export function useAccount(opts: UseAccountOptions = {}) {
  const { autoConnect = true, expectedChainId, storageKey = SESSION_KEY_DEFAULT } = opts;

  const provider = useMemo(() => getProvider(), []);
  const [status, setStatus] = useState<Status>(provider ? "disconnected" : "unavailable");
  const [account, setAccount] = useState<Account | null>(null);
  const [chainId, setChainId] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const connectingRef = useRef<boolean>(false);

  const persist = useCallback(
    (connected: boolean) => {
      try {
        if (connected) localStorage.setItem(storageKey, "1");
        else localStorage.removeItem(storageKey);
      } catch {}
    },
    [storageKey]
  );

  const readPersisted = useCallback((): boolean => {
    try {
      return localStorage.getItem(storageKey) === "1";
    } catch {
      return false;
    }
  }, [storageKey]);

  const refreshChainId = useCallback(async () => {
    if (!provider) return;
    const id = await getChainId(provider);
    setChainId(id);
    return id;
  }, [provider]);

  const hasAccountsPermission = useCallback(async (): Promise<boolean> => {
    if (!provider) return false;
    const perms = await getPermissions(provider);
    return hasAccountsPerm(perms);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [provider]);

  const connect = useCallback(async () => {
    if (!provider) {
      setStatus("unavailable");
      setError("Animica provider not found. Please install/enable the wallet.");
      return;
    }
    if (connectingRef.current) return;
    connectingRef.current = true;
    setStatus("connecting");
    setError(null);

    try {
      const accounts = await tryRequestAccounts(provider);
      if (!accounts || accounts.length === 0) {
        throw new Error("No accounts authorized.");
      }
      const addr = accounts[0];
      setAccount({ address: addr });
      persist(true);

      const id = await refreshChainId();

      // Switch if expectedChainId provided and different
      if (typeof expectedChainId === "number" && id !== expectedChainId) {
        const ok = await switchChain(provider, expectedChainId);
        if (!ok) {
          setStatus("error");
          setError(`Wrong network. Expected chainId=${expectedChainId}, got ${id ?? "unknown"}. Please switch in your wallet.`);
          connectingRef.current = false;
          return;
        }
        // After switch, refresh id
        await refreshChainId();
      }

      setStatus("connected");
    } catch (e: any) {
      setStatus("error");
      setError(e?.message ?? "Failed to connect.");
      persist(false);
      setAccount(null);
    } finally {
      connectingRef.current = false;
    }
  }, [provider, expectedChainId, persist, refreshChainId]);

  const disconnect = useCallback(async () => {
    if (!provider) return;
    try {
      // Best-effort revoke
      try {
        await provider.request({
          method: "wallet_revokePermissions",
          params: [{ accounts: {} }],
        });
      } catch {
        // ignore if unsupported
      }
    } finally {
      setAccount(null);
      persist(false);
      setStatus(provider ? "disconnected" : "unavailable");
      setError(null);
    }
  }, [provider, persist]);

  const ensureChain = useCallback(
    async (targetChainId: number) => {
      if (!provider) return false;
      const id = await getChainId(provider);
      if (id === targetChainId) return true;
      const ok = await switchChain(provider, targetChainId);
      if (ok) {
        setChainId(targetChainId);
      }
      return ok;
    },
    [provider]
  );

  // Event handlers
  useEffect(() => {
    if (!provider || typeof provider.on !== "function") return;

    const onAccountsChanged = (accs: string[] = []) => {
      if (accs.length === 0) {
        // Locked or permissions revoked
        setAccount(null);
        setStatus("disconnected");
        persist(false);
        return;
      }
      const addr = accs[0];
      setAccount({ address: addr });
      if (status !== "connected") setStatus("connected");
    };

    const onChainChanged = async (_: any) => {
      const id = await refreshChainId();
      if (typeof expectedChainId === "number" && id !== expectedChainId) {
        setError(`Connected to chainId=${id}; expected ${expectedChainId}.`);
      } else {
        setError(null);
      }
    };

    const onDisconnect = (_: any) => {
      setStatus("disconnected");
      setAccount(null);
      persist(false);
    };

    const onConnect = async (_: any) => {
      await refreshChainId();
    };

    provider.on("accountsChanged", onAccountsChanged);
    provider.on("chainChanged", onChainChanged);
    provider.on("disconnect", onDisconnect);
    provider.on("connect", onConnect);

    return () => {
      provider.removeListener?.("accountsChanged", onAccountsChanged);
      provider.removeListener?.("chainChanged", onChainChanged);
      provider.removeListener?.("disconnect", onDisconnect);
      provider.removeListener?.("connect", onConnect);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [provider, expectedChainId, persist, refreshChainId, status]);

  // Attempt auto-connect on mount if previously authorized
  useEffect(() => {
    let cancelled = false;
    (async () => {
      if (!provider) return;
      if (!autoConnect || !readPersisted()) return;
      // If we already have permission, pick the first account silently
      try {
        const perms = await getPermissions(provider);
        if (hasAccountsPerm(perms)) {
          const accounts =
            (await provider.request<string[]>({ method: "animica_accounts" }).catch(() => [])) ||
            (await provider.request<string[]>({ method: "requestAccounts" }).catch(() => [])) ||
            [];
          if (!cancelled && accounts.length > 0) {
            setAccount({ address: accounts[0] });
            await refreshChainId();
            setStatus("connected");
            return;
          }
        }
        // If we reached here but have no accounts, remain disconnected
        if (!cancelled && status === "disconnected") {
          setStatus("disconnected");
        }
      } catch {
        // ignore
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [provider, autoConnect]);

  return {
    provider,
    status,
    account,
    chainId,
    error,
    connect,
    disconnect,
    ensureChain,
    hasAccountsPermission,
  };
}

export default useAccount;
