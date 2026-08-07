'use client';

import { useCallback, useEffect, useRef, useState } from 'react';

// Hire Me operator console (admin.animica.dev + /admin/hire). Cookie login (HIRE_ADMIN_USER /
// HIRE_ADMIN_PWHASH); the APIs also accept x-admin-token for CLI use. Everything renders from
// the hire admin APIs — orders (with server-renewal bookkeeping), support tickets, and the
// data-driven service catalog.

const API = '/api/mkt/v1/hire';

async function jfetch(path: string, init?: RequestInit): Promise<any> {
  const res = await fetch(`${API}${path}`, {
    ...init,
    headers: { 'content-type': 'application/json', ...(init?.headers as any) },
  });
  const j = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(j?.error?.message || j?.message || `request failed (${res.status})`);
  return j;
}

const ORDER_TABS: Array<[string, string]> = [
  ['pending', 'Pending Setup'],
  ['active', 'Active Services'],
  ['quotes', 'Quotes'],
  ['failed', 'Failed Payments'],
  ['cancelled', 'Cancelled'],
  ['renewals', 'Renewals'],
  ['all', 'All'],
];

function fmtDate(d?: string | null) {
  return d ? new Date(d).toLocaleDateString() : '—';
}
// Renewal dates are calendar days, not instants: compare local midnight to local midnight so a
// date stored as UTC midnight doesn't read as "expired" while it is still that day locally.
function daysUntil(d?: string | null): number | null {
  if (!d) return null;
  const t = new Date(d);
  if (Number.isNaN(t.getTime())) return null;
  const due = new Date(t.getUTCFullYear(), t.getUTCMonth(), t.getUTCDate()).getTime();
  const now = new Date();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  return Math.round((due - today) / 86400000);
}

export default function HireAdminPage() {
  const [authed, setAuthed] = useState<boolean | null>(null);
  const [section, setSection] = useState<'orders' | 'tickets' | 'services'>('orders');

  useEffect(() => {
    jfetch('/admin/session').then((j) => setAuthed(!!j.authed)).catch(() => setAuthed(false));
  }, []);

  return (
    <div className="wrap" style={{ paddingTop: 34, paddingBottom: 60 }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 12, flexWrap: 'wrap' }}>
        <h1 style={{ fontSize: 30, letterSpacing: '-0.03em' }}>Hire Me — Admin</h1>
        <div className="muted">orders · renewals · tickets · services</div>
        {authed && (
          <button className="btn ghost" style={{ marginLeft: 'auto' }}
            onClick={async () => { await jfetch('/admin/logout', { method: 'POST' }).catch(() => {}); setAuthed(false); }}>
            Sign out
          </button>
        )}
      </div>

      {authed === null ? (
        <div className="empty" style={{ marginTop: 24 }}>Loading…</div>
      ) : !authed ? (
        <AdminLogin onAuthed={() => setAuthed(true)} />
      ) : (
        <>
          <div className="chips" style={{ marginTop: 18 }}>
            <button className={`chip ${section === 'orders' ? 'active' : ''}`} onClick={() => setSection('orders')}>Orders</button>
            <button className={`chip ${section === 'tickets' ? 'active' : ''}`} onClick={() => setSection('tickets')}>Tickets</button>
            <button className={`chip ${section === 'services' ? 'active' : ''}`} onClick={() => setSection('services')}>Services</button>
          </div>
          {section === 'orders' && <OrdersSection />}
          {section === 'tickets' && <TicketsSection />}
          {section === 'services' && <ServicesSection />}
        </>
      )}

      <style>{`
        /* globals.css has no button reset and .panel sets no color, so a <button class="panel">
           row would render UA-default black text in the UA font on the dark theme. */
        button.panel, button.chip { color: var(--text); font-family: var(--font); }
        button.panel { display: block; width: 100%; }
        button.chip { color: var(--text-dim); }
        button.chip.active { color: var(--text); }
        .adm-field { display: flex; flex-direction: column; gap: 5px; margin-top: 10px; }
        .adm-field label { font-size: 12px; color: var(--text-faint); }
        .adm-field input, .adm-field textarea, .adm-field select {
          background: var(--bg-elev); border: 1px solid var(--border-bright); border-radius: 8px;
          color: var(--text); padding: 8px 10px; font-size: 13.5px; font-family: var(--font); outline: none;
        }
        .adm-err { border: 1px solid rgba(255,92,114,0.45); background: rgba(255,92,114,0.08); color: var(--bad);
          border-radius: 10px; padding: 10px 12px; font-size: 13.5px; margin-top: 12px; }
        .adm-warn { border: 1px solid rgba(255,180,84,0.45); background: rgba(255,180,84,0.08); color: var(--warn);
          border-radius: 10px; padding: 8px 12px; font-size: 13px; margin-top: 10px; }
        .fact { display: flex; font-size: 13.5px; padding: 2px 0; }
        .fact .fk { color: var(--text-faint); min-width: 128px; flex: none; font-size: 12.5px; padding-top: 1px; }
        .fact .fv { word-break: break-word; }
        .adm-grid2 { display: grid; grid-template-columns: repeat(auto-fit, minmax(230px, 1fr)); gap: 0 22px; margin-top: 10px; }
        .msg { border: 1px solid var(--border); border-radius: 10px; padding: 10px 12px; margin-top: 8px; }
        .msg.admin { border-color: rgba(108,92,255,0.5); background: rgba(108,92,255,0.06); }
        .msg .who { font-size: 11.5px; color: var(--text-faint); margin-bottom: 4px; }
        .msg pre { white-space: pre-wrap; font-family: var(--font); font-size: 14px; margin: 0; }
      `}</style>
    </div>
  );
}

function AdminLogin({ onAuthed }: { onAuthed: () => void }) {
  const [user, setUser] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const go = async () => {
    setBusy(true); setError('');
    try {
      await jfetch('/admin/login', { method: 'POST', body: JSON.stringify({ user, password }) });
      onAuthed();
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  };
  return (
    <div className="panel" style={{ maxWidth: 400, margin: '30px auto 0', padding: 22 }}>
      <h3 style={{ fontSize: 17 }}>Operator sign-in</h3>
      <div className="adm-field"><label>Username</label>
        <input value={user} onChange={(e) => setUser(e.target.value)} autoComplete="username" /></div>
      <div className="adm-field"><label>Password</label>
        <input type="password" value={password} onChange={(e) => setPassword(e.target.value)}
          autoComplete="current-password" onKeyDown={(e) => e.key === 'Enter' && !busy && go()} /></div>
      {error && <div className="adm-err">{error}</div>}
      <button className="btn primary" style={{ width: '100%', justifyContent: 'center', marginTop: 16 }} onClick={go} disabled={busy}>
        {busy ? 'Signing in…' : 'Sign in'}
      </button>
    </div>
  );
}

// ── orders ────────────────────────────────────────────────────────────────────
function OrdersSection() {
  const [tab, setTab] = useState('pending');
  const [orders, setOrders] = useState<any[] | null>(null);
  const [counts, setCounts] = useState<Record<string, number>>({});
  const [error, setError] = useState('');
  // Notices survive the refresh that unmounts the panel which produced them.
  const [notice, setNotice] = useState('');
  const seq = useRef(0);

  const refresh = useCallback(() => {
    setOrders(null);
    const mine = ++seq.current;
    jfetch(`/admin/orders?tab=${tab}`)
      .then((j) => {
        if (mine !== seq.current) return; // a newer request already answered — ignore this one
        setOrders(j.orders); setCounts(j.tabCounts || {});
      })
      .catch((e) => { if (mine === seq.current) setError(e.message); });
  }, [tab]);
  useEffect(() => { refresh(); }, [refresh]);

  return (
    <div style={{ marginTop: 16 }}>
      {notice && (
        <div className="adm-warn" style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
          <span style={{ flex: 1 }}>{notice}</span>
          <button className="btn ghost" onClick={() => setNotice('')}>Dismiss</button>
        </div>
      )}
      <div className="kpi">
        {ORDER_TABS.filter(([k]) => k !== 'all').map(([k, label]) => (
          <div className="k" key={k}><b>{counts[k] ?? '…'}</b><span>{label.toLowerCase()}</span></div>
        ))}
      </div>
      <div className="chips" style={{ marginTop: 12 }}>
        {ORDER_TABS.map(([k, label]) => (
          <button key={k} className={`chip ${tab === k ? 'active' : ''}`} onClick={() => setTab(k)}>
            {label}{counts[k] != null ? ` (${counts[k]})` : ''}
          </button>
        ))}
        <button className="chip" onClick={refresh}>↻ Refresh</button>
      </div>
      {error && <div className="adm-err">{error}</div>}
      {!orders ? (
        <div className="empty" style={{ marginTop: 16 }}>Loading…</div>
      ) : !orders.length ? (
        <div className="empty" style={{ marginTop: 16 }}>Nothing here.</div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12, marginTop: 16 }}>
          {orders.map((o) => (
            <OrderPanel key={o.orderId} order={o} onChanged={refresh} onNotice={setNotice} showRenewal={tab === 'renewals'} />
          ))}
        </div>
      )}
    </div>
  );
}

function OrderPanel({ order: o, onChanged, onNotice, showRenewal }: {
  order: any; onChanged: () => void; onNotice: (s: string) => void; showRenewal: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [note, setNote] = useState(o.adminNotes ?? '');
  const [srvStart, setSrvStart] = useState(o.serverStartAt ? String(o.serverStartAt).slice(0, 10) : '');
  const [srvEnd, setSrvEnd] = useState(o.serverEndAt ? String(o.serverEndAt).slice(0, 10) : '');
  const [srvLabel, setSrvLabel] = useState(o.serverLabel ?? '');

  const act = async (body: any, confirmMsg?: string) => {
    if (confirmMsg && !window.confirm(confirmMsg)) return;
    setBusy(true); setError('');
    try {
      const j = await jfetch(`/admin/orders/${o.orderId}`, { method: 'POST', body: JSON.stringify(body) });
      // Hoisted: refreshing unmounts this panel, so a local warning would never be seen.
      onNotice(j.warning ? `${o.orderId}: ${j.warning}` : '');
      onChanged();
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  };

  const days = daysUntil(o.serverEndAt);
  const renewColor = days == null ? undefined : days < 0 ? 'var(--bad)' : days <= 7 ? 'var(--warn)' : 'var(--good)';

  return (
    <div className="panel" style={{ padding: 16 }}>
      <div style={{ display: 'flex', gap: 10, alignItems: 'baseline', flexWrap: 'wrap' }}>
        <b>{o.serviceName}</b>
        <span className="mono muted" style={{ fontSize: 12.5 }}>{o.orderId}</span>
        <span className="pill">{o.deploymentStatus.replace('_', ' ')}</span>
        <span className="pill">{o.paymentStatus === 'quote' ? 'quote' : `pay: ${o.paymentStatus}`}</span>
        {(showRenewal || o.serverEndAt) && (
          <span className="pill" style={renewColor ? { borderColor: renewColor, color: renewColor } : {}}>
            {o.serverEndAt ? (days! < 0 ? `server EXPIRED ${-days!}d ago` : `server renews in ${days}d`) : 'no server dates'}
          </span>
        )}
        <span style={{ marginLeft: 'auto' }} className="muted">{fmtDate(o.createdAt)}</span>
      </div>
      <div className="fact" style={{ marginTop: 8 }}>
        <div className="fk">Customer</div>
        <div className="fv">{o.customerName} · <span className="mono">{o.email}</span>{o.company ? ` · ${o.company}` : ''}</div>
      </div>
      <div className="fact">
        <div className="fk">Price</div>
        <div className="fv">{o.monthlyPriceUsd != null ? `$${o.monthlyPriceUsd}/mo${o.setupFeeUsd ? ` + $${o.setupFeeUsd} setup` : ''}` : 'quote'}</div>
      </div>
      {o.domain && <div className="fact"><div className="fk">Domain</div><div className="fv mono">{o.domain}</div></div>}
      {o.paypalSubscriptionId && (
        <div className="fact"><div className="fk">PayPal sub</div><div className="fv mono">{o.paypalSubscriptionId}</div></div>
      )}
      {o.notes && <div className="fact"><div className="fk">Customer notes</div><div className="fv">{o.notes}</div></div>}

      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 12 }}>
        {o.deploymentStatus !== 'active' && o.paymentStatus !== 'quote' && (
          <button className="btn primary" disabled={busy} onClick={() => act({ action: 'complete' })}>Mark Complete</button>
        )}
        {o.deploymentStatus !== 'suspended' && o.paymentStatus !== 'quote' && (
          <button className="btn" disabled={busy} onClick={() => act({ action: 'suspend' }, 'Suspend this service AND its PayPal subscription?')}>Suspend</button>
        )}
        {(o.deploymentStatus === 'suspended' || o.paymentStatus === 'suspended') && (
          <button className="btn" disabled={busy} onClick={() => act({ action: 'reactivate' })}>Reactivate</button>
        )}
        {o.deploymentStatus !== 'cancelled' && (
          <button className="btn" style={{ borderColor: 'rgba(255,92,114,0.4)', color: 'var(--bad)' }} disabled={busy}
            onClick={() => act({ action: 'cancel' }, 'Cancel this order AND permanently cancel the PayPal subscription?')}>
            Cancel
          </button>
        )}
        <button className="btn ghost" onClick={() => setOpen((v) => !v)}>{open ? 'Hide Details' : 'View Details'}</button>
      </div>
      {error && <div className="adm-err">{error}</div>}

      {open && (
        <div style={{ borderTop: '1px solid var(--border)', marginTop: 12, paddingTop: 10 }}>
          <div className="adm-grid2">
            <div>
              {o.discordUsername && <div className="fact"><div className="fk">Discord</div><div className="fv">{o.discordUsername}</div></div>}
              <div className="fact"><div className="fk">Plan</div><div className="fv mono">{o.paypalPlanId || '—'}</div></div>
              <div className="fact"><div className="fk">Txn</div><div className="fv mono">{o.paypalTransactionId || '—'}</div></div>
              <div className="fact"><div className="fk">IP</div><div className="fv mono">{o.customerIp || '—'}</div></div>
              <div className="fact"><div className="fk">User agent</div><div className="fv" style={{ fontSize: 12 }}>{o.userAgent || '—'}</div></div>
              <div className="fact"><div className="fk">Account</div><div className="fv mono">{o.user?.email || '(guest)'}</div></div>
            </div>
            <div>
              <div className="adm-field"><label>Server start (bookkeeping)</label>
                <input type="date" value={srvStart} onChange={(e) => setSrvStart(e.target.value)} /></div>
              <div className="adm-field"><label>Server end / renewal due</label>
                <input type="date" value={srvEnd} onChange={(e) => setSrvEnd(e.target.value)} /></div>
              <div className="adm-field"><label>Server label (provider / instance)</label>
                <input value={srvLabel} onChange={(e) => setSrvLabel(e.target.value)} placeholder="e.g. Contabo VPS16 #12345" /></div>
              <button className="btn" style={{ marginTop: 10 }} disabled={busy}
                onClick={() => act({ action: 'server_dates', serverStartAt: srvStart || null, serverEndAt: srvEnd || null, serverLabel: srvLabel || null })}>
                Save server dates
              </button>
            </div>
          </div>
          <div className="adm-field"><label>Admin notes (internal)</label>
            <textarea rows={3} value={note} onChange={(e) => setNote(e.target.value)} /></div>
          <button className="btn" style={{ marginTop: 8 }} disabled={busy} onClick={() => act({ action: 'note', note })}>Save notes</button>
        </div>
      )}
    </div>
  );
}

// ── tickets ───────────────────────────────────────────────────────────────────
function TicketsSection() {
  const [tab, setTab] = useState<'open' | 'closed'>('open');
  const [tickets, setTickets] = useState<any[] | null>(null);
  const [counts, setCounts] = useState<{ open?: number; closed?: number }>({});
  const [openId, setOpenId] = useState<string | null>(null);
  const [thread, setThread] = useState<any | null>(null);
  const [error, setError] = useState('');

  const seq = useRef(0);
  const refresh = useCallback(() => {
    const mine = ++seq.current;
    jfetch(`/admin/tickets?tab=${tab}`)
      .then((j) => {
        if (mine !== seq.current) return; // stale response from a previous tab — drop it
        setTickets(j.tickets); setCounts(j.counts || {});
      })
      .catch((e) => { if (mine === seq.current) setError(e.message); });
  }, [tab]);
  useEffect(() => { setTickets(null); refresh(); }, [refresh]);
  useEffect(() => {
    if (!openId) { setThread(null); return; }
    jfetch(`/admin/tickets/${openId}`).then((j) => setThread(j.ticket)).catch((e) => setError(e.message));
  }, [openId]);

  if (openId && thread) {
    return <AdminTicketThread ticket={thread} onBack={() => { setOpenId(null); refresh(); }} onUpdated={setThread} />;
  }
  return (
    <div style={{ marginTop: 16 }}>
      <div className="chips">
        <button className={`chip ${tab === 'open' ? 'active' : ''}`} onClick={() => setTab('open')}>Open ({counts.open ?? '…'})</button>
        <button className={`chip ${tab === 'closed' ? 'active' : ''}`} onClick={() => setTab('closed')}>Closed ({counts.closed ?? '…'})</button>
        <button className="chip" onClick={refresh}>↻ Refresh</button>
      </div>
      {error && <div className="adm-err">{error}</div>}
      {!tickets ? (
        <div className="empty" style={{ marginTop: 16 }}>Loading…</div>
      ) : !tickets.length ? (
        <div className="empty" style={{ marginTop: 16 }}>No tickets.</div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10, marginTop: 14 }}>
          {tickets.map((t) => (
            <button key={t.ticketId} className="panel" style={{ padding: 14, textAlign: 'left', cursor: 'pointer', background: 'var(--bg-card)', border: '1px solid var(--border)' }}
              onClick={() => setOpenId(t.ticketId)}>
              <div style={{ display: 'flex', gap: 10, alignItems: 'baseline', flexWrap: 'wrap' }}>
                <b>{t.subject}</b>
                <span className="mono muted" style={{ fontSize: 12 }}>{t.ticketId}</span>
                {t.priority !== 'normal' && <span className="pill" style={t.priority === 'high' ? { borderColor: 'var(--bad)', color: 'var(--bad)' } : {}}>{t.priority}</span>}
                <span className="pill" style={{ marginLeft: 'auto' }}>{t.status.replace('_', ' ')}</span>
              </div>
              <div className="muted" style={{ fontSize: 12.5, marginTop: 4 }}>
                {t.user?.name} · {t.user?.email}{t.order ? ` · ${t.order.orderId} (${t.order.serviceName})` : ''} · updated {new Date(t.updatedAt).toLocaleString()}
              </div>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function AdminTicketThread({ ticket, onBack, onUpdated }: { ticket: any; onBack: () => void; onUpdated: (t: any) => void }) {
  const [reply, setReply] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  const act = async (body: any) => {
    setBusy(true); setError('');
    try {
      const j = await jfetch(`/admin/tickets/${ticket.ticketId}`, { method: 'POST', body: JSON.stringify(body) });
      onUpdated(j.ticket); setReply('');
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  };

  return (
    <div style={{ marginTop: 16 }}>
      <button className="btn ghost" onClick={onBack}>← All tickets</button>
      <div className="panel" style={{ padding: 18, marginTop: 10 }}>
        <div style={{ display: 'flex', gap: 10, alignItems: 'baseline', flexWrap: 'wrap' }}>
          <h3 style={{ fontSize: 17 }}>{ticket.subject}</h3>
          <span className="mono muted" style={{ fontSize: 12.5 }}>{ticket.ticketId}</span>
          <span className="pill" style={{ marginLeft: 'auto' }}>{ticket.status.replace('_', ' ')}</span>
        </div>
        <div className="muted" style={{ fontSize: 13, marginTop: 4 }}>
          {ticket.user?.name} · <span className="mono">{ticket.user?.email}</span>
          {ticket.order ? <> · {ticket.order.orderId} ({ticket.order.serviceName})</> : null}
        </div>
        {(ticket.messages ?? []).map((m: any, i: number) => (
          <div key={i} className={`msg ${m.author === 'admin' ? 'admin' : ''}`}>
            <div className="who">{m.author === 'admin' ? 'You (support)' : 'Customer'} · {new Date(m.createdAt).toLocaleString()}</div>
            <pre>{m.body}</pre>
          </div>
        ))}
        <div className="adm-field"><label>Reply (emailed to the customer)</label>
          <textarea rows={4} value={reply} onChange={(e) => setReply(e.target.value)} /></div>
        {error && <div className="adm-err">{error}</div>}
        <div style={{ display: 'flex', gap: 10, marginTop: 12, flexWrap: 'wrap' }}>
          <button className="btn primary" onClick={() => act({ action: 'reply', message: reply })} disabled={busy || !reply.trim()}>
            {busy ? 'Sending…' : 'Send reply'}
          </button>
          {ticket.status !== 'closed'
            ? <button className="btn" onClick={() => act({ action: 'close' })} disabled={busy}>Close</button>
            : <button className="btn" onClick={() => act({ action: 'reopen' })} disabled={busy}>Reopen</button>}
        </div>
      </div>
    </div>
  );
}

// ── services (data-driven catalog) ────────────────────────────────────────────
const EMPTY_SVC = {
  slug: '', name: '', tagline: '', description: '', icon: '🛠️', kind: 'subscription',
  priceUsdMonthly: 100, setupFeeUsd: 500, sortOrder: 100, featuresText: '', createPaypalPlan: true, active: true,
};

function ServicesSection() {
  const [services, setServices] = useState<any[] | null>(null);
  const [error, setError] = useState('');
  const [editing, setEditing] = useState<any | null>(null);
  const [creating, setCreating] = useState(false);

  const refresh = useCallback(() => {
    jfetch('/admin/services').then((j) => setServices(j.services)).catch((e) => setError(e.message));
  }, []);
  useEffect(() => { refresh(); }, [refresh]);

  return (
    <div style={{ marginTop: 16 }}>
      <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
        <button className="btn primary" onClick={() => { setCreating((v) => !v); setEditing(null); }}>
          {creating ? 'Back' : '+ New service'}
        </button>
        <span className="muted" style={{ fontSize: 13 }}>
          The /hire page renders whatever is active here — add future offerings without code changes.
        </span>
      </div>
      {error && <div className="adm-err">{error}</div>}

      {creating || editing ? (
        // key: switching between "edit X" and "new" must remount, or the old form state persists.
        <ServiceForm
          key={editing?.id ?? 'new'}
          initial={editing}
          onDone={() => { setCreating(false); setEditing(null); refresh(); }}
        />
      ) : !services ? (
        <div className="empty" style={{ marginTop: 16 }}>Loading…</div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10, marginTop: 14 }}>
          {services.map((s) => (
            <div className="panel" key={s.id} style={{ padding: 14, opacity: s.active ? 1 : 0.55 }}>
              <div style={{ display: 'flex', gap: 10, alignItems: 'baseline', flexWrap: 'wrap' }}>
                <span>{s.icon}</span><b>{s.name}</b>
                <span className="mono muted" style={{ fontSize: 12 }}>/{s.slug}</span>
                <span className="pill">{s.kind === 'quote' ? 'quote' : `$${s.priceUsdMonthly}/mo${s.setupFeeUsd ? ` + $${s.setupFeeUsd} setup` : ''}`}</span>
                {!s.active && <span className="pill" style={{ borderColor: 'var(--warn)', color: 'var(--warn)' }}>hidden</span>}
                {s.kind !== 'quote' && !s.paypalPlanId && <span className="pill" style={{ borderColor: 'var(--bad)', color: 'var(--bad)' }}>no PayPal plan</span>}
                <span className="muted" style={{ marginLeft: 'auto', fontSize: 12.5 }}>{s._count?.orders ?? 0} orders</span>
              </div>
              <div style={{ display: 'flex', gap: 8, marginTop: 10, flexWrap: 'wrap' }}>
                <button className="btn" onClick={() => { setEditing(s); setCreating(false); }}>Edit</button>
                <button className="btn" onClick={async () => {
                  try { await jfetch('/admin/services', { method: 'PATCH', body: JSON.stringify({ id: s.id, active: !s.active }) }); refresh(); }
                  catch (e) { setError((e as Error).message); }
                }}>{s.active ? 'Hide from /hire' : 'Show on /hire'}</button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function ServiceForm({ initial, onDone }: { initial: any | null; onDone: () => void }) {
  const [f, setF] = useState<any>(() => initial ? {
    ...initial,
    featuresText: (initial.features ?? []).join('\n'),
    createPaypalPlan: false,
  } : { ...EMPTY_SVC });
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const set = (k: string, v: any) => setF((p: any) => ({ ...p, [k]: v }));

  const save = async () => {
    setBusy(true); setError('');
    try {
      // An empty price field must not silently become $0 while the live PayPal plan keeps
      // charging the real amount.
      let price: number | null = null;
      if (f.kind !== 'quote') {
        price = Number(f.priceUsdMonthly);
        if (String(f.priceUsdMonthly ?? '').trim() === '' || !Number.isFinite(price) || price <= 0) {
          throw new Error('Enter a monthly price greater than $0 (or switch the service to “quote”).');
        }
      }
      const setupRaw = String(f.setupFeeUsd ?? '').trim();
      const setup = setupRaw === '' ? 0 : Number(f.setupFeeUsd);
      if (f.kind !== 'quote' && (!Number.isFinite(setup) || setup < 0)) throw new Error('Setup fee must be $0 or more.');
      const body: any = {
        slug: f.slug, name: f.name, tagline: f.tagline, description: f.description, icon: f.icon,
        kind: f.kind, sortOrder: Number(f.sortOrder) || 100, active: !!f.active,
        priceUsdMonthly: price,
        setupFeeUsd: f.kind === 'quote' ? 0 : setup,
        features: String(f.featuresText || '').split('\n').map((x: string) => x.trim()).filter(Boolean),
        createPaypalPlan: !!f.createPaypalPlan,
      };
      if (initial) await jfetch('/admin/services', { method: 'PATCH', body: JSON.stringify({ ...body, id: initial.id }) });
      else await jfetch('/admin/services', { method: 'POST', body: JSON.stringify(body) });
      onDone();
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  };

  return (
    <div className="panel" style={{ padding: 18, marginTop: 14, maxWidth: 640 }}>
      <h3 style={{ fontSize: 17 }}>{initial ? `Edit ${initial.name}` : 'New service'}</h3>
      <div className="adm-grid2">
        <div className="adm-field"><label>Slug (kebab-case)</label>
          <input value={f.slug} onChange={(e) => set('slug', e.target.value)} disabled={!!initial} /></div>
        <div className="adm-field"><label>Icon (emoji)</label>
          <input value={f.icon} onChange={(e) => set('icon', e.target.value)} /></div>
      </div>
      <div className="adm-field"><label>Name</label>
        <input value={f.name} onChange={(e) => set('name', e.target.value)} /></div>
      <div className="adm-field"><label>Tagline</label>
        <input value={f.tagline} onChange={(e) => set('tagline', e.target.value)} /></div>
      <div className="adm-field"><label>Description</label>
        <textarea rows={3} value={f.description} onChange={(e) => set('description', e.target.value)} /></div>
      <div className="adm-field"><label>Included features (one per line)</label>
        <textarea rows={6} value={f.featuresText} onChange={(e) => set('featuresText', e.target.value)} /></div>
      <div className="adm-grid2">
        <div className="adm-field"><label>Kind</label>
          <select value={f.kind} onChange={(e) => set('kind', e.target.value)}>
            <option value="subscription">subscription</option>
            <option value="quote">quote (contact us)</option>
          </select></div>
        <div className="adm-field"><label>Sort order</label>
          <input type="number" value={f.sortOrder} onChange={(e) => set('sortOrder', e.target.value)} /></div>
      </div>
      {f.kind !== 'quote' && (
        <div className="adm-grid2">
          <div className="adm-field"><label>Monthly price (USD)</label>
            <input type="number" value={f.priceUsdMonthly ?? ''} onChange={(e) => set('priceUsdMonthly', e.target.value)} /></div>
          <div className="adm-field"><label>One-time setup fee (USD)</label>
            <input type="number" value={f.setupFeeUsd ?? 0} onChange={(e) => set('setupFeeUsd', e.target.value)} /></div>
        </div>
      )}
      {f.kind !== 'quote' && (
        <label style={{ display: 'flex', gap: 8, alignItems: 'center', marginTop: 12, fontSize: 13.5 }}>
          <input type="checkbox" checked={!!f.createPaypalPlan} onChange={(e) => set('createPaypalPlan', e.target.checked)} />
          Create a new PayPal billing plan with these prices {initial?.paypalPlanId ? '(replaces the current plan for NEW orders only)' : ''}
        </label>
      )}
      <label style={{ display: 'flex', gap: 8, alignItems: 'center', marginTop: 8, fontSize: 13.5 }}>
        <input type="checkbox" checked={!!f.active} onChange={(e) => set('active', e.target.checked)} />
        Visible on /hire
      </label>
      {error && <div className="adm-err">{error}</div>}
      <div style={{ display: 'flex', gap: 10, marginTop: 14 }}>
        <button className="btn primary" onClick={save} disabled={busy}>{busy ? 'Saving…' : 'Save service'}</button>
        <button className="btn ghost" onClick={onDone} disabled={busy}>Cancel</button>
      </div>
    </div>
  );
}
