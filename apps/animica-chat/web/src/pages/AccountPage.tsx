import { useAuth } from '../lib/auth';
import { SiteHeader } from '../components/SiteHeader';
import { Link } from 'react-router-dom';

export function AccountPage() {
  const me = useAuth((s) => s.me);
  const logout = useAuth((s) => s.logout);
  if (!me) return null;
  return (
    <div className="min-h-full bg-ink-950 text-ink-50">
      <SiteHeader />
      <main className="mx-auto mt-12 max-w-2xl px-4">
        <h1 className="text-2xl font-semibold">Account</h1>
        <div className="mt-5 space-y-4 rounded-2xl border border-white/8 bg-white/[0.02] p-6 text-sm">
          <Row label="Email" value={me.user.email} />
          <Row label="Role" value={me.user.role} />
          {me.subscription && (
            <Row
              label="Plan"
              value={`${me.subscription.planCode} (${me.subscription.status})`}
            />
          )}
          <div className="flex gap-3 pt-2">
            <Link to="/billing" className="rounded-full border border-white/10 bg-white/5 px-4 py-2 text-xs hover:bg-white/10">
              Billing
            </Link>
            <Link to="/tools" className="rounded-full border border-white/10 bg-white/5 px-4 py-2 text-xs hover:bg-white/10">
              Coding agent
            </Link>
            <button
              type="button"
              onClick={logout}
              className="rounded-full border border-rose-400/40 bg-rose-500/15 px-4 py-2 text-xs text-rose-200 hover:bg-rose-500/25"
            >
              Sign out
            </button>
          </div>
        </div>
      </main>
    </div>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline justify-between gap-3 border-b border-white/5 py-1.5">
      <span className="text-ink-400">{label}</span>
      <span className="text-ink-100">{value}</span>
    </div>
  );
}
