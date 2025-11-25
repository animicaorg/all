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

      // Call balance
      // Expect hex or decimal string per node; normalize to bigint
      const raw = await provider.request<string | number>({ method: "animica_getBalance", params: [address, "latest"] });
      let bn: bigint;
      if (typeof raw === "number") bn = BigInt(raw);
      else if (typeof raw === "string" && raw.startsWith("0x")) bn = BigInt(raw);
      else if (typeof raw === "string") bn = BigInt(raw);
      else throw new Error("Unsupported balance format");

      if (disposed.current || ticket !== inFlight.current) return;
      setValue(bn);
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
      chainIdRef.current = await readChainId();
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

  return { value, formatted, loading, error, refresh };
}

export default useBalance;
