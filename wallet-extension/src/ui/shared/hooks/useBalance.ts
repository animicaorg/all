import { useCallback, useEffect, useMemo, useRef, useState } from "react";

/**
 * Minimal EIP-1193-ish provider typing for our in-page provider.
 * The real ambient typing lives in src/types/global.d.ts; this is a local fallback.
 */
type AnimicaProvider = {
  request<T = unknown>(args: { method: string; params?: unknown[] }): Promise<T>;
  on?(event: string, handler: (...args: any[]) => void): void;
  removeListener?(event: string, handler: (...args: any[]) => void): void;
};

declare global {
  interface Window {
    animica?: AnimicaProvider;
  }
}

export type UseBalanceState = {
  /** Address used for balance lookup. */
  address: string | null;
  /** Active network info when available. */
  network: { chainId?: string | number; name?: string } | null;
  /** Raw chain units (bigint) used by popup BalanceCard. */
  balance: bigint | null;
  /** Symbol displayed in popup BalanceCard. */
  symbol: string;
  /** Chain decimals used for display/formatting. */
  decimals: number;
  /** Last successful balance refresh timestamp (epoch ms). */
  lastUpdatedAt: number | null;
  /** Raw chain units (bigint) */
  value: bigint | null;
  /** Human string using provided decimals */
  formatted: string | null;
  loading: boolean;
  error: string | null;
  /** Manually re-fetch balance */
  refresh: () => void;
};

/** Format bigint in chain units into decimal string with given decimals. */
function formatUnits(value: bigint, decimals = 18): string {
  const neg = value < 0n;
  const v = neg ? -value : value;

  const base = 10n ** BigInt(decimals);
  const whole = v / base;
  const frac = v % base;

  // Left-pad fractional with zeros up to `decimals`
  const fracStr = frac.toString().padStart(decimals, "0").replace(/0+$/, "");
  const body = fracStr.length ? `${whole.toString()}.${fracStr}` : whole.toString();
  return neg ? `-${body}` : body;
}

function parseBalance(raw: unknown): bigint {
  if (typeof raw === "bigint") return raw;
  if (typeof raw === "number") return BigInt(raw);
  if (typeof raw === "string") return BigInt(raw);

  // Some RPC backends wrap the value in an object (e.g., { balance: "0x..." }).
  if (raw && typeof raw === "object") {
    const fromObj = (raw as any).balance ?? (raw as any).result ?? (raw as any).free;
    if (typeof fromObj === "bigint") return fromObj;
    if (typeof fromObj === "number") return BigInt(fromObj);
    if (typeof fromObj === "string") return BigInt(fromObj);
  }

  throw new Error("Unsupported balance format");
}

/**
 * useBalance — reads balance for an address and keeps it fresh on newHeads.
 * - Uses window.animica provider:
 *     - chainId via `animica_chainId`
 *     - balance via `animica_getBalance` with params [address, "latest"]
 * - Re-fetches when: address/chain changes, or on each `newHeads` event.
 */
export function useBalance(address: string | undefined, decimals = 18): UseBalanceState {
  const [value, setValue] = useState<bigint | null>(null);
  const [loading, setLoading] = useState<boolean>(!!address);
  const [error, setError] = useState<string | null>(null);
  const [lastUpdatedAt, setLastUpdatedAt] = useState<number | null>(null);
  const [chainId, setChainId] = useState<string | null>(null);

  const chainIdRef = useRef<string | null>(null);
  const inFlight = useRef<number>(0);
  const disposed = useRef<boolean>(false);

  const provider = typeof window !== "undefined" ? window.animica : undefined;

  const readChainId = useCallback(async (): Promise<string | null> => {
    if (!provider) return null;
    try {
      const id = await provider.request<string>({ method: "animica_chainId" });
      return id ?? null;
    } catch {
      return null;
    }
  }, [provider]);

  const fetchBalance = useCallback(async () => {
    if (!provider || !address) return;
    const ticket = ++inFlight.current;
    setLoading(true);
    setError(null);
    try {
      // Ensure we have chainId (cached) to tie reactivity to network changes
      const cid = chainIdRef.current ?? (await readChainId());
      chainIdRef.current = cid;
      setChainId(cid);

      // Call balance
      // Expect hex or decimal string per node; normalize to bigint
      const raw = await provider.request<unknown>({ method: "animica_getBalance", params: [address, "latest"] });
      const bn = parseBalance(raw);

      if (disposed.current || ticket !== inFlight.current) return;
      setValue(bn);
      setLastUpdatedAt(Date.now());
    } catch (e: any) {
      if (disposed.current || ticket !== inFlight.current) return;
      setError(e?.message ?? String(e));
    } finally {
      if (disposed.current || ticket !== inFlight.current) return;
      setLoading(false);
    }
  }, [provider, address, readChainId]);

  // Manual refresh
  const refresh = useCallback(() => {
    void fetchBalance();
  }, [fetchBalance]);

  // Initial & dependency-driven fetch
  useEffect(() => {
    disposed.current = false;
    chainIdRef.current = null;
    if (address) {
      void fetchBalance();
    } else {
      setValue(null);
      setLoading(false);
      setError(null);
      setLastUpdatedAt(null);
      setChainId(null);
    }
    return () => {
      disposed.current = true;
      inFlight.current = 0;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [address]);

  // Refetch on chain changes
  useEffect(() => {
    if (!provider) return;
    const onChainChanged = async () => {
      const next = await readChainId();
      chainIdRef.current = next;
      setChainId(next);
      void fetchBalance();
    };
    provider.on?.("chainChanged", onChainChanged);
    return () => provider.removeListener?.("chainChanged", onChainChanged);
  }, [provider, readChainId, fetchBalance]);

  // Refetch on each new head
  useEffect(() => {
    if (!provider || !address) return;
    const onNewHead = () => void fetchBalance();
    provider.on?.("newHeads", onNewHead);
    return () => provider.removeListener?.("newHeads", onNewHead);
  }, [provider, address, fetchBalance]);

  const formatted = useMemo(() => (value != null ? formatUnits(value, decimals) : null), [value, decimals]);
  const network = useMemo(() => (chainId ? { chainId, name: chainId } : null), [chainId]);

  return {
    address: address ?? null,
    network,
    balance: value,
    symbol: "ANIM",
    decimals,
    lastUpdatedAt,
    value,
    formatted,
    loading,
    error,
    refresh,
  };
}

export default useBalance;
