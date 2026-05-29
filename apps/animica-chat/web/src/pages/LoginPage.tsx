import { useState } from 'react';
import { api } from '../lib/api';
import { SiteHeader } from '../components/SiteHeader';
import { useNavigate } from 'react-router-dom';

export function LoginPage() {
  const [email, setEmail] = useState('');
  const [sent, setSent] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const nav = useNavigate();

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setLoading(true);
    setError(null);
    try {
      await api.post('/api/auth/request-link', { email, redirectTo: '/chat' });
      setSent(true);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="min-h-full bg-ink-950 text-ink-50">
      <SiteHeader />
      <main className="mx-auto mt-16 max-w-md px-4">
        <div className="rounded-2xl border border-white/8 bg-white/[0.02] p-6">
          <h1 className="text-xl font-semibold">Sign in to Animica Chat</h1>
          <p className="mt-1 text-sm text-ink-400">
            We email you a one-time link. No password to remember.
          </p>
          {sent ? (
            <div className="mt-5 rounded-lg border border-emerald-500/40 bg-emerald-500/10 p-3 text-sm text-emerald-200">
              Check your email for a sign-in link. Closing this tab is fine —
              the link opens directly into the chat.
              <button
                type="button"
                onClick={() => nav('/')}
                className="ml-2 underline-offset-2 hover:underline"
              >
                Back to home
              </button>
            </div>
          ) : (
            <form onSubmit={onSubmit} className="mt-5 space-y-3">
              <input
                type="email"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="you@example.com"
                className="w-full rounded-lg border border-white/10 bg-ink-900/60 px-3 py-2 text-sm focus:border-accent-500 focus:outline-none"
              />
              {error && (
                <div className="rounded-md border border-rose-500/40 bg-rose-500/10 px-3 py-2 text-xs text-rose-200">
                  {error}
                </div>
              )}
              <button
                type="submit"
                disabled={loading || !email}
                className="w-full rounded-full bg-accent-600 px-4 py-2 text-sm font-medium text-white shadow-glow transition hover:bg-accent-500 disabled:bg-ink-700"
              >
                {loading ? 'Sending…' : 'Email me a sign-in link'}
              </button>
            </form>
          )}
        </div>
      </main>
    </div>
  );
}
