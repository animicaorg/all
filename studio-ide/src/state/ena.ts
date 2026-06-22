import { create } from "zustand";
import { enaApi } from "@/services/enaApi";

interface EnaState {
  connected: boolean;
  checked: boolean;
  busy: boolean;
  error: string | null;
  status: () => Promise<void>;
  connect: (key: string) => Promise<boolean>;
  disconnect: () => Promise<void>;
}

export const useEnaStore = create<EnaState>((set) => ({
  connected: false,
  checked: false,
  busy: false,
  error: null,
  status: async () => {
    try {
      const r = await enaApi.keyStatus();
      set({ connected: !!r.connected, checked: true });
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
  },
}));
