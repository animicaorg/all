import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import axios from 'axios';
import { getApiBaseUrl } from './endpoints';

const API_URL = getApiBaseUrl();

interface AuthState {
  authReady: boolean;
  isAuthenticated: boolean;
  user: { id: string; email: string } | null;
  initialize: () => Promise<void>;
  login: (email: string, password: string) => Promise<void>;
  logout: () => void;
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set) => ({
      authReady: false,
      isAuthenticated: false,
      user: null,
      initialize: async () => {
        try {
          const response = await axios.get(`${API_URL}/auth/me`, {
            withCredentials: true,
          });
          const { id, email } = response.data;
          if (id && email) {
            set({ isAuthenticated: true, user: { id, email }, authReady: true });
            return;
          }
        } catch {
          // No valid session. Keep unauthenticated state.
        }
        set({ isAuthenticated: false, user: null, authReady: true });
      },
      login: async (email: string, password: string) => {
        const response = await axios.post(`${API_URL}/auth/login`, {
          email,
          password,
        }, {
          withCredentials: true,
        });
        
        const { userId, email: userEmail } = response.data;
        set({ isAuthenticated: true, user: { id: userId, email: userEmail || email }, authReady: true });
      },
      logout: () => {
        axios.post(`${API_URL}/auth/logout`, {}, { withCredentials: true }).catch(() => {
          // Ignore logout errors and still clear local auth state.
        });
        set({ isAuthenticated: false, user: null, authReady: true });
      },
    }),
    {
      name: 'auth-storage',
    }
  )
);
