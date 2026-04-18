import { createContext, useContext, useEffect, useMemo, useState } from "react";

type AuthState = {
  token: string | null;
  role: string | null;
  username: string | null;
};

type AuthContextValue = AuthState & {
  setAuth: (next: AuthState) => void;
  clearAuth: () => void;
};

const AuthContext = createContext<AuthContextValue | undefined>(undefined);
const STORAGE_KEY = "banm_admin_auth";

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [state, setState] = useState<AuthState>({
    token: null,
    role: null,
    username: null
  });

  useEffect(() => {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) {
      try {
        const parsed = JSON.parse(raw) as AuthState;
        setState(parsed);
      } catch {
        localStorage.removeItem(STORAGE_KEY);
      }
    }
  }, []);

  const value = useMemo<AuthContextValue>(
    () => ({
      ...state,
      setAuth: (next) => {
        setState(next);
        localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
      },
      clearAuth: () => {
        setState({ token: null, role: null, username: null });
        localStorage.removeItem(STORAGE_KEY);
      }
    }),
    [state]
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) throw new Error("useAuth must be used within AuthProvider");
  return context;
}

