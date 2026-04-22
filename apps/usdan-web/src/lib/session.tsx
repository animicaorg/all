import React, { createContext, useContext, useEffect, useMemo, useState } from 'react';
import type { SessionState } from './types';

interface SessionContextValue {
  session: SessionState | null;
  setSession: (session: SessionState | null) => void;
}

const SessionContext = createContext<SessionContextValue | undefined>(undefined);

const STORAGE_KEY = 'usdan_session';

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

  const value = useMemo<SessionContextValue>(
    () => ({
      session,
      setSession: (next) => {
        setSessionState(next);
        if (next) {
          localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
        } else {
          localStorage.removeItem(STORAGE_KEY);
        }
      }
    }),
    [session]
  );

  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSession(): SessionContextValue {
  const ctx = useContext(SessionContext);
  if (!ctx) throw new Error('useSession must be used within SessionProvider');
  return ctx;
}
