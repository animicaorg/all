/**
 * useAnimicaWallet — bridge to the in-page provider injected by the
 * Animica browser extension (window.animica).
 *
 * Mirrors AIP-1193-ish semantics:
 *   - connect()          requests account access (prompts the user)
 *   - getBalance()       fetches the active account's balance
 *   - signAndSend(tx)    requests user approval, signs, broadcasts
 *
 * When the extension isn't installed, exposes `isAvailable=false` and a
 * helpful installUrl. Components should render an "install" call-to-action
 * in that case instead of failing.
 */

import { useCallback, useEffect, useState } from "react";

declare global {
  interface Window {
    animica?: AnimicaProvider;
  }
}

export type AnimicaProvider = {
  isAnimica?: boolean;
  request: <T = unknown>(args: {
    method: string;
    params?: unknown[] | Record<string, unknown>;
  }) => Promise<T>;
  on?: (event: string, fn: (...args: unknown[]) => void) => void;
  removeListener?: (event: string, fn: (...args: unknown[]) => void) => void;
};

export type AnimicaTxRequest = {
  to: string;
  value: string; // decimal string in ANIMICA
  data?: string;
  chainId?: number;
  memo?: Record<string, unknown>;
};

export type WalletState = {
  isAvailable: boolean;
  installUrl: string;
  address: string | null;
  balanceAnimica: number | null;
  chainId: number | null;
  network: string | null;
  connecting: boolean;
  error: string | null;
};

const INSTALL_URL = "https://animica.org/wallet";

export function useAnimicaWallet() {
  const [state, setState] = useState<WalletState>({
    isAvailable: typeof window !== "undefined" && !!window.animica,
    installUrl: INSTALL_URL,
    address: null,
    balanceAnimica: null,
    chainId: null,
    network: null,
    connecting: false,
    error: null,
  });

  // Re-detect on mount and watch for late-injection.
  useEffect(() => {
    if (typeof window === "undefined") return;
    const tick = () => {
      const available = !!window.animica;
      setState((s) => (s.isAvailable === available ? s : { ...s, isAvailable: available }));
    };
    tick();
    const handle = window.setInterval(tick, 500);
    return () => window.clearInterval(handle);
  }, []);

  // React to provider events.
  useEffect(() => {
    const p = window.animica;
    if (!p?.on) return;
    const onAccounts = (...args: unknown[]) => {
      const accounts = (args[0] as string[] | undefined) ?? [];
      setState((s) => ({ ...s, address: accounts[0] ?? null }));
    };
    const onChain = (...args: unknown[]) => {
      const chainId = args[0] as string | number | undefined;
      setState((s) => ({
        ...s,
        chainId: typeof chainId === "string" ? parseInt(chainId, 16) || null
                 : typeof chainId === "number" ? chainId
                 : null,
      }));
    };
    p.on("accountsChanged", onAccounts);
    p.on("chainChanged", onChain);
    return () => {
      p.removeListener?.("accountsChanged", onAccounts);
      p.removeListener?.("chainChanged", onChain);
    };
  }, [state.isAvailable]);

  const connect = useCallback(async () => {
    if (!window.animica) {
      setState((s) => ({ ...s, error: "wallet extension not installed" }));
      return;
    }
    setState((s) => ({ ...s, connecting: true, error: null }));
    try {
      const accounts = await window.animica.request<string[]>({
        method: "animica_requestAccounts",
      });
      const chainHex = await window.animica.request<string>({
        method: "animica_chainId",
      }).catch(() => null);
      const balance = accounts[0]
        ? await window.animica
            .request<{ balance: string }>({
              method: "animica_getBalance",
              params: { address: accounts[0] },
            })
            .catch(() => ({ balance: "0" }))
        : null;
      const network = await window.animica
        .request<string>({ method: "animica_network" })
        .catch(() => null);
      setState((s) => ({
        ...s,
        connecting: false,
        address: accounts[0] ?? null,
        chainId: chainHex ? parseInt(chainHex, 16) || null : null,
        balanceAnimica: balance ? Number(balance.balance) : 0,
        network: network ?? null,
        error: null,
      }));
    } catch (err) {
      const msg =
        err instanceof Error ? err.message
        : typeof err === "object" && err && "message" in err
        ? String((err as { message: unknown }).message)
        : String(err);
      setState((s) => ({ ...s, connecting: false, error: msg }));
    }
  }, []);

  const refreshBalance = useCallback(async () => {
    if (!window.animica || !state.address) return;
    try {
      const { balance } = await window.animica.request<{ balance: string }>({
        method: "animica_getBalance",
        params: { address: state.address },
      });
      setState((s) => ({ ...s, balanceAnimica: Number(balance) }));
    } catch {
      /* ignore — non-fatal */
    }
  }, [state.address]);

  const signAndSend = useCallback(
    async (tx: AnimicaTxRequest): Promise<string> => {
      if (!window.animica) throw new Error("wallet extension not installed");
      if (!state.address) throw new Error("wallet not connected");
      const txHash = await window.animica.request<string>({
        method: "animica_sendTransaction",
        params: [{ from: state.address, ...tx }],
      });
      // optimistic refresh (final balance settles on receipt)
      void refreshBalance();
      return txHash;
    },
    [state.address, refreshBalance],
  );

  const signMessage = useCallback(
    async (message: string): Promise<string> => {
      if (!window.animica) throw new Error("wallet extension not installed");
      if (!state.address) throw new Error("wallet not connected");
      return window.animica.request<string>({
        method: "animica_signMessage",
        params: [state.address, message],
      });
    },
    [state.address],
  );

  return { ...state, connect, signAndSend, signMessage, refreshBalance };
}
