import { createContext, useContext, useEffect, useMemo, useState } from 'react';

type SessionUser = {
  id: string;
  email: string;
  role: 'developer' | 'provider' | 'admin';
  wallet?: {
    address: string;
    chainId: number;
    signature: string;
    linkedAt: string;
  };
};

export type SessionState = {
  token: string;
  user: SessionUser;
  selectedProjectId?: string;
};

type SessionContextType = {
  session: SessionState | null;
  setSession: (value: SessionState | null) => void;
  setSelectedProjectId: (projectId: string | undefined) => void;
};

const SessionContext = createContext<SessionContextType | undefined>(undefined);

const STORAGE_KEY = 'aicf-web-session';

export function SessionProvider({ children }: { children: React.ReactNode }) {
  const [session, setSessionState] = useState<SessionState | null>(null);

  useEffect(() => {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return;
    try {
      const parsed = JSON.parse(raw) as SessionState;
      setSessionState(parsed);
    } catch {
      localStorage.removeItem(STORAGE_KEY);
    }
  }, []);

  const setSession = (value: SessionState | null) => {
    setSessionState(value);
    if (!value) {
      localStorage.removeItem(STORAGE_KEY);
      return;
    }
    localStorage.setItem(STORAGE_KEY, JSON.stringify(value));
  };

  const setSelectedProjectId = (projectId: string | undefined) => {
    setSessionState((prev) => {
      if (!prev) return prev;
      const next = { ...prev, selectedProjectId: projectId };
      localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
      return next;
    });
  };

  const value = useMemo(
    () => ({
      session,
      setSession,
      setSelectedProjectId
    }),
    [session]
  );

  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSession() {
  const context = useContext(SessionContext);
  if (!context) {
    throw new Error('useSession must be used within SessionProvider');
  }
  return context;
}
