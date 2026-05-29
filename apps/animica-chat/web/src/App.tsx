import { useEffect } from 'react';
import { Navigate, Route, Routes, useLocation } from 'react-router-dom';
import { HomePage } from './pages/HomePage';
import { ChatPage } from './pages/ChatPage';
import { LoginPage } from './pages/LoginPage';
import { PricingPage } from './pages/PricingPage';
import { BillingPage } from './pages/BillingPage';
import { AccountPage } from './pages/AccountPage';
import { AdminPage } from './pages/AdminPage';
import { ToolsPage } from './pages/ToolsPage';
import { useAuth } from './lib/auth';

function Protected({ children, adminOnly = false }: { children: React.ReactNode; adminOnly?: boolean }) {
  const me = useAuth((s) => s.me);
  const ready = useAuth((s) => s.ready);
  const loc = useLocation();
  // Wait for the initial /api/auth/me probe to settle before deciding —
  // otherwise we bounce signed-in users to /login during the millisecond
  // gap on first mount (most visibly after the GitHub OAuth callback).
  if (!ready) {
    return (
      <div className="grid min-h-full place-items-center bg-ink-950 text-ink-500">
        <div className="text-xs">Loading…</div>
      </div>
    );
  }
  if (me === null) return <Navigate to={`/login?redirectTo=${encodeURIComponent(loc.pathname)}`} replace />;
  if (me && adminOnly && me.user.role !== 'ADMIN') return <Navigate to="/" replace />;
  return <>{children}</>;
}

// Wrapper for /login: if the user is already signed in, bounce them to
// `?redirectTo=` (or /chat) so they never see a sign-in form they don't
// need. Anyone who genuinely wants to switch accounts has to sign out
// first (via the header menu).
function PublicOnly({ children }: { children: React.ReactNode }) {
  const me = useAuth((s) => s.me);
  const ready = useAuth((s) => s.ready);
  const loc = useLocation();
  if (!ready) {
    return (
      <div className="grid min-h-full place-items-center bg-ink-950 text-ink-500">
        <div className="text-xs">Loading…</div>
      </div>
    );
  }
  if (me) {
    const params = new URLSearchParams(loc.search);
    const dest = params.get('redirectTo') || '/chat';
    return <Navigate to={dest} replace />;
  }
  return <>{children}</>;
}

export default function App() {
  const refresh = useAuth((s) => s.refresh);
  useEffect(() => { refresh(); }, [refresh]);
  return (
    <Routes>
      <Route path="/" element={<HomePage />} />
      <Route path="/pricing" element={<PricingPage />} />
      <Route path="/login" element={<PublicOnly><LoginPage /></PublicOnly>} />
      <Route path="/chat" element={<Protected><ChatPage /></Protected>} />
      <Route path="/chat/:conversationId" element={<Protected><ChatPage /></Protected>} />
      <Route path="/billing" element={<Protected><BillingPage /></Protected>} />
      <Route path="/account" element={<Protected><AccountPage /></Protected>} />
      <Route path="/tools" element={<Protected><ToolsPage /></Protected>} />
      <Route path="/admin" element={<Protected adminOnly><AdminPage /></Protected>} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
