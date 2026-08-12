'use client';
import { Fragment, useCallback, useEffect, useState } from 'react';

// Billing operator console (/admin/billing). Three surfaces over /api/mkt/v1/billing/admin/*:
//   Subscribers — searchable subscriber ledger w/ per-row usage, row-expand detail (webhook
//                 audit trail + recent Worker runs) and suspend/reactivate/cancel actions;
//   Analytics   — MRR/ARPU, tier mix, 30d funnel, Worker adoption, upgrade/downgrade paths;
//   Engine      — the Workers engine kill switch (WorkerEngineState singleton) + queue depth.
// Auth mirrors lib/adminAuth.ts requireAdmin(): the MKT_ADMIN_TOKEN via the x-admin-token
// header (held in sessionStorage under the same key as the store console, so one paste covers
// both) OR an ADMIN-role wallet session cookie sent automatically. PayPal secrets never reach
// this page — rows carry only the subscription snapshot (I-… id, payer email, cents).

const API = '/api/mkt/v1/billing/admin';
const PLAN_KEYS = ['free', 'starter', 'pro', 'operator', 'business'] as const;
const SUB_STATUSES = ['PENDING', 'ACTIVE', 'PAST_DUE', 'GRACE_PERIOD', 'SUSPENDED', 'CANCELED'] as const;
const TAKE = 50;

const STATUS_COLOR: Record<string, string> = {
  // subscription states
  ACTIVE: 'var(--good)',
  PAST_DUE: 'var(--warn)',
  GRACE_PERIOD: 'var(--warn)',
  SUSPENDED: 'var(--bad)',
  CANCELED: 'var(--text-faint)',
  PENDING: 'var(--accent-2)',
  // worker-run states (detail panel)
  DONE: 'var(--good)',
  RUNNING: 'var(--accent-2)',
  FAILED: 'var(--bad)',
  TIMEOUT: 'var(--bad)',
  CANCELLED: 'var(--text-faint)',
};

function StatusPill({ status }: { status: string }) {
  const c = STATUS_COLOR[status] ?? 'var(--text-dim)';
  return (
    <span className="pill" style={{ color: c, borderColor: c, fontWeight: 600, fontSize: 11 }}>
      {status.replace(/_/g, ' ').toLowerCase()}
    </span>
  );
}

// ── local formatting ─────────────────────────────────────────────────────────
function fmtUsd(cents: number | null | undefined): string {
  if (cents == null || !isFinite(Number(cents))) return '—';
  return '$' + (Number(cents) / 100).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}
function fmtDate(s?: string | null): string {
  if (!s) return '—';
  try { return new Date(s).toLocaleDateString(); } catch { return String(s); }
}
function fmtDateTime(s?: string | null): string {
  if (!s) return '—';
  try { return new Date(s).toLocaleString(); } catch { return String(s); }
}
function shortAddr(a?: string | null): string {
  if (!a) return '—';
  return a.length > 22 ? `${a.slice(0, 12)}…${a.slice(-6)}` : a;
}
function pct(v: number | null | undefined): string {
  return `${((v ?? 0) * 100).toFixed(1)}%`;
}

// ── shared fetch: attach x-admin-token when present, always send cookies ──────
type AdminFetch = (path: string, init?: RequestInit) => Promise<Response>;

function useAdmin() {
  const [token, setToken] = useState('');
  useEffect(() => {
    try { setToken(sessionStorage.getItem('anm_store_admin_token') ?? ''); } catch { /* no storage */ }
  }, []);
  const saveToken = useCallback((t: string) => {
    setToken(t);
    try { t ? sessionStorage.setItem('anm_store_admin_token', t) : sessionStorage.removeItem('anm_store_admin_token'); } catch { /* ignore */ }
  }, []);
  const adminFetch = useCallback<AdminFetch>((path, init) => {
    const headers: Record<string, string> = { ...(init?.headers as Record<string, string> | undefined) };
    if (token) headers['x-admin-token'] = token;
    return fetch(path, { ...init, headers });
  }, [token]);
  return { token, saveToken, adminFetch };
}

async function readErr(r: Response): Promise<string> {
  try { const d = await r.json(); return d?.error?.message ?? `HTTP ${r.status}`; }
  catch { return `HTTP ${r.status}`; }
}

// ── subscriber row-expand detail ─────────────────────────────────────────────
function SubDetail({ row, adminFetch, onChanged }: { row: any; adminFetch: AdminFetch; onChanged: () => void }) {
  const [detail, setDetail] = useState<any>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [statusSel, setStatusSel] = useState<string>(row.status);
  const [note, setNote] = useState('');

  const load = useCallback(async () => {
    try {
      const r = await adminFetch(`${API}/subscriptions/${row.id}`);
      if (!r.ok) throw new Error(await readErr(r));
      setDetail(await r.json());
    } catch (e: any) { setMsg({ ok: false, text: e.message }); }
  }, [adminFetch, row.id]);
  useEffect(() => { load(); }, [load]);

  async function act(action: string, extra: Record<string, unknown> = {}, confirmText?: string) {
    if (confirmText && !window.confirm(confirmText)) return;
    setBusy(action); setMsg(null);
    try {
      const r = await adminFetch(`${API}/subscriptions/${row.id}`, {
        method: 'POST', headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ action, ...extra }),
      });
      if (!r.ok) throw new Error(await readErr(r));
      const d = await r.json();
      setMsg({ ok: true, text: d.warning ? `${action} done — ⚠ ${d.warning}` : `${action} done` });
      load(); onChanged();
    } catch (e: any) { setMsg({ ok: false, text: e.message }); }
    finally { setBusy(null); }
  }

  const sub = detail?.subscription ?? row;
  const events: any[] = detail?.events ?? [];
  const runs: any[] = detail?.runs ?? [];

  return (
    <div className="badm-detail">
      <div className="badm-facts">
        <div className="badm-fact"><span className="k">account</span><span className="mono">{sub.account?.address ?? row.account?.address ?? '—'}</span></div>
        <div className="badm-fact"><span className="k">paypal sub</span><span className="mono">{sub.paypalSubscriptionId ?? '—'}</span></div>
        <div className="badm-fact"><span className="k">row id</span><span className="mono">{sub.id}</span></div>
        <div className="badm-fact"><span className="k">created</span><span>{fmtDateTime(sub.createdAt)}</span></div>
        <div className="badm-fact"><span className="k">period end</span><span>{fmtDateTime(sub.currentPeriodEnd)}</span></div>
        <div className="badm-fact"><span className="k">grace until</span><span>{fmtDateTime(sub.graceUntil)}</span></div>
        <div className="badm-fact"><span className="k">last payment</span><span>{sub.lastPaymentAt ? `${fmtUsd(sub.lastPaymentAmountCents)} · ${fmtDateTime(sub.lastPaymentAt)}` : '—'}</span></div>
        <div className="badm-fact"><span className="k">canceled at</span><span>{fmtDateTime(sub.canceledAt)}</span></div>
      </div>
      {sub.lastError && <div className="badm-warnbox">note / last error: {sub.lastError}</div>}

      {/* actions */}
      <div className="badm-actions">
        <button className="btn" disabled={!!busy}
          onClick={() => act('suspend', {}, 'Suspend this subscription? PayPal billing is suspended (soft-fail) and entitlements drop to free limits on the next enforcement pass.')}
          style={{ borderColor: 'rgba(255,180,84,0.45)', color: 'var(--warn)' }}>
          {busy === 'suspend' ? 'Suspending…' : 'Suspend'}
        </button>
        <button className="btn" disabled={!!busy}
          onClick={() => act('reactivate', {}, 'Reactivate this subscription? PayPal billing resumes and the row returns to ACTIVE with the dunning window cleared.')}
          style={{ borderColor: 'rgba(36,209,139,0.45)', color: 'var(--good)' }}>
          {busy === 'reactivate' ? 'Reactivating…' : 'Reactivate'}
        </button>
        <button className="btn" disabled={!!busy}
          onClick={() => act('cancel', {}, 'Cancel this subscription? Terminal for this row — PayPal stops billing and the account drops to free at period end.')}
          style={{ borderColor: 'rgba(255,92,114,0.45)', color: 'var(--bad)' }}>
          {busy === 'cancel' ? 'Cancelling…' : 'Cancel'}
        </button>
        <span className="badm-inline">
          <select className="badm-input" value={statusSel} onChange={(e) => setStatusSel(e.target.value)}>
            {SUB_STATUSES.map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
          <button className="btn ghost" disabled={!!busy || statusSel === sub.status}
            onClick={() => act('set_status', { status: statusSel }, `Force status ${statusSel}? Escape hatch — bypasses PayPal entirely.`)}>
            Set status
          </button>
        </span>
      </div>
      <div className="badm-inline" style={{ marginTop: 8 }}>
        <input className="badm-input" style={{ flex: 1, minWidth: 220 }} value={note}
          onChange={(e) => setNote(e.target.value)}
          placeholder="operator note (stored on the row; empty note clears)" />
        <button className="btn ghost" disabled={!!busy}
          onClick={() => { act('note', { note: note.trim() }); setNote(''); }}>
          Save note
        </button>
      </div>
      {msg && <div style={{ marginTop: 8, fontSize: 12.5, color: msg.ok ? 'var(--good)' : 'var(--bad)' }}>{msg.text}</div>}

      {/* webhook audit trail */}
      <div className="badm-sub-h">Billing events</div>
      {events.length === 0 ? (
        <div className="muted" style={{ fontSize: 12.5 }}>No webhook deliveries recorded{sub.paypalSubscriptionId ? '' : ' (no PayPal subscription linked)'}.</div>
      ) : (
        events.map((ev) => (
          <div key={ev.id} className="badm-event">
            <span className="muted" style={{ flex: 'none', width: 150 }}>{fmtDateTime(ev.processedAt)}</span>
            <span className="mono" style={{ flex: 'none', fontSize: 12 }}>{ev.eventType}</span>
            <span className="muted badm-ellipsis">{ev.summary ?? ''}</span>
          </div>
        ))
      )}

      {/* recent worker runs */}
      <div className="badm-sub-h">Recent Worker runs</div>
      {runs.length === 0 ? (
        <div className="muted" style={{ fontSize: 12.5 }}>No Worker runs for this account.</div>
      ) : (
        runs.map((run) => (
          <div key={run.id} className="badm-event">
            <span className="muted" style={{ flex: 'none', width: 150 }}>{fmtDateTime(run.createdAt)}</span>
            <span style={{ flex: 'none', fontWeight: 600 }}>{run.worker?.name ?? '—'}</span>
            <StatusPill status={run.status} />
            <span className="muted badm-ellipsis">
              {run.trigger}
              {run.durationMs != null ? ` · ${(run.durationMs / 1000).toFixed(1)}s` : ''}
              {run.error ? ` · ${String(run.error).slice(0, 100)}` : ''}
            </span>
          </div>
        ))
      )}
    </div>
  );
}

// ── Subscribers tab ──────────────────────────────────────────────────────────
function SubscribersSection({ adminFetch }: { adminFetch: AdminFetch }) {
  const [q, setQ] = useState('');
  const [qApplied, setQApplied] = useState('');
  const [plan, setPlan] = useState('');
  const [status, setStatus] = useState('');
  const [rows, setRows] = useState<any[]>([]);
  const [total, setTotal] = useState(0);
  const [skip, setSkip] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [openId, setOpenId] = useState<string | null>(null);

  const load = useCallback(async (nextSkip: number) => {
    setLoading(true); setError('');
    try {
      const p = new URLSearchParams({ take: String(TAKE), skip: String(nextSkip) });
      if (qApplied) p.set('q', qApplied);
      if (plan) p.set('plan', plan);
      if (status) p.set('status', status);
      const r = await adminFetch(`${API}/subscriptions?${p}`);
      if (!r.ok) throw new Error(await readErr(r));
      const d = await r.json();
      setRows(d.rows ?? []); setTotal(d.totals?.count ?? 0); setSkip(nextSkip);
    } catch (e: any) { setError(e.message); setRows([]); setTotal(0); }
    finally { setLoading(false); }
  }, [adminFetch, qApplied, plan, status]);

  useEffect(() => { setOpenId(null); load(0); }, [load]);

  return (
    <>
      {/* filters */}
      <div className="panel" style={{ marginTop: 16, padding: 14 }}>
        <div className="badm-inline">
          <input className="badm-input" style={{ flex: 1, minWidth: 240 }} value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && setQApplied(q.trim())}
            placeholder="payer email · PayPal I-… id (exact) · anim1… address (exact)" />
          <select className="badm-input" value={plan} onChange={(e) => setPlan(e.target.value)}>
            <option value="">any plan</option>
            {PLAN_KEYS.map((k) => <option key={k} value={k}>{k}</option>)}
          </select>
          <select className="badm-input" value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">any status</option>
            {SUB_STATUSES.map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
          <button className="btn" onClick={() => { qApplied === q.trim() ? load(0) : setQApplied(q.trim()); }} disabled={loading}>
            {loading ? 'Loading…' : 'Search'}
          </button>
        </div>
      </div>

      {error && <div className="badm-err">{error}</div>}

      {/* table */}
      <div className="panel" style={{ marginTop: 14, padding: 0, overflow: 'hidden' }}>
        <div style={{ overflowX: 'auto' }}>
          <table className="badm-table">
            <thead>
              <tr>
                <th>account</th><th>payer email</th><th>plan</th><th>status</th>
                <th style={{ textAlign: 'right' }}>$/mo</th><th>period end</th>
                <th style={{ textAlign: 'right' }}>fails</th><th>last webhook</th><th>usage</th>
              </tr>
            </thead>
            <tbody>
              {rows.length === 0 ? (
                <tr><td colSpan={9} className="muted" style={{ padding: 24, textAlign: 'center' }}>
                  {loading ? 'Loading…' : 'No subscriptions match.'}
                </td></tr>
              ) : rows.map((r) => (
                <Fragment key={r.id}>
                  <tr className={`badm-row${openId === r.id ? ' open' : ''}`}
                    onClick={() => setOpenId(openId === r.id ? null : r.id)}>
                    <td>
                      <span className="mono" style={{ fontSize: 12 }}>{shortAddr(r.account?.address)}</span>
                      {r.account?.displayName ? <span className="muted"> · {r.account.displayName}</span> : null}
                    </td>
                    <td>{r.payerEmail ?? '—'}</td>
                    <td><span className="badm-plan">{r.planKey}</span></td>
                    <td><StatusPill status={r.status} /></td>
                    <td style={{ textAlign: 'right', fontWeight: 600 }}>{fmtUsd(r.priceUsdCents)}</td>
                    <td>{fmtDate(r.currentPeriodEnd)}</td>
                    <td style={{ textAlign: 'right', color: r.failedPayments > 0 ? 'var(--warn)' : undefined }}>{r.failedPayments}</td>
                    <td className="muted" style={{ fontSize: 12 }}>
                      {r.lastWebhookType
                        ? `${r.lastWebhookType.replace('BILLING.SUBSCRIPTION.', '').replace('PAYMENT.SALE.', 'SALE.')} · ${fmtDate(r.lastWebhookAt)}`
                        : '—'}
                    </td>
                    <td className="mono badm-usage" title="Workers · Runs this period · Deployments · production Keys · Team members">
                      W {r.usage?.workers ?? 0} · R {r.usage?.runsThisPeriod ?? 0} · D {r.usage?.deployments ?? 0} · K {r.usage?.productionKeys ?? 0} · T {r.usage?.teamMembers ?? 0}
                    </td>
                  </tr>
                  {openId === r.id && (
                    <tr className="badm-detail-row">
                      <td colSpan={9} style={{ whiteSpace: 'normal' }}>
                        <SubDetail row={r} adminFetch={adminFetch} onChanged={() => load(skip)} />
                      </td>
                    </tr>
                  )}
                </Fragment>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* pagination */}
      <div className="badm-inline" style={{ marginTop: 12, justifyContent: 'space-between' }}>
        <span className="muted" style={{ fontSize: 12.5 }}>
          {total === 0 ? '0 subscriptions' : `${skip + 1}–${skip + rows.length} of ${total}`}
        </span>
        <span className="badm-inline">
          <button className="btn ghost" disabled={loading || skip === 0} onClick={() => load(Math.max(0, skip - TAKE))}>← Prev</button>
          <button className="btn ghost" disabled={loading || skip + rows.length >= total} onClick={() => load(skip + TAKE)}>Next →</button>
        </span>
      </div>
    </>
  );
}

// ── Analytics tab ────────────────────────────────────────────────────────────
const FUNNEL_ORDER = ['pricing_view', 'checkout_start', 'checkout_success', 'checkout_error', 'checkout_abandoned'];
const LIFECYCLE_ORDER = ['upgrade', 'downgrade', 'cancel', 'payment_failed', 'reactivation', 'plan_limit_hit'];

function FunnelRow({ label, value, max }: { label: string; value: number; max: number }) {
  return (
    <div className="badm-funnel-row">
      <span className="badm-funnel-label">{label.replace(/_/g, ' ')}</span>
      <div className="badm-funnel-track">
        <div className="badm-bar" style={{ width: `${value > 0 ? Math.max(2, (value / max) * 100) : 0}%` }} />
      </div>
      <b className="badm-funnel-count">{value}</b>
    </div>
  );
}

function AnalyticsSection({ adminFetch }: { adminFetch: AdminFetch }) {
  const [data, setData] = useState<any>(null);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    setError('');
    try {
      const r = await adminFetch(`${API}/analytics`);
      if (!r.ok) throw new Error(await readErr(r));
      setData(await r.json());
    } catch (e: any) { setError(e.message); setData(null); }
  }, [adminFetch]);
  useEffect(() => { load(); }, [load]);

  if (error) return <div className="badm-err">{error}</div>;
  if (!data) return <div className="empty" style={{ marginTop: 16 }}>Loading analytics…</div>;

  const funnel: Record<string, number> = data.funnel30d ?? {};
  const maxFunnel = Math.max(1, ...[...FUNNEL_ORDER, ...LIFECYCLE_ORDER].map((k) => funnel[k] ?? 0));
  const adoption = data.workerAdoption ?? {};
  const paths: any[] = data.paths30d ?? [];

  return (
    <>
      {/* revenue KPIs */}
      <div className="panel" style={{ marginTop: 16 }}>
        <div className="kpi">
          <div className="k"><b style={{ color: 'var(--good)' }}>{fmtUsd(data.mrrCents)}</b><span>MRR</span></div>
          <div className="k"><b>{data.subscribers ?? 0}</b><span>active subs</span></div>
          <div className="k"><b>{fmtUsd(data.arpuCents)}</b><span>ARPU</span></div>
          <div className="k"><b style={{ color: 'var(--accent-2)' }}>{pct(data.conversionRate)}</b><span>pricing → paid · 30d</span></div>
          {PLAN_KEYS.filter((k) => k !== 'free').map((k) => (
            <div className="k" key={k}><b>{data.activeByPlan?.[k] ?? 0}</b><span>{k}</span></div>
          ))}
        </div>
      </div>

      <section className="section" style={{ padding: '26px 0 0' }}>
        <h2>Funnel — last 30 days</h2>
        <p className="sub">Checkout funnel above the rule, lifecycle events below it. Counts from the PII-light analytics beacon.</p>
        <div className="panel">
          {FUNNEL_ORDER.map((k) => <FunnelRow key={k} label={k} value={funnel[k] ?? 0} max={maxFunnel} />)}
          <div style={{ borderTop: '1px solid var(--border)', margin: '10px 0' }} />
          {LIFECYCLE_ORDER.map((k) => <FunnelRow key={k} label={k} value={funnel[k] ?? 0} max={maxFunnel} />)}
        </div>
      </section>

      <section className="section" style={{ padding: '26px 0 0' }}>
        <h2>Adoption</h2>
        <p className="sub">Workers, production API keys and marketplace sellers — the paid-feature pull-through.</p>
        <div className="panel">
          <div className="kpi">
            <div className="k"><b>{adoption.accountsWithWorkers ?? 0}</b><span>accounts w/ Workers</span></div>
            <div className="k"><b>{adoption.totalWorkers ?? 0}</b><span>total Workers</span></div>
            <div className="k"><b>{adoption.workersPerSubscriber ?? 0}</b><span>Workers / subscriber</span></div>
            <div className="k"><b style={{ color: 'var(--accent-2)' }}>{adoption.runsThisMonth ?? 0}</b><span>runs · {data.period}</span></div>
            <div className="k"><b>{data.productionKeys ?? 0}</b><span>production keys</span></div>
            <div className="k"><b>{data.sellers ?? 0}</b><span>marketplace sellers</span></div>
          </div>
        </div>
      </section>

      <section className="section" style={{ padding: '26px 0' }}>
        <h2>Plan changes — last 30 days</h2>
        <div className="panel">
          {paths.length === 0 ? (
            <div className="muted" style={{ fontSize: 13 }}>No upgrades or downgrades yet.</div>
          ) : (
            paths.map((p, i) => (
              <div key={i} className="badm-event" style={{ alignItems: 'center' }}>
                <span className="pill" style={{
                  fontSize: 11, fontWeight: 600,
                  color: p.kind === 'upgrade' ? 'var(--good)' : 'var(--warn)',
                  borderColor: p.kind === 'upgrade' ? 'var(--good)' : 'var(--warn)',
                }}>{p.kind}</span>
                <span className="mono" style={{ fontSize: 13 }}>{p.fromPlanKey ?? '?'} → {p.planKey ?? '?'}</span>
                <b style={{ marginLeft: 'auto' }}>×{p.count}</b>
              </div>
            ))
          )}
        </div>
      </section>
    </>
  );
}

// ── Workers engine tab ───────────────────────────────────────────────────────
function EngineSection({ adminFetch }: { adminFetch: AdminFetch }) {
  const [data, setData] = useState<any>(null);
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    setError('');
    try {
      const r = await adminFetch(`${API}/engine`);
      if (!r.ok) throw new Error(await readErr(r));
      setData(await r.json());
    } catch (e: any) { setError(e.message); setData(null); }
  }, [adminFetch]);
  useEffect(() => { load(); }, [load]);

  async function toggle(paused: boolean) {
    const confirmText = paused
      ? 'Pause ALL Workers? No runs will be scheduled or claimed until resumed; RUNNING executions finish their current run.'
      : 'Resume the Workers engine? Scheduling and claiming restart on the next tick.';
    if (!window.confirm(confirmText)) return;
    setBusy(true); setError('');
    try {
      const body: Record<string, unknown> = { paused };
      if (note.trim()) body.note = note.trim();
      const r = await adminFetch(`${API}/engine`, {
        method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body),
      });
      if (!r.ok) throw new Error(await readErr(r));
      setData(await r.json());
      setNote('');
    } catch (e: any) { setError(e.message); }
    finally { setBusy(false); }
  }

  const engine = data?.engine;
  const counts = data?.counts ?? {};
  const paused = !!engine?.paused;

  return (
    <>
      {error && <div className="badm-err">{error}</div>}
      <div className="panel" style={{ marginTop: 16, borderColor: paused ? 'rgba(255,92,114,0.5)' : undefined }}>
        {!data ? (
          <div className="muted">Loading engine state…</div>
        ) : (
          <>
            <div style={{ display: 'flex', alignItems: 'center', gap: 16, flexWrap: 'wrap' }}>
              <div style={{ flex: 1, minWidth: 260 }}>
                <div style={{ fontSize: 18, fontWeight: 700, color: paused ? 'var(--bad)' : 'var(--good)' }}>
                  {paused ? '⏸ Engine PAUSED — no Worker runs scheduled or claimed' : '● Engine running'}
                </div>
                <div className="muted" style={{ fontSize: 12.5, marginTop: 5 }}>
                  last tick {fmtDateTime(engine?.lastTickAt)}
                  {engine?.lastTickInfo ? ` · ${engine.lastTickInfo}` : ''}
                </div>
                {engine?.note && <div className="muted" style={{ fontSize: 12.5, marginTop: 3 }}>note: {engine.note}</div>}
              </div>
              <button className={paused ? 'badm-resume' : 'badm-kill'} disabled={busy} onClick={() => toggle(!paused)}>
                {busy ? 'Applying…' : paused ? '▶ Resume Workers engine' : '⏸ Pause ALL Workers'}
              </button>
            </div>
            <div className="badm-inline" style={{ marginTop: 14 }}>
              <input className="badm-input" style={{ flex: 1, minWidth: 240 }} value={note}
                onChange={(e) => setNote(e.target.value)}
                placeholder="reason (recorded on the engine state row with your identity)" />
            </div>
            <div className="kpi" style={{ marginTop: 20 }}>
              <div className="k"><b style={{ color: 'var(--warn)' }}>{counts.pendingRuns ?? 0}</b><span>pending runs</span></div>
              <div className="k"><b style={{ color: 'var(--accent-2)' }}>{counts.runningRuns ?? 0}</b><span>running now</span></div>
              <div className="k"><b>{counts.activeWorkers ?? 0}</b><span>active Workers</span></div>
            </div>
          </>
        )}
      </div>
      <p className="muted" style={{ fontSize: 12.5, marginTop: 12 }}>
        The switch flips the <code className="inline">WorkerEngineState</code> singleton the runner re-reads every tick —
        it takes effect within a minute without touching systemd. The <code className="inline">WORKERS_ENABLED</code> env
        gate is the second, deploy-level kill switch.
      </p>
    </>
  );
}

// ── page ─────────────────────────────────────────────────────────────────────
export default function BillingAdminPage() {
  const { token, saveToken, adminFetch } = useAdmin();
  const [tokenInput, setTokenInput] = useState('');
  const [tab, setTab] = useState<'subs' | 'analytics' | 'engine'>('subs');

  return (
    <div className="wrap" style={{ paddingTop: 34, paddingBottom: 60 }}>
      <div>
        <h1 style={{ fontSize: 30, letterSpacing: '-0.03em', margin: 0 }}>Billing — Admin</h1>
        <p className="muted" style={{ margin: '4px 0 0', fontSize: 14 }}>
          Subscribers, revenue analytics, and the Workers engine kill switch.
        </p>
      </div>

      {/* admin token gate */}
      <div className="panel" style={{ marginTop: 18, padding: 16 }}>
        <div className="badm-inline">
          <span style={{ color: 'var(--text-faint)', fontSize: 12, minWidth: 90, flexShrink: 0 }}>admin token</span>
          <input
            type="password"
            className="badm-input"
            style={{ flex: 1, minWidth: 220 }}
            value={tokenInput}
            onChange={(e) => setTokenInput(e.target.value)}
            placeholder={token ? '•••••• (set — leave blank to keep)' : 'MKT_ADMIN_TOKEN'}
          />
          <button className="btn primary" onClick={() => { if (tokenInput) { saveToken(tokenInput); setTokenInput(''); } }}>Use token</button>
          {token && <button className="btn ghost" onClick={() => saveToken('')}>Clear</button>}
        </div>
        <p className="muted" style={{ fontSize: 12, margin: '10px 0 0' }}>
          Send the <code className="inline">MKT_ADMIN_TOKEN</code> (ops), or sign in with an <b>ADMIN</b>-role wallet —
          either satisfies <code className="inline">requireAdmin</code>. The token stays in this tab only.
        </p>
      </div>

      {/* tabs */}
      <div className="chips" style={{ marginTop: 20 }}>
        <button className={`chip${tab === 'subs' ? ' active' : ''}`} onClick={() => setTab('subs')}>Subscribers</button>
        <button className={`chip${tab === 'analytics' ? ' active' : ''}`} onClick={() => setTab('analytics')}>Analytics</button>
        <button className={`chip${tab === 'engine' ? ' active' : ''}`} onClick={() => setTab('engine')}>Workers engine</button>
      </div>

      {tab === 'subs' && <SubscribersSection adminFetch={adminFetch} />}
      {tab === 'analytics' && <AnalyticsSection adminFetch={adminFetch} />}
      {tab === 'engine' && <EngineSection adminFetch={adminFetch} />}

      <style>{`
        /* globals.css has no button reset: unstyled <button>s would render UA-default black
           text in the UA font on the dark theme. */
        button.chip { color: var(--text-dim); font-family: var(--font); }
        button.chip.active, button.chip:hover { color: var(--text); }
        .badm-inline { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
        .badm-input {
          background: var(--bg-elev); border: 1px solid var(--border-bright); border-radius: 8px;
          color: var(--text); padding: 8px 11px; font-size: 13px; font-family: var(--font); outline: none;
        }
        .badm-err { border: 1px solid rgba(255,92,114,0.45); background: rgba(255,92,114,0.08); color: var(--bad);
          border-radius: 10px; padding: 10px 12px; font-size: 13.5px; margin-top: 12px; }
        .badm-warnbox { border: 1px solid rgba(255,180,84,0.45); background: rgba(255,180,84,0.08); color: var(--warn);
          border-radius: 10px; padding: 8px 12px; font-size: 12.5px; margin: 8px 0 2px; }

        .badm-table { width: 100%; border-collapse: collapse; font-size: 13px; min-width: 1020px; }
        .badm-table th { text-align: left; font-size: 11px; text-transform: uppercase; letter-spacing: 0.05em;
          color: var(--text-faint); font-weight: 600; padding: 11px 12px; border-bottom: 1px solid var(--border); white-space: nowrap; }
        .badm-table td { padding: 9px 12px; border-bottom: 1px solid var(--border); vertical-align: middle; white-space: nowrap; }
        .badm-table tbody tr:last-child td { border-bottom: none; }
        .badm-row { cursor: pointer; }
        .badm-row:hover td { background: rgba(108,92,255,0.05); }
        .badm-row.open td { background: rgba(108,92,255,0.08); }
        .badm-detail-row td { background: rgba(108,92,255,0.04); }
        .badm-plan { text-transform: capitalize; font-weight: 600; font-size: 12.5px; color: #a99bff; }
        .badm-usage { font-size: 11.5px; color: var(--text-dim); }

        .badm-detail { padding: 6px 2px 10px; }
        .badm-facts { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 2px 24px; margin: 4px 0; }
        .badm-fact { display: flex; font-size: 12.5px; padding: 2px 0; min-width: 0; }
        .badm-fact .k { color: var(--text-faint); min-width: 100px; flex: none; }
        .badm-fact .mono { font-size: 12px; overflow: hidden; text-overflow: ellipsis; }
        .badm-actions { display: flex; gap: 10px; flex-wrap: wrap; align-items: center; margin-top: 12px;
          border-top: 1px solid var(--border); padding-top: 12px; }
        .badm-sub-h { font-size: 12px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.05em;
          color: var(--text-faint); margin: 16px 0 6px; }
        .badm-event { display: flex; gap: 10px; font-size: 12.5px; padding: 5px 0; border-top: 1px solid var(--border);
          align-items: baseline; min-width: 0; }
        .badm-event:first-of-type { border-top: none; }
        .badm-ellipsis { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

        .badm-funnel-row { display: flex; align-items: center; gap: 12px; padding: 5px 0; }
        .badm-funnel-label { width: 160px; flex: none; font-size: 13px; color: var(--text-dim); text-transform: capitalize; }
        .badm-funnel-track { flex: 1; height: 9px; background: var(--bg-elev); border: 1px solid var(--border);
          border-radius: 999px; overflow: hidden; }
        .badm-bar { height: 100%; border-radius: 999px; background: linear-gradient(90deg, var(--accent), var(--accent-2)); }
        .badm-funnel-count { width: 60px; flex: none; text-align: right; font-size: 13.5px; }

        .badm-kill, .badm-resume { border: none; border-radius: 12px; padding: 14px 22px; font-size: 15px;
          font-weight: 700; cursor: pointer; font-family: var(--font); flex: none; }
        .badm-kill { background: linear-gradient(135deg, var(--bad), #ff7a8c); color: #fff;
          box-shadow: 0 4px 22px rgba(255,92,114,0.35); }
        .badm-resume { background: linear-gradient(135deg, var(--good), #3ee6a4); color: #06281a;
          box-shadow: 0 4px 22px rgba(36,209,139,0.3); }
        .badm-kill:hover, .badm-resume:hover { filter: brightness(1.08); }
        .badm-kill:disabled, .badm-resume:disabled { opacity: 0.6; cursor: default; filter: none; }
      `}</style>
    </div>
  );
}
