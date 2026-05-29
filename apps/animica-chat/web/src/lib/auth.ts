import { create } from 'zustand';
import { api, ApiError } from './api';

export interface MeSubscription {
  status: string;
  planCode: string;
  currentPeriodEnd: string | null;
  messagesUsedThisPeriod: number;
  weeklyMessages: number;
  currentWeekStart: string | null;
}

export interface Me {
  user: { id: string; email: string; name: string | null; role: string };
  subscription: MeSubscription | null;
}

interface AuthState {
  me: Me | null;
  // True until the first /api/auth/me call settles (either way). Used by
  // <Protected> so it doesn't redirect to /login during the initial mount
  // gap where `me` is null because we haven't asked yet — distinct from
  // "definitely logged out".
  ready: boolean;
  loading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
  logout: () => Promise<void>;
}

export const useAuth = create<AuthState>((set) => ({
  me: null,
  ready: false,
  loading: false,
  error: null,
  refresh: async () => {
    set({ loading: true, error: null });
    try {
      const r = await api.get<Me>('/api/auth/me');
      set({ me: r, loading: false, ready: true });
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) {
        set({ me: null, loading: false, ready: true });
      } else {
        set({ error: (e as Error).message, loading: false, ready: true });
      }
    }
  },
  logout: async () => {
    await api.post('/api/auth/logout').catch(() => undefined);
    set({ me: null, ready: true });
    // Hard reload so any cached chat / billing state in memory is dropped.
    window.location.href = '/';
  },
}));
