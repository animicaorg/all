// Wallet store for the ENA "pay with your wallet, capped" flow.
//
// Ported from studio-web/src/state/account.ts + studio-web/src/hooks/
// useAnimicaWallet.ts: talks to the injected `window.animica` provider
// (an EIP-1193-like { request({ method, params }) } interface).
//
// This store DOES NOT hold private keys — all signing happens in the wallet
// extension. We only:
//   - connect()      → prompt for accounts, remember the selected address
//   - refreshBalance → read on-chain ANM balance (decimal string)
//   - sendAnm(to, n) → ask the extension to sign+send an ANM transfer; the
//                      extension shows the single confirmation prompt and
//                      returns the tx hash.
//
// The exact provider tx-send method (`animica_sendTransaction` with
// params [{ from, to, value, memo? }]) and the balance method
// (`animica_getBalance` { address } → { balance }) are the ones studio-web's
// aicfChat pay() path uses (see useAnimicaWallet.signAndSend / refreshBalance).
// We fall back to node JSON-RPC `state.getBalance` for balance if the
// provider method is unavailable.

import { create } from "zustand";

export interface AnimicaProvider {
  request<T = unknown>(args: {
    method: string;
    params?: unknown[] | Record<string, unknown>;
  }): Promise<T>;
  on?(event: string, handler: (...args: any[]) => void): void;
  removeListener?(event: string, handler: (...args: any[]) => void): void;
}

declare global {
  interface Window {
    animica?: AnimicaProvider;
  }
}

function provider(): AnimicaProvider | undefined {
  return typeof window !== "undefined" ? window.animica : undefined;
}

// Node JSON-RPC endpoint (same host the broker proxies the chain to). The
// node exposes state.getBalance under /rpc.
const NODE_RPC = (import.meta as any)?.env?.VITE_NODE_RPC_URL || "/rpc";

async function nodeRpc<T = unknown>(method: string, params: unknown): Promise<T> {
  const res = await fetch(NODE_RPC, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ jsonrpc: "2.0", id: Date.now(), method, params }),
  });
  if (!res.ok) throw new Error(`RPC ${method} HTTP ${res.status}`);
  const msg = await res.json();
  if (msg.error) throw new Error(msg.error?.message ?? "RPC error");
  return msg.result as T;
}

// Parse a balance value that may arrive as a decimal string, a number, or a
// hex base-unit string. ANM has 9 decimals; node state.getBalance returns
// base units (nano-ANM) while the wallet's animica_getBalance returns a
// human ANM decimal string. We normalise both to a decimal ANM number.
function parseAnmDecimal(v: unknown): number {
  if (typeof v === "number") return Number.isFinite(v) ? v : 0;
  if (typeof v === "string") {
    const s = v.trim();
    if (/^0x[0-9a-fA-F]+$/.test(s)) {
      // hex base units → ANM
      return Number(BigInt(s)) / 1e9;
    }
    if (/^\d+$/.test(s) && s.length > 0) {
      // Heuristic: a pure integer from the node is base units (nano-ANM);
      // a small decimal-looking value from the wallet is already ANM.
      return Number(s) / 1e9;
    }
    const n = Number(s);
    return Number.isFinite(n) ? n : 0;
  }
  return 0;
}

interface WalletState {
  available: boolean;
  connected: boolean;
  connecting: boolean;
  // How we connected: "injected" can sign txs in-Studio (extension / in-app
  // browser provider); "web" (wallet.animica.org popup) is accounts-only — it
  // can't sign for the Studio, so deposits must be funded manually.
  kind: "injected" | "web" | null;
  address?: string;
  balanceAnm: number; // on-chain wallet balance, decimal ANM
  error: string | null;

  detect: () => void;
  connect: () => Promise<boolean>;
  // Web-wallet (wallet.animica.org) popup connect — start handshake, open the
  // approval popup, poll the broker until approved.
  connectWeb: () => Promise<boolean>;
  refreshBalance: () => Promise<void>;
  // Sign + send an ANM transfer via the wallet extension. Resolves with the
  // tx hash. `amountAnm` is a decimal ANM number.
  sendAnm: (to: string, amountAnm: number) => Promise<string>;
}

export const useWalletStore = create<WalletState>((set, get) => ({
  available: typeof window !== "undefined" && !!window.animica,
  connected: false,
  connecting: false,
  kind: null,
  address: undefined,
  balanceAnm: 0,
  error: null,

  detect: () => set({ available: !!provider() }),

  connect: async () => {
    const p = provider();
    if (!p) {
      set({
        available: false,
        error: "Animica wallet not found. Install/enable the extension.",
      });
      return false;
    }
    set({ available: true, connecting: true, error: null });
    try {
      let accounts: string[] = [];
      // The Animica wallet background handles exactly these (see
      // apps/wallet-extension background: animica_requestAccounts triggers the
      // connect-approval popup; eth_/provider_ are aliases). Calling an unknown
      // method (e.g. wallet_requestAccounts) can hang with no response and never
      // reach the real connect method, so we ONLY use the supported ones.
      let lastErr: unknown = null;
      const candidates = [
        "animica_requestAccounts",
        "eth_requestAccounts",
        "provider_requestAccounts",
      ];
      for (const m of candidates) {
        try {
          accounts = (await p.request<string[]>({ method: m })) || [];
          if (accounts.length) break;
        } catch (e) {
          lastErr = e;
        }
      }
      if (!accounts.length && lastErr) throw lastErr;
      const addr = accounts?.[0];
      if (!addr) throw new Error("No account returned by wallet.");
      set({ connected: true, kind: "injected", address: addr, connecting: false, error: null });
      void get().refreshBalance();
      return true;
    } catch (e: any) {
      set({
        connecting: false,
        connected: false,
        error: String(e?.message ?? e),
      });
      return false;
    }
  },

  connectWeb: async () => {
    // Open the popup SYNCHRONOUSLY (still in the click gesture) so it isn't
    // blocked; we navigate it once the broker returns the handshake URL.
    const popup = window.open("about:blank", "animica-wallet", "width=440,height=760");
    set({ connecting: true, error: null });
    try {
      const startRes = await fetch("/api/ide/wallet/connect/start", {
        method: "POST",
        credentials: "include",
        headers: { "content-type": "application/json" },
        body: "{}",
      });
      if (!startRes.ok) throw new Error("Could not start the wallet handshake.");
      const { requestId, popupUrl } = await startRes.json();
      if (popup) popup.location.href = popupUrl;
      else {
        set({ connecting: false, error: "Allow pop-ups for this site, then try the web wallet again." });
        return false;
      }

      const deadline = Date.now() + 5 * 60 * 1000;
      while (Date.now() < deadline) {
        await new Promise((r) => setTimeout(r, 1500));
        let pr: any;
        try {
          const res = await fetch(
            `/api/ide/wallet/connect/poll?requestId=${encodeURIComponent(requestId)}`,
            { credentials: "include" },
          );
          pr = await res.json();
        } catch {
          continue;
        }
        if (pr.status === "approved" && pr.address) {
          set({ connected: true, kind: "web", address: pr.address, connecting: false, error: null });
          try { popup.close(); } catch { /* ignore */ }
          void get().refreshBalance();
          return true;
        }
        if (pr.status === "rejected" || pr.status === "expired" || pr.status === "unknown") {
          set({ connecting: false, error: pr.status === "rejected" ? "Connection rejected in the wallet." : "Wallet connect request expired." });
          try { popup.close(); } catch { /* ignore */ }
          return false;
        }
      }
      set({ connecting: false, error: "Wallet connect timed out." });
      try { popup.close(); } catch { /* ignore */ }
      return false;
    } catch (e: any) {
      set({ connecting: false, error: String(e?.message ?? e) });
      try { popup?.close(); } catch { /* ignore */ }
      return false;
    }
  },

  refreshBalance: async () => {
    const addr = get().address;
    if (!addr) return;
    const p = provider();
    // Prefer the wallet's own balance method (matches studio-web).
    if (p) {
      try {
        const r = await p.request<{ balance: string }>({
          method: "animica_getBalance",
          params: { address: addr },
        });
        if (r && r.balance != null) {
          // wallet returns a human ANM decimal string
          const n = Number(r.balance);
          set({ balanceAnm: Number.isFinite(n) ? n : parseAnmDecimal(r.balance) });
          return;
        }
      } catch {
        /* fall back to node RPC */
      }
    }
    try {
      const result = await nodeRpc<unknown>("state.getBalance", [addr]);
      set({ balanceAnm: parseAnmDecimal(result) });
    } catch (e: any) {
      set({ error: `balance: ${String(e?.message ?? e)}` });
    }
  },

  sendAnm: async (to: string, amountAnm: number) => {
    const p = provider();
    if (get().kind === "web" || !p) {
      throw new Error(
        "This wallet can't sign in the Studio. Fund your budget manually (send ANM to the treasury, then confirm the tx), or connect the Animica extension / in-app wallet to sign here.",
      );
    }
    const from = get().address;
    if (!from) throw new Error("Wallet not connected.");
    if (!(amountAnm > 0)) throw new Error("Deposit amount must be positive.");
    // Decimal ANM string with up to 9 places (matches studio-web pay()).
    const value = amountAnm.toFixed(9).replace(/\.?0+$/, "");
    const txHash = await p.request<string>({
      method: "animica_sendTransaction",
      params: [
        {
          from,
          to,
          value,
          memo: { kind: "ena.budget.deposit" },
        },
      ],
    });
    void get().refreshBalance();
    return txHash;
  },
}));
