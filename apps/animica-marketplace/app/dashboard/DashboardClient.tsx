'use client';

import { useCallback, useEffect, useState } from 'react';

// Customer dashboard (dashboard.animica.dev + /dashboard): sign in, track managed-service
// orders, and run support tickets. Same account as the /hire checkout.

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

interface Customer { email: string; name: string; company: string | null; discord: string | null }

const PAY_BADGE: Record<string, { label: string; color: string }> = {
  draft: { label: 'awaiting payment', color: 'var(--text-faint)' },
  pending: { label: 'payment pending', color: 'var(--warn)' },
  active: { label: 'billing active', color: 'var(--good)' },
  suspended: { label: 'billing suspended', color: 'var(--warn)' },
  cancelled: { label: 'cancelled', color: 'var(--bad)' },
  failed: { label: 'payment failed', color: 'var(--bad)' },
  quote: { label: 'quote', color: 'var(--accent-2)' },
};
const DEPLOY_BADGE: Record<string, { label: string; color: string }> = {
  awaiting_payment: { label: 'awaiting payment', color: 'var(--text-faint)' },
  pending_setup: { label: 'Pending Setup', color: 'var(--warn)' },
  active: { label: 'Active', color: 'var(--good)' },
  suspended: { label: 'Suspended', color: 'var(--warn)' },
  cancelled: { label: 'Cancelled', color: 'var(--bad)' },
  quote_requested: { label: 'Quote requested', color: 'var(--accent-2)' },
};

function Badge({ map, k }: { map: Record<string, { label: string; color: string }>; k: string }) {
  const b = map[k] ?? { label: k, color: 'var(--text-dim)' };
  return (
    <span className="pill" style={{ borderColor: b.color, color: b.color }}>{b.label}</span>
  );
}

export default function DashboardClient() {
  const [loaded, setLoaded] = useState(false);
  const [customer, setCustomer] = useState<Customer | null>(null);
  const [tab, setTab] = useState<'services' | 'support'>('services');

  useEffect(() => {
    jfetch('/auth/session').then((j) => setCustomer(j.customer)).catch(() => {}).finally(() => setLoaded(true));
  }, []);

  return (
    <div className="wrap" style={{ paddingTop: 36, paddingBottom: 60 }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 12, flexWrap: 'wrap' }}>
        <h1 style={{ fontSize: 30, letterSpacing: '-0.03em' }}>Your dashboard</h1>
        <div className="muted">managed services · billing · support</div>
        <div style={{ marginLeft: 'auto', display: 'flex', gap: 10, alignItems: 'center' }}>
          {customer && (
            <>
              <span className="muted mono" style={{ fontSize: 13 }}>{customer.email}</span>
              <button className="btn ghost" onClick={async () => { await jfetch('/auth/logout', { method: 'POST' }).catch(() => {}); setCustomer(null); }}>
                Sign out
              </button>
            </>
          )}
        </div>
      </div>

      {!loaded ? (
        <div className="empty" style={{ marginTop: 24 }}>Loading…</div>
      ) : !customer ? (
        <AuthPanel onAuthed={setCustomer} />
      ) : (
        <>
          <div className="chips" style={{ marginTop: 18 }}>
            <button className={`chip ${tab === 'services' ? 'active' : ''}`} onClick={() => setTab('services')}>My services</button>
            <button className={`chip ${tab === 'support' ? 'active' : ''}`} onClick={() => setTab('support')}>Support tickets</button>
            <a className="chip" href="/hire">+ New service request</a>
          </div>
          {tab === 'services' ? <OrdersPanel /> : <SupportPanel />}
        </>
      )}

      <style>{`
        /* globals.css has no button reset and .panel sets no color, so a <button class="panel">
           row would render UA-default black text in the UA font on the dark theme. */
        button.panel, .dash-field button, button.chip { color: var(--text); font-family: var(--font); }
        button.panel { display: block; width: 100%; }
        .dash-field { display: flex; flex-direction: column; gap: 5px; margin-top: 12px; }
        .dash-field label { font-size: 12.5px; color: var(--text-faint); }
        .dash-field input, .dash-field textarea, .dash-field select {
          background: var(--bg-elev); border: 1px solid var(--border-bright); border-radius: 8px;
          color: var(--text); padding: 9px 11px; font-size: 14px; font-family: var(--font); outline: none;
        }
        .dash-field input:focus, .dash-field textarea:focus { border-color: var(--accent); }
        .dash-err { border: 1px solid rgba(255,92,114,0.45); background: rgba(255,92,114,0.08); color: var(--bad);
          border-radius: 10px; padding: 10px 12px; font-size: 13.5px; margin-top: 12px; }
        .dash-row { display: flex; gap: 8px 18px; flex-wrap: wrap; font-size: 13.5px; }
        .dash-row .k { color: var(--text-faint); margin-right: 4px; }
        .msg { border: 1px solid var(--border); border-radius: 10px; padding: 10px 12px; margin-top: 8px; }
        .msg.admin { border-color: rgba(108,92,255,0.5); background: rgba(108,92,255,0.06); }
        .msg .who { font-size: 11.5px; color: var(--text-faint); margin-bottom: 4px; }
        .msg pre { white-space: pre-wrap; font-family: var(--font); font-size: 14px; margin: 0; }
      `}</style>
    </div>
  );
}

function AuthPanel({ onAuthed }: { onAuthed: (c: Customer) => void }) {
  const [mode, setMode] = useState<'login' | 'register'>('login');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [name, setName] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  const go = useCallback(async () => {
    setBusy(true); setError('');
    try {
      const j = mode === 'login'
        ? await jfetch('/auth/login', { method: 'POST', body: JSON.stringify({ email, password }) })
        : await jfetch('/auth/register', { method: 'POST', body: JSON.stringify({ email, password, name }) });
      onAuthed(j.customer);
    } catch (e) {
      setError((e as Error).message);
    } finally { setBusy(false); }
  }, [mode, email, password, name, onAuthed]);

  return (
    <div className="panel" style={{ maxWidth: 440, margin: '28px auto 0', padding: 22 }}>
      <div className="chips">
        <button className={`chip ${mode === 'login' ? 'active' : ''}`} onClick={() => setMode('login')}>Sign in</button>
        <button className={`chip ${mode === 'register' ? 'active' : ''}`} onClick={() => setMode('register')}>Create account</button>
      </div>
      <p className="muted" style={{ fontSize: 13, marginTop: 10 }}>
        Use the account you created when ordering — or create one to place your first service request.
      </p>
      {mode === 'register' && (
        <div className="dash-field"><label>Your name</label>
          <input value={name} onChange={(e) => setName(e.target.value)} autoComplete="name" /></div>
      )}
      <div className="dash-field"><label>Email</label>
        <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="email" /></div>
      <div className="dash-field"><label>Password</label>
        <input type="password" value={password} onChange={(e) => setPassword(e.target.value)}
          autoComplete={mode === 'register' ? 'new-password' : 'current-password'}
          onKeyDown={(e) => e.key === 'Enter' && !busy && go()} /></div>
      {error && <div className="dash-err">{error}</div>}
      <button className="btn primary" style={{ width: '100%', justifyContent: 'center', marginTop: 16 }} onClick={go} disabled={busy}>
        {busy ? 'Please wait…' : mode === 'login' ? 'Sign in' : 'Create account'}
      </button>
    </div>
  );
}

function OrdersPanel() {
  const [orders, setOrders] = useState<any[] | null>(null);
  const [error, setError] = useState('');

  useEffect(() => {
    jfetch('/orders').then((j) => setOrders(j.orders)).catch((e) => setError(e.message));
  }, []);

  if (error) return <div className="dash-err">{error}</div>;
  if (!orders) return <div className="empty" style={{ marginTop: 20 }}>Loading your services…</div>;
  if (!orders.length) {
    return (
      <div className="empty" style={{ marginTop: 20 }}>
        No services yet. <a href="/hire" className="mono">Order your first deployment →</a>
      </div>
    );
  }
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14, marginTop: 18 }}>
      {orders.map((o) => (
        <div className="panel" key={o.orderId} style={{ padding: 18 }}>
          <div style={{ display: 'flex', gap: 10, alignItems: 'baseline', flexWrap: 'wrap' }}>
            <h3 style={{ fontSize: 17 }}>{o.serviceName}</h3>
            <span className="mono muted" style={{ fontSize: 12.5 }}>{o.orderId}</span>
            <div style={{ marginLeft: 'auto', display: 'flex', gap: 8, flexWrap: 'wrap' }}>
              <Badge map={DEPLOY_BADGE} k={o.deploymentStatus} />
              <Badge map={PAY_BADGE} k={o.paymentStatus} />
            </div>
          </div>
          <div className="dash-row" style={{ marginTop: 10 }}>
            {o.monthlyPriceUsd != null && <span><span className="k">Price</span>${o.monthlyPriceUsd}/month{o.setupFeeUsd ? ` (+$${o.setupFeeUsd} setup)` : ''}</span>}
            <span><span className="k">Ordered</span>{new Date(o.createdAt).toLocaleDateString()}</span>
            {o.domain && <span><span className="k">Domain</span><span className="mono">{o.domain}</span></span>}
            {o.paypalSubscriptionId && <span><span className="k">Subscription</span><span className="mono">{o.paypalSubscriptionId}</span></span>}
          </div>
          {o.deploymentStatus === 'pending_setup' && (
            <p className="muted" style={{ fontSize: 13, marginTop: 10 }}>
              We’re reviewing your deployment — expect first contact within 24 hours.
            </p>
          )}
          {o.paymentStatus === 'failed' && (
            <p style={{ fontSize: 13, marginTop: 10, color: 'var(--bad)' }}>
              Your last payment failed — please update your payment method in PayPal to avoid suspension.
            </p>
          )}
        </div>
      ))}
      <p className="muted" style={{ fontSize: 12.5 }}>
        Manage or cancel the PayPal subscription itself from your PayPal account → Automatic payments.
        Need help? Open a support ticket.
      </p>
    </div>
  );
}

function SupportPanel() {
  const [tickets, setTickets] = useState<any[] | null>(null);
  const [orders, setOrders] = useState<any[]>([]);
  const [error, setError] = useState('');
  const [openId, setOpenId] = useState<string | null>(null);
  const [thread, setThread] = useState<any | null>(null);
  const [showNew, setShowNew] = useState(false);

  const refresh = useCallback(() => {
    jfetch('/tickets').then((j) => setTickets(j.tickets)).catch((e) => setError(e.message));
  }, []);
  useEffect(() => {
    refresh();
    jfetch('/orders').then((j) => setOrders(j.orders)).catch(() => {});
  }, [refresh]);
  useEffect(() => {
    if (!openId) { setThread(null); return; }
    jfetch(`/tickets/${openId}`).then((j) => setThread(j.ticket)).catch((e) => setError(e.message));
  }, [openId]);

  return (
    <div style={{ marginTop: 18 }}>
      <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
        <button className="btn primary" onClick={() => { setShowNew((v) => !v); setOpenId(null); }}>
          {showNew ? 'Back to tickets' : '+ Open a ticket'}
        </button>
        <span className="muted" style={{ fontSize: 13 }}>We reply by email and here, usually within 24 hours.</span>
      </div>
      {error && <div className="dash-err">{error}</div>}

      {showNew ? (
        <NewTicket orders={orders} onCreated={(t) => { setShowNew(false); refresh(); setOpenId(t.ticketId); }} />
      ) : openId && thread ? (
        <TicketThread ticket={thread} onBack={() => { setOpenId(null); refresh(); }} onUpdated={setThread} />
      ) : !tickets ? (
        <div className="empty" style={{ marginTop: 18 }}>Loading tickets…</div>
      ) : !tickets.length ? (
        <div className="empty" style={{ marginTop: 18 }}>No tickets yet.</div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10, marginTop: 16 }}>
          {tickets.map((t) => (
            <button key={t.ticketId} className="panel" style={{ padding: 14, textAlign: 'left', cursor: 'pointer', border: '1px solid var(--border)', background: 'var(--bg-card)' }}
              onClick={() => setOpenId(t.ticketId)}>
              <div style={{ display: 'flex', gap: 10, alignItems: 'baseline', flexWrap: 'wrap' }}>
                <b>{t.subject}</b>
                <span className="mono muted" style={{ fontSize: 12 }}>{t.ticketId}</span>
                <span className="pill" style={{ marginLeft: 'auto' }}>{t.status.replace('_', ' ')}</span>
              </div>
              <div className="muted" style={{ fontSize: 12.5, marginTop: 4 }}>
                {t.orderRef ? `order ${t.orderRef} · ` : ''}updated {new Date(t.updatedAt).toLocaleString()}
              </div>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function NewTicket({ orders, onCreated }: { orders: any[]; onCreated: (t: any) => void }) {
  const [subject, setSubject] = useState('');
  const [message, setMessage] = useState('');
  const [orderRef, setOrderRef] = useState('');
  const [priority, setPriority] = useState('normal');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    setBusy(true); setError('');
    try {
      const j = await jfetch('/tickets', {
        method: 'POST',
        body: JSON.stringify({ subject, message, priority, orderRef: orderRef || undefined }),
      });
      onCreated(j.ticket);
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  };

  return (
    <div className="panel" style={{ maxWidth: 560, marginTop: 16, padding: 20 }}>
      <h3 style={{ fontSize: 17 }}>Open a support ticket</h3>
      <div className="dash-field"><label>Subject *</label>
        <input value={subject} onChange={(e) => setSubject(e.target.value)} placeholder="e.g. DNS setup for my custom domain" /></div>
      <div className="dash-field"><label>Related service (optional)</label>
        <select value={orderRef} onChange={(e) => setOrderRef(e.target.value)}>
          <option value="">— none —</option>
          {orders.map((o) => <option key={o.orderId} value={o.orderId}>{o.orderId} · {o.serviceName}</option>)}
        </select></div>
      <div className="dash-field"><label>Priority</label>
        <select value={priority} onChange={(e) => setPriority(e.target.value)}>
          <option value="low">Low</option><option value="normal">Normal</option><option value="high">High</option>
        </select></div>
      <div className="dash-field"><label>Message *</label>
        <textarea rows={5} value={message} onChange={(e) => setMessage(e.target.value)}
          placeholder="Describe the issue or request — include domains, error messages, timelines…" /></div>
      {error && <div className="dash-err">{error}</div>}
      <button className="btn primary" style={{ marginTop: 14 }} onClick={submit} disabled={busy}>
        {busy ? 'Sending…' : 'Submit ticket'}
      </button>
    </div>
  );
}

function TicketThread({ ticket, onBack, onUpdated }: { ticket: any; onBack: () => void; onUpdated: (t: any) => void }) {
  const [reply, setReply] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  const send = async () => {
    setBusy(true); setError('');
    try {
      const j = await jfetch(`/tickets/${ticket.ticketId}`, { method: 'POST', body: JSON.stringify({ message: reply }) });
      onUpdated(j.ticket); setReply('');
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  };
  const close = async () => {
    setBusy(true); setError('');
    try {
      const j = await jfetch(`/tickets/${ticket.ticketId}`, { method: 'POST', body: JSON.stringify({ action: 'close' }) });
      onUpdated(j.ticket);
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
        {(ticket.messages ?? []).map((m: any, i: number) => (
          <div key={i} className={`msg ${m.author === 'admin' ? 'admin' : ''}`}>
            <div className="who">{m.author === 'admin' ? 'Animica support' : 'You'} · {new Date(m.createdAt).toLocaleString()}</div>
            <pre>{m.body}</pre>
          </div>
        ))}
        {ticket.status !== 'closed' ? (
          <>
            <div className="dash-field"><label>Reply</label>
              <textarea rows={4} value={reply} onChange={(e) => setReply(e.target.value)} /></div>
            {error && <div className="dash-err">{error}</div>}
            <div style={{ display: 'flex', gap: 10, marginTop: 12 }}>
              <button className="btn primary" onClick={send} disabled={busy || !reply.trim()}>{busy ? 'Sending…' : 'Send reply'}</button>
              <button className="btn" style={{ borderColor: 'rgba(255,92,114,0.4)', color: 'var(--bad)' }} onClick={close} disabled={busy}>Close ticket</button>
            </div>
          </>
        ) : (
          <p className="muted" style={{ fontSize: 13, marginTop: 12 }}>This ticket is closed — open a new one if you need anything else.</p>
        )}
      </div>
    </div>
  );
}
