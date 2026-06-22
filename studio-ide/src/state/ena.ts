import { create } from "zustand";
import { enaApi } from "@/services/enaApi";
import { useWalletStore } from "@/state/wallet";

export interface FreeStatus {
  enabled: boolean;
  limit: number;
  used: number;
  remaining: number;
}

const CAP_KEY = "ena.cap.anm";

function loadCap(fallback: number): number {
  try {
    const v = localStorage.getItem(CAP_KEY);
    if (v != null) {
      const n = Number(v);
      if (Number.isFinite(n) && n > 0) return n;
    }
  } catch {
    /* ignore */
  }
  return fallback;
}

function saveCap(n: number) {
  try {
    localStorage.setItem(CAP_KEY, String(n));
  } catch {
    /* ignore */
  }
}

interface EnaState {
  connected: boolean;
  free: FreeStatus;
  buyUrl: string;
  checked: boolean;
  busy: boolean;
  error: string | null;

  // ANM budget (pay-with-wallet, capped).
  balanceAnm: number; // prepaid budget held by broker for this user
  treasury: string;
  perCallAnm: number; // min balance to start a run / min deposit
  anmPerKtok: number; // actual per-1k-token rate (usage-billed)
  defaultCap: number;
  cap: number; // user-set cap, persisted to localStorage
  depositing: boolean; // a wallet deposit is in flight

  canChat: () => boolean;
  needsBudget: () => boolean; // not own-key, free exhausted, balance too low
  status: () => Promise<void>;
  connect: (key: string) => Promise<boolean>;
  disconnect: () => Promise<void>;

  setCap: (n: number) => void;
  refreshBudget: () => Promise<void>;
  // Make sure the broker-held budget covers `cap`; if short, trigger ONE
  // wallet deposit for the difference (the single signature). Returns true
  // when the budget is sufficient afterwards.
  ensureBudget: (cap: number) => Promise<boolean>;

  // Manual funding (for web wallets / any wallet that can't sign in-Studio):
  // the user sends ANM to the treasury themselves, then confirms by tx id.
  manualOpen: boolean;
  setManualOpen: (v: boolean) => void;
  submitDeposit: (txid: string) => Promise<boolean>;
}

const NO_FREE: FreeStatus = { enabled: false, limit: 0, used: 0, remaining: 0 };

// Poll the broker's deposit verifier until the tx is confirmed on-chain (it
// replies {pending:true} until then), then return the new balance. Throws on a
// hard rejection (wrong recipient / underpaid). Returns null on timeout.
async function pollDepositConfirmed(txid: string): Promise<{ balanceAnm: number } | null> {
  const deadline = Date.now() + 4 * 60 * 1000;
  for (;;) {
    const r = await enaApi.deposit(txid); // throws (ApiError) on 4xx hard reject
    if (!r.pending) return { balanceAnm: r.balanceAnm };
    if (Date.now() > deadline) return null;
    await new Promise((res) => setTimeout(res, 4000));
  }
}

export const useEnaStore = create<EnaState>((set, get) => ({
  connected: false,
  free: NO_FREE,
  buyUrl: "https://pool.animica.org/keys",
  checked: false,
  busy: false,
  error: null,

  balanceAnm: 0,
  treasury: "",
  perCallAnm: 0,
  anmPerKtok: 0,
  defaultCap: 5,
  cap: loadCap(5),
  depositing: false,
  manualOpen: false,

  canChat: () => {
    const s = get();
    if (s.connected) return true;
    if (s.free.enabled && s.free.remaining > 0) return true;
    // Budget mode: enough prepaid balance for at least one model call.
    return s.perCallAnm > 0 && s.balanceAnm >= s.perCallAnm - 1e-12;
  },

  needsBudget: () => {
    const s = get();
    if (s.connected) return false;
    if (s.free.enabled && s.free.remaining > 0) return false;
    return !(s.perCallAnm > 0 && s.balanceAnm >= s.perCallAnm - 1e-12);
  },

  status: async () => {
    try {
      const r: any = await enaApi.keyStatus();
      set({
        connected: !!r.connected,
        free: r.free || NO_FREE,
        buyUrl: r.buyUrl || get().buyUrl,
        checked: true,
      });
    } catch {
      set({ checked: true });
    }
    // Budget status comes from a separate endpoint; refresh alongside.
    await get().refreshBudget();
  },

  refreshBudget: async () => {
    try {
      const w = await enaApi.getWallet();
      const defaultCap = w.defaultCap > 0 ? w.defaultCap : get().defaultCap;
      set({
        balanceAnm: w.balanceAnm,
        treasury: w.treasury,
        perCallAnm: w.perCallAnm,
        anmPerKtok: w.anmPerKtok,
        defaultCap,
        // If the user never set a cap, adopt the broker default.
        cap: get().cap || loadCap(defaultCap),
      });
    } catch {
      /* budget endpoint unavailable — leave defaults */
    }
  },

  connect: async (key: string) => {
    set({ busy: true, error: null });
    try {
      const r = await enaApi.connectKey(key.trim());
      set({ connected: !!r.connected, busy: false });
      return !!r.connected;
    } catch (e: any) {
      set({ busy: false, error: e?.message || "Could not connect that key." });
      return false;
    }
  },

  disconnect: async () => {
    try {
      await enaApi.disconnectKey();
    } catch {
      /* ignore */
    }
    set({ connected: false });
    await get().status();
  },

  setCap: (n: number) => {
    const v = Number.isFinite(n) && n > 0 ? n : get().defaultCap;
    saveCap(v);
    set({ cap: v });
  },

  ensureBudget: async (cap: number) => {
    const s = get();
    if (s.connected) return true; // own-key mode — no budget needed
    // `cap` is how much ANM to LOAD per top-up. Use the existing prepaid fund as
    // long as it still has enough to run — only deposit when it's (nearly) empty.
    // This is what makes a single top-up last across many chats.
    const minBal = s.perCallAnm > 0 ? s.perCallAnm : 1;
    if (s.balanceAnm >= minBal - 1e-12) return true;

    const wallet = useWalletStore.getState();
    set({ depositing: true, error: null });
    try {
      // Connect the wallet on demand (idempotent if already connected).
      if (!wallet.connected) {
        const ok = await wallet.connect();
        if (!ok) {
          set({
            depositing: false,
            error: useWalletStore.getState().error || "Connect your wallet to fund the budget.",
          });
          return false;
        }
      }
      const treasury = s.treasury;
      if (!treasury) throw new Error("No treasury address configured.");

      const wkind = useWalletStore.getState().kind;
      // Load the chosen amount in a single deposit (one signature).
      const amount = Math.ceil(Math.max(minBal, cap) * 1e9) / 1e9;

      // Web wallet → one-click sign-and-send via its /sign/ approval popup,
      // then wait for the first on-chain confirmation and credit. No paste.
      if (wkind === "web") {
        const txid = await useWalletStore.getState().signSendWeb(amount);
        const done = await pollDepositConfirmed(txid);
        if (!done) {
          set({ depositing: false, error: "Sent — waiting for on-chain confirmation. Tap Fund to check." });
          return false;
        }
        set({ balanceAnm: done.balanceAnm, depositing: false });
        return done.balanceAnm >= minBal - 1e-12;
      }

      // No signer at all → manual funding (send to treasury + confirm).
      if (wkind !== "injected") {
        set({ depositing: false, manualOpen: true });
        return false;
      }

      // Injected wallet: one wallet confirmation popup; tx id comes back.
      const txid = await useWalletStore.getState().sendAnm(treasury, amount);
      // Wait for the first on-chain confirmation, then credit — no paste.
      const done = await pollDepositConfirmed(txid);
      if (!done) {
        set({ depositing: false, error: "Sent — waiting for on-chain confirmation. It'll credit shortly; tap Fund to check." });
        return false;
      }
      set({ balanceAnm: done.balanceAnm, depositing: false });
      return done.balanceAnm >= minBal - 1e-12;
    } catch (e: any) {
      set({
        depositing: false,
        error: e?.message || "Deposit failed. Your wallet may have rejected the transaction.",
      });
      return false;
    }
  },

  setManualOpen: (v: boolean) => set({ manualOpen: v }),
  submitDeposit: async (txid: string) => {
    const t = (txid || "").trim();
    if (!t) return false;
    set({ depositing: true, error: null });
    try {
      const done = await pollDepositConfirmed(t);
      if (!done) {
        set({ depositing: false, error: "Waiting for on-chain confirmation — tap Confirm again shortly." });
        return false;
      }
      set({ balanceAnm: done.balanceAnm, depositing: false, manualOpen: false });
      return true;
    } catch (e: any) {
      set({ depositing: false, error: e?.message || "Could not verify that deposit." });
      return false;
    }
  },
}));
