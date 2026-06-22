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
  perCallAnm: number;
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
    if (s.balanceAnm >= cap - 1e-12) return true; // already covered

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

      // The web wallet (and any non-injected connection) can't sign a tx for
      // the Studio — fall back to manual funding instead of a confusing error.
      if (useWalletStore.getState().kind !== "injected") {
        set({ depositing: false, manualOpen: true });
        return false;
      }

      // Deposit only the shortfall — one wallet signature.
      const shortfall = Math.max(0, cap - s.balanceAnm);
      // Round up to whole nano-ANM to avoid sub-unit rounding leaving us short.
      const amount = Math.ceil(shortfall * 1e9) / 1e9;
      if (amount <= 0) {
        set({ depositing: false });
        return true;
      }

      const txid = await useWalletStore.getState().sendAnm(treasury, amount);
      const r = await enaApi.deposit(txid);
      set({ balanceAnm: r.balanceAnm, depositing: false });
      return r.balanceAnm >= cap - 1e-12 || r.balanceAnm >= (get().perCallAnm || 0) - 1e-12;
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
      const r = await enaApi.deposit(t);
      if (r.pending) {
        // Tx not confirmed yet — keep the dialog open so they can retry.
        set({ depositing: false, error: r.message || "Deposit not confirmed yet — try again shortly." });
        return false;
      }
      set({ balanceAnm: r.balanceAnm, depositing: false, manualOpen: false });
      return true;
    } catch (e: any) {
      set({ depositing: false, error: e?.message || "Could not verify that deposit." });
      return false;
    }
  },
}));
