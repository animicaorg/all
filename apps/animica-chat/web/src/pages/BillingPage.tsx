import { useEffect, useState } from 'react';
import { api } from '../lib/api';
import { SiteHeader } from '../components/SiteHeader';

interface SubscriptionStatus {
  status: string;
  planName: string;
  priceUsdCents: number;
  currentPeriodEnd: string | null;
  messagesUsedThisPeriod: number;
  weeklyMessages: number;
  currentWeekStart: string | null;
  paypalSubscriptionId?: string;
  paypalStatus?: string;
}

export function BillingPage() {
  const [sub, setSub] = useState<SubscriptionStatus | null | undefined>(undefined);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function refresh() {
    const r = await api.get<{ subscription: SubscriptionStatus | null }>('/api/billing/status');
    setSub(r.subscription);
  }

  useEffect(() => {
    refresh().catch(() => setSub(null));
  }, []);

  async function subscribe() {
    setBusy(true);
    setError(null);
    try {
      const r = await api.post<{ approveUrl: string }>('/api/paypal/create-subscription', {
        planCode: 'pro',
      });
      // PayPal hosts the rest of the flow — full-page redirect (not new tab)
      // so the return / cancel URLs land us back on /billing.
      window.location.href = r.approveUrl;
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  }

  async function cancel() {
    if (!confirm('Cancel your subscription? Access continues until the end of the current period.')) return;
    setBusy(true);
    setError(null);
    try {
      await api.post('/api/billing/cancel');
      await refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const pending = sub?.status === 'PENDING';

  return (
    <div className="min-h-full bg-ink-950 text-ink-50">
      <SiteHeader />
      <main className="mx-auto mt-12 max-w-2xl px-4">
        <h1 className="text-2xl font-semibold">Billing</h1>
        <div className="mt-5 rounded-2xl border border-white/8 bg-white/[0.02] p-6">
          {sub === undefined && <p className="text-ink-400">Loading…</p>}

          {sub === null && (
            <>
              <p className="text-ink-300">You don't have an active subscription yet.</p>
              <button
                type="button"
                onClick={subscribe}
                disabled={busy}
                className="mt-4 rounded-full bg-accent-600 px-6 py-2 text-sm font-medium text-white shadow-glow hover:bg-accent-500 disabled:bg-ink-700"
              >
                {busy ? 'Opening PayPal…' : 'Subscribe ($10 / month)'}
              </button>
            </>
          )}

          {sub && pending && (
            <>
              <div className="rounded-xl border border-yellow-500/40 bg-yellow-500/10 p-4 text-sm">
                <p className="font-semibold text-yellow-100">Checkout incomplete</p>
                <p className="mt-1 text-yellow-200/80">
                  Your last PayPal checkout wasn't completed yet. You can finish it or start a fresh
                  one below — your subscription only activates once PayPal confirms the first payment.
                </p>
              </div>
              <div className="mt-4 flex flex-wrap gap-2">
                <button
                  type="button"
                  onClick={subscribe}
                  disabled={busy}
                  className="rounded-full bg-accent-600 px-5 py-2 text-sm font-medium text-white shadow-glow hover:bg-accent-500 disabled:bg-ink-700"
                >
                  {busy ? 'Opening PayPal…' : 'Retry checkout'}
                </button>
                {sub.paypalSubscriptionId && (
                  <span className="self-center text-xs text-ink-500">
                    Previous attempt: <span className="font-mono">{sub.paypalSubscriptionId.slice(0, 14)}…</span>
                  </span>
                )}
              </div>
            </>
          )}

          {sub && !pending && (
            <div className="space-y-3 text-sm">
              <Row label="Plan" value={sub.planName} />
              <Row label="Status" value={sub.status} />
              <Row label="Price" value={`$${(sub.priceUsdCents / 100).toFixed(2)} / month`} />
              <Row
                label="Quota window ends"
                value={
                  sub.currentWeekStart
                    ? new Date(new Date(sub.currentWeekStart).getTime() + 7 * 24 * 60 * 60_000).toLocaleString()
                    : 'starts on first message'
                }
              />
              <Row
                label="Next PayPal bill"
                value={sub.currentPeriodEnd ? new Date(sub.currentPeriodEnd).toLocaleString() : '—'}
              />
              <Row
                label="Usage this week"
                value={`${sub.messagesUsedThisPeriod} / ${sub.weeklyMessages} messages`}
              />
              {sub.paypalSubscriptionId && <Row label="PayPal id" value={sub.paypalSubscriptionId} />}
              {sub.status === 'PAST_DUE' && (
                <p className="rounded-md border border-yellow-500/40 bg-yellow-500/10 p-2 text-xs text-yellow-200">
                  Payment is past-due. PayPal will retry; your access continues for now.
                </p>
              )}
              {sub.status === 'ACTIVE' && (
                <button
                  type="button"
                  onClick={cancel}
                  disabled={busy}
                  className="mt-3 rounded-full border border-rose-400/40 bg-rose-500/15 px-4 py-2 text-xs text-rose-200 hover:bg-rose-500/25 disabled:opacity-50"
                >
                  Cancel subscription
                </button>
              )}
            </div>
          )}

          {error && (
            <p className="mt-3 rounded-md border border-rose-500/40 bg-rose-500/10 px-3 py-2 text-xs text-rose-200">
              {error}
            </p>
          )}
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
