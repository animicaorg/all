import { create } from "zustand";
import { api } from "@/services/api";

export interface Me {
  authed: boolean;
  email: string | null;
  tier: string | null;
  plan: string | null;
  methods?: { password?: boolean; anon?: boolean; magic?: boolean; wallet?: boolean };
}

interface AuthState {
  me: Me | null;
  loading: boolean;
  error: string | null;
  fetchMe: () => Promise<void>;
  startAnon: () => Promise<void>;
  logout: () => Promise<void>;
}

export const useAuthStore = create<AuthState>((set) => ({
  me: null,
  loading: true,
  error: null,
  fetchMe: async () => {
    set({ loading: true, error: null });
    try {
      const me = await api.get("/api/me");
      set({ me, loading: false });
    } catch (e: any) {
      set({ me: { authed: false, email: null, tier: null, plan: null }, loading: false, error: e?.message });
    }
  },
  startAnon: async () => {
    await api.post("/api/anon", {});
    const me = await api.get("/api/me");
    set({ me });
  },
  logout: async () => {
    try {
      await api.post("/api/logout", {});
    } catch {
      /* ignore */
    }
    set({ me: { authed: false, email: null, tier: null, plan: null } });
  },
}));
