'use client';

import { useCallback, useEffect, useState } from 'react';
import { connectAndLogin, hasWallet } from '@/components/wallet';
import { planName, PLAN_PRICE_CENTS } from '@/components/PlanUpsell';

// Billing settings (DashboardClient pattern): wallet gate → GET /billing/summary → current
// plan card + state banner, usage meters, over-limit "choose what stays active" pickers
// (POST /billing/keep) and subscription history. Plan changes route through /pricing; the
// only destructive action here is Cancel, which shelves — never deletes — resources.

const API = '/api/mkt/v1';

interface ApiErr extends Error { status?: number; code?: string; details?: any }

async function api(path: string, init?: RequestInit): Promise<any> {
  const res = await fetch(`${API}${path}`, {
    credentials: 'include',
    ...init,
    headers: { 'content-type': 'application/json', ...(init?.headers as any) },
  });
  const j = await res.json().catch(() => ({}));
  if (!res.ok) {
    const e = new Error(j?.error?.message || `request failed (${res.status})`) as ApiErr;
    e.status = res.status;
    e.code = j?.error?.code;
    e.details = j?.error?.details;
    throw e;
  }
  return j;
}

interface Meter { used: number; limit: number }
interface Summary {
  plan: { key: string; subscribedKey: string; state: string; billingWarning: boolean; limits: Record<string, number | boolean> };
  subscription: { id: string; planKey: string; status: string; currentPeriodEnd: string | null; graceUntil: string | null; paypalSubscriptionId: string | null } | null;
  usage: Record<string, Meter>;
  period: string;
  history: Array<{
    id: string; planKey: string; status: string; priceUsdCents: number; payerEmail: string | null;
    currentPeriodEnd: string | null; lastPaymentAt: string | null; lastPaymentAmountCents: number | null;
    canceledAt: string | null; createdAt: string;
  }>;
}

const STATE_COLOR: Record<string, string> = {
  FREE: 'var(--text-faint)', ACTIVE: 'var(--good)', PAST_DUE: 'var(--warn)',
  GRACE_PERIOD: 'var(--warn)', SUSPENDED: 'var(--bad)', CANCELED: 'var(--bad)', PENDING: 'var(--text-faint)',
};

const STATE_BANNER: Record<string, string> = {
  PAST_DUE:
    'Your last payment failed. Your paid limits are still active, but creating new paid resources is blocked until the payment clears.',
  GRACE_PERIOD:
    'Your subscription is in its grace period — if payment keeps failing it will be suspended and your account drops to Free limits.',
  SUSPENDED:
    'Your subscription is suspended: Free limits apply and Workers are paused. Nothing was deleted — everything resumes when billing is fixed.',
  CANCELED:
    'Your subscription is cancelled. Paid access continues until the end of the period you already paid for, then Free limits apply.',
};

function fmtDate(s: string | null | undefined): string {
  if (!s) return '—';
  try { return new Date(s).toLocaleDateString(); } catch { return String(s); }
}

function usd2(cents: number): string {
  return `$${(cents / 100).toFixed(2)}`;
}

function num(n: number): string {
  return n.toLocaleString('en-US');
}

export default function BillingClient() {
  const [state, setState] = useState<'loading' | 'out' | 'in'>('loading');
  const [summary, setSummary] = useState<Summary | null>(null);
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState('');

  const load = useCallback(async () => {
    setErr('');
    try {
      const j = await api('/billing/summary');
      setSummary(j);
      setState('in');
    } catch (e: any) {
      if (e?.status === 401 || e?.status === 403) setState('out');
      else { setErr(e?.message ?? 'failed to load'); setState('out'); }
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const connect = useCallback(async () => {
    setErr(''); setBusy(true);
    try {
      await connectAndLogin();
      await load();
    } catch (e: any) {
      setErr(e?.message ?? 'connect failed');
    } finally { setBusy(false); }
  }, [load]);

  const cancel = useCallback(async () => {
    if (!window.confirm(
      'Cancel your subscription? You keep paid access until the end of the period you already paid for. ' +
      'Nothing you built is deleted — over-limit resources are paused and you can choose which stay active below.',
    )) return;
    setBusy(true); setErr(''); setNotice('');
    try {
      await api('/billing/cancel', { method: 'POST' });
      setNotice('Subscription cancelled. Paid access continues until the end of your current billing period.');
      await load();
    } catch (e: any) {
      setErr(e?.message ?? 'cancel failed');
    } finally { setBusy(false); }
  }, [load]);

  if (state === 'loading') {
    return <div className="wrap" style={{ paddingTop: 60 }}><div className="empty">Loading billing…</div></div>;
  }

  if (state === 'out') {
    const installed = hasWallet();
    return (
      <div className="wrap billing-page" style={{ paddingTop: 50, paddingBottom: 60 }}>
        <div style={{ maxWidth: 520, margin: '0 auto', textAlign: 'center' }}>
          <div className="pill" style={{ marginBottom: 14 }}>💳 Billing &amp; plan</div>
          <h1 style={{ fontSize: 32, letterSpacing: '-0.03em', margin: '0 0 10px' }}>Your Animica plan</h1>
          <p className="muted" style={{ fontSize: 15, lineHeight: 1.55 }}>
            Sign in with your Animica wallet to see your plan, usage meters and billing history. Plans attach
            to your wallet identity — the account that owns your Workers, keys and .anm names.
          </p>
          <div className="panel" style={{ marginTop: 22, textAlign: 'left' }}>
            {installed ? (
              <button className="btn primary" onClick={connect} disabled={busy}>
                {busy ? 'Waiting for wallet…' : 'Connect wallet & sign in'}
              </button>
            ) : (
              <>
                <p className="muted" style={{ margin: '0 0 12px', fontSize: 13.5 }}>
                  No Animica wallet detected. Install the <a className="inline" href="/browser">browser extension</a>{' '}
                  or open this page in the mobile wallet&apos;s dapp browser, then retry.
                </p>
                <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
                  <a className="btn primary" href="/browser">Get the extension</a>
                  <button className="btn" onClick={load}>I&apos;ve installed it — retry</button>
                </div>
              </>
            )}
            {err && <div className="billing-err">{err}</div>}
          </div>
          <p className="muted" style={{ fontSize: 13, marginTop: 16 }}>
            Just browsing? See <a className="mono" href="/pricing">what each plan includes →</a>
          </p>
        </div>
        <Style />
      </div>
    );
  }

  const s = summary!;
  const shownKey = s.plan.subscribedKey;
  // The current sub's real price lives on its history row; hardcoded map is display fallback.
  const currentRow = s.subscription ? s.history.find((h) => h.id === s.subscription!.id) : undefined;
  const priceCents = currentRow?.priceUsdCents ?? PLAN_PRICE_CENTS[shownKey] ?? 0;
  const banner = STATE_BANNER[s.plan.state];
  const hasLiveSub = !!s.subscription && !['CANCELED', 'PENDING'].includes(String(s.subscription.status));

  return (
    <div className="wrap billing-page" style={{ paddingTop: 36, paddingBottom: 60 }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 12, flexWrap: 'wrap' }}>
        <h1 style={{ fontSize: 30, letterSpacing: '-0.03em', margin: 0 }}>Billing &amp; plan</h1>
        <div className="muted">subscription · usage · history</div>
        <div style={{ marginLeft: 'auto' }}>
          <a className="btn ghost" href="/pricing" style={{ fontSize: 13 }}>Compare plans →</a>
        </div>
      </div>

      {notice && <div className="billing-okbox" style={{ marginTop: 16 }}>{notice}</div>}
      {err && <div className="billing-err" style={{ marginTop: 16 }}>{err}</div>}

      {/* current plan */}
      <div className="panel" style={{ marginTop: 18 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
          <div style={{ minWidth: 0 }}>
            <div className="muted" style={{ fontSize: 12.5 }}>Current plan</div>
            <h2 style={{ fontSize: 24, letterSpacing: '-0.02em', margin: '2px 0 0' }}>
              {planName(shownKey)}
              <span className="muted" style={{ fontSize: 15, fontWeight: 500, marginLeft: 10 }}>
                {priceCents > 0 ? `${usd2(priceCents)}/month` : 'free forever'}
              </span>
            </h2>
          </div>
          <span
            className="pill"
            style={{ color: STATE_COLOR[s.plan.state] ?? 'var(--text-dim)', borderColor: STATE_COLOR[s.plan.state] ?? 'var(--border)', fontWeight: 600 }}
          >
            {s.plan.state.replace(/_/g, ' ').toLowerCase()}
          </span>
          <div style={{ marginLeft: 'auto', display: 'flex', gap: 10, flexWrap: 'wrap' }}>
            <a className="btn primary" href="/pricing">{shownKey === 'free' ? 'Upgrade' : 'Upgrade / downgrade'}</a>
            {hasLiveSub && (
              <button className="btn billing-danger" onClick={cancel} disabled={busy}>Cancel subscription</button>
            )}
          </div>
        </div>
        {banner && (
          <div className="billing-warnbox">
            {banner}
            <div className="muted" style={{ fontSize: 12.5, marginTop: 6 }}>
              Manage: fix the payment method in your PayPal account (Settings → Payments → Automatic payments)
              — a successful charge restores your plan automatically. Something else wrong? Reach the operator
              from <a className="inline" href="/hire">/hire</a>.
            </div>
          </div>
        )}
        <div className="billing-row" style={{ marginTop: 12 }}>
          {s.subscription?.currentPeriodEnd && (
            <span><span className="k">{s.plan.state === 'CANCELED' ? 'Access until' : 'Renews'}</span>{fmtDate(s.subscription.currentPeriodEnd)}</span>
          )}
          {s.subscription?.paypalSubscriptionId && (
            <span><span className="k">PayPal subscription</span><span className="mono">{s.subscription.paypalSubscriptionId}</span></span>
          )}
          <span><span className="k">Usage period</span><span className="mono">{s.period}</span> (resets on the 1st, UTC)</span>
        </div>
      </div>

      {/* usage meters */}
      <section className="section" style={{ paddingBottom: 10 }}>
        <h2>Usage</h2>
        <div className="sub">What your {planName(s.plan.key)} limits cover right now.</div>
        <div className="panel" style={{ padding: '8px 22px' }}>
          <MeterRow label="Workers" m={s.usage.workers} />
          <MeterRow label="Scheduled Executions" m={s.usage.scheduled_executions} suffix=" this month" />
          <MeterRow label=".anm Deployments" m={s.usage.anm_deployments} />
          <MeterRow label="Production API Keys" m={s.usage.api_keys} />
          <MeterRow label="Team Members" m={s.usage.team_members} />
        </div>
      </section>

      {/* over-limit pickers */}
      <OverLimitPicker kind="workers" label="Workers" meter={s.usage.workers} onKept={load} />
      <OverLimitPicker kind="deployments" label=".anm deployments" meter={s.usage.anm_deployments} onKept={load} />
      <OverLimitPicker kind="api_keys" label="production API keys" meter={s.usage.api_keys} onKept={load} />

      {/* history */}
      <section className="section">
        <h2>Subscription history</h2>
        <div className="sub">Your last {s.history.length} subscription record{s.history.length === 1 ? '' : 's'}.</div>
        {s.history.length === 0 ? (
          <div className="empty">No subscriptions yet — you&apos;re on the free plan.</div>
        ) : (
          <div className="panel" style={{ padding: 0, overflowX: 'auto' }}>
            <table className="billing-table">
              <thead>
                <tr><th>Plan</th><th>Status</th><th>Price</th><th>Started</th><th>Period end</th><th>Last payment</th></tr>
              </thead>
              <tbody>
                {s.history.map((h) => (
                  <tr key={h.id}>
                    <td>{planName(h.planKey)}</td>
                    <td>
                      <span className="pill" style={{ color: STATE_COLOR[h.status] ?? 'var(--text-dim)', borderColor: STATE_COLOR[h.status] ?? 'var(--border)', fontSize: 11 }}>
                        {h.status.replace(/_/g, ' ').toLowerCase()}
                      </span>
                    </td>
                    <td>{usd2(h.priceUsdCents)}/mo</td>
                    <td>{fmtDate(h.createdAt)}</td>
                    <td>{fmtDate(h.currentPeriodEnd)}</td>
                    <td>{h.lastPaymentAt ? `${fmtDate(h.lastPaymentAt)}${h.lastPaymentAmountCents ? ` · ${usd2(h.lastPaymentAmountCents)}` : ''}` : '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <Style />
    </div>
  );
}

// One usage meter: label, "used / limit" and a thin progress bar (warn-colored once over).
function MeterRow({ label, m, suffix = '' }: { label: string; m: Meter | undefined; suffix?: string }) {
  if (!m) return null;
  const unlimited = m.limit === -1;
  const over = !unlimited && m.used > m.limit;
  const pct = unlimited ? 0 : m.limit > 0 ? Math.min(100, (m.used / m.limit) * 100) : m.used > 0 ? 100 : 0;
  return (
    <div className="billing-meter">
      <div className="kpi" style={{ gap: 12, alignItems: 'baseline' }}>
        <div className="k" style={{ minWidth: 190 }}>
          <b style={{ fontSize: 15 }}>{label}</b>
        </div>
        <span style={{ fontSize: 14, fontWeight: 600, color: over ? 'var(--warn)' : 'var(--text)' }}>
          {num(m.used)} / {unlimited ? '∞' : num(m.limit)}
          <span className="muted" style={{ fontWeight: 400 }}>{suffix}</span>
        </span>
        {over && <span className="pill" style={{ color: 'var(--warn)', borderColor: 'var(--warn)', fontSize: 11 }}>over limit</span>}
      </div>
      <div className="billing-bar">
        <div
          className="billing-bar-fill"
          style={{
            width: `${pct}%`,
            background: over ? 'var(--warn)' : 'linear-gradient(90deg, var(--accent), var(--accent-2))',
          }}
        />
      </div>
    </div>
  );
}

// After a downgrade the enforcement pass keeps the OLDEST resources active; this picker lets
// the user swap which ones stay active instead (POST /billing/keep — everything not kept is
// shelved, never deleted). Only rendered while a kind is actually over its limit.
function OverLimitPicker({
  kind, label, meter, onKept,
}: {
  kind: 'workers' | 'deployments' | 'api_keys';
  label: string;
  meter: Meter | undefined;
  onKept: () => void;
}) {
  const over = !!meter && meter.limit !== -1 && meter.limit >= 0 && meter.used > meter.limit;
  const limit = meter?.limit ?? 0;
  const [items, setItems] = useState<Array<{ id: string; name: string; active: boolean }> | null>(null);
  const [checked, setChecked] = useState<Record<string, boolean>>({});
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!over) { setItems(null); return; }
    (async () => {
      try {
        if (kind === 'workers') {
          const j = await api('/workers');
          // Only workers you OWN occupy your plan's slots — workspace-shared ones belong to
          // their owner's plan, so offering them here would just fail on save.
          setItems((j.workers ?? [])
            .filter((w: any) => w.owned !== false)
            .map((w: any) => ({
              id: w.id, name: w.name || w.id, active: w.status !== 'PLAN_LIMIT',
            })));
        } else if (kind === 'deployments') {
          const j = await api('/names/mine');
          setItems((j.domains ?? []).map((d: any) => ({
            id: d.id, name: `${d.name}.anm`, active: d.status === 'ACTIVE',
          })));
        } else {
          const j = await api('/keys');
          setItems((j.keys ?? []).filter((k: any) => k.kind === 'production').map((k: any) => ({
            id: k.id, name: `${k.name} (${k.prefix}…)`, active: (k.status ?? 'ACTIVE') === 'ACTIVE',
          })));
        }
      } catch (e: any) {
        setErr(e?.message ?? 'failed to load');
      }
    })();
  }, [over, kind]);

  // Pre-check the currently-active ones, capped at the limit.
  useEffect(() => {
    if (!items) return;
    const next: Record<string, boolean> = {};
    let n = 0;
    for (const it of items) {
      if (it.active && n < limit) { next[it.id] = true; n++; }
    }
    setChecked(next);
  }, [items, limit]);

  if (!over || !items || items.length === 0) return null;
  const nChecked = Object.values(checked).filter(Boolean).length;

  const save = async () => {
    setBusy(true); setErr('');
    try {
      const keepIds = items.filter((it) => checked[it.id]).map((it) => it.id);
      await api('/billing/keep', { method: 'POST', body: JSON.stringify({ kind, keepIds }) });
      onKept();
    } catch (e: any) {
      setErr(e?.message ?? 'save failed');
    } finally { setBusy(false); }
  };

  return (
    <div className="panel" style={{ marginTop: 14, borderColor: 'rgba(255,180,84,0.45)' }}>
      <h3 style={{ fontSize: 16, margin: 0 }}>Choose which {label} stay active</h3>
      <p className="muted" style={{ fontSize: 13, margin: '6px 0 10px', lineHeight: 1.5 }}>
        You have more {label} than your plan includes ({num(limit)}). Pick up to {num(limit)} to keep active —
        the rest are paused, not deleted, and come back if you upgrade.
      </p>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 7 }}>
        {items.map((it) => {
          const isChecked = !!checked[it.id];
          const disabled = !isChecked && nChecked >= limit;
          return (
            <label key={it.id} style={{ display: 'flex', alignItems: 'center', gap: 9, fontSize: 13.5, cursor: disabled ? 'not-allowed' : 'pointer', opacity: disabled ? 0.55 : 1 }}>
              <input
                type="checkbox"
                checked={isChecked}
                disabled={disabled}
                onChange={(e) => setChecked((c) => ({ ...c, [it.id]: e.target.checked }))}
              />
              <span className="mono" style={{ fontSize: 13 }}>{it.name}</span>
              {!it.active && <span className="pill" style={{ fontSize: 10.5, color: 'var(--text-faint)' }}>paused</span>}
            </label>
          );
        })}
      </div>
      {err && <div className="billing-err">{err}</div>}
      <button className="btn primary" style={{ marginTop: 12 }} onClick={save} disabled={busy || nChecked > limit}>
        {busy ? 'Saving…' : `Keep ${num(nChecked)} active`}
      </button>
    </div>
  );
}

function Style() {
  return (
    <style>{`
      /* globals.css has no button reset; without this, UA styles paint button text black on dark. */
      .billing-page button { font-family: var(--font); color: var(--text); }
      .billing-page button:disabled { opacity: 0.6; cursor: default; }
      .billing-danger { border-color: rgba(255,92,114,0.4); color: var(--bad) !important; }
      .billing-err { border: 1px solid rgba(255,92,114,0.45); background: rgba(255,92,114,0.08); color: var(--bad);
        border-radius: 10px; padding: 10px 12px; font-size: 13.5px; margin-top: 12px; }
      .billing-okbox { border: 1px solid rgba(36,209,139,0.4); background: rgba(36,209,139,0.07); border-radius: 10px; padding: 12px; font-size: 14px; }
      .billing-warnbox { border: 1px solid rgba(255,180,84,0.45); background: rgba(255,180,84,0.07); color: var(--text);
        border-radius: 10px; padding: 12px 14px; font-size: 13.5px; margin-top: 14px; line-height: 1.5; }
      .billing-row { display: flex; gap: 8px 20px; flex-wrap: wrap; font-size: 13.5px; }
      .billing-row .k { color: var(--text-faint); margin-right: 5px; }
      .billing-meter { padding: 13px 0; border-bottom: 1px solid var(--border); }
      .billing-meter:last-child { border-bottom: 0; }
      .billing-bar { height: 4px; border-radius: 999px; background: var(--bg-elev); margin-top: 9px; overflow: hidden; }
      .billing-bar-fill { height: 100%; border-radius: 999px; transition: width .3s; }
      .billing-table { width: 100%; border-collapse: collapse; font-size: 13.5px; min-width: 640px; }
      .billing-table th { text-align: left; color: var(--text-faint); font-size: 12px; font-weight: 600;
        padding: 12px 16px; border-bottom: 1px solid var(--border); }
      .billing-table td { padding: 11px 16px; border-bottom: 1px solid var(--border); }
      .billing-table tr:last-child td { border-bottom: 0; }
    `}</style>
  );
}
