import { create } from "zustand";
import { enaApi } from "@/services/enaApi";

export interface FreeStatus {
  enabled: boolean;
  limit: number;
  used: number;
  remaining: number;
}

interface EnaState {
  connected: boolean;
  free: FreeStatus;
  buyUrl: string;
  checked: boolean;
  busy: boolean;
  error: string | null;
  canChat: () => boolean;
  status: () => Promise<void>;
  connect: (key: string) => Promise<boolean>;
  disconnect: () => Promise<void>;
}

const NO_FREE: FreeStatus = { enabled: false, limit: 0, used: 0, remaining: 0 };

export const useEnaStore = create<EnaState>((set, get) => ({
  connected: false,
  free: NO_FREE,
  buyUrl: "https://pool.animica.org/keys",
  checked: false,
  busy: false,
  error: null,
  canChat: () => {
    const s = get();
    return s.connected || (s.free.enabled && s.free.remaining > 0);
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
}));
