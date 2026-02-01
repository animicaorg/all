import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import axios from 'axios';

const API_URL = import.meta.env.VITE_CEX_API_URL || 'http://trade.animica.org';

interface AuthState {
  isAuthenticated: boolean;
  user: { id: string; email: string } | null;
  login: (email: string, password: string) => Promise<void>;
  logout: () => void;
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set) => ({
      isAuthenticated: false,
      user: null,
      login: async (email: string, password: string) => {
        // Call the authentication API
        // Note: This assumes the auth endpoint exists at /auth/login
        // Adjust the endpoint based on actual API implementation
        const response = await axios.post(`${API_URL}/auth/login`, {
          email,
          password,
        }, {
          withCredentials: true,
        });
        
        const { userId, email: userEmail } = response.data;
        set({ isAuthenticated: true, user: { id: userId, email: userEmail || email } });
      },
      logout: () => {
        // Optionally call logout endpoint to clear server-side session
        axios.post(`${API_URL}/auth/logout`, {}, { withCredentials: true }).catch(() => {
          // Ignore errors on logout
        });
        set({ isAuthenticated: false, user: null });
      },
    }),
    {
      name: 'auth-storage',
    }
  )
);
