'use client';

import { useCallback, useEffect, useRef, useState } from 'react';

// Client half of /hire: service cards (fully data-driven from props) + the checkout modal
// (sign in → details → PayPal subscribe → thank-you). Quote-kind services skip PayPal.

export interface HireServiceDto {
  id: string;
  slug: string;
  name: string;
  tagline: string;
  description: string;
  features: string[];
  icon: string;
  kind: string; // subscription | quote
  priceUsdMonthly: number | null;
  setupFeeUsd: number;
  checkoutReady: boolean;
}

interface Customer { email: string; name: string; company: string | null; discord: string | null }

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

let sdkPromise: Promise<any> | null = null;
function loadPayPalSdk(clientId: string): Promise<any> {
  const w = window as any;
  if (w.paypal) return Promise.resolve(w.paypal);
  if (!sdkPromise) {
    sdkPromise = new Promise((resolve, reject) => {
      const s = document.createElement('script');
      s.src = `https://www.paypal.com/sdk/js?client-id=${encodeURIComponent(clientId)}&vault=true&intent=subscription&currency=USD`;
      s.onload = () => resolve((window as any).paypal);
      s.onerror = () => {
        // Drop the failed script and the cached rejection so the Retry button can try again.
        s.remove();
        sdkPromise = null;
        reject(new Error('PayPal failed to load — check your connection or ad blocker, then retry.'));
      };
      document.head.appendChild(s);
    });
  }
  return sdkPromise;
}

export default function HireClient({
  services,
  paypal,
}: {
  services: HireServiceDto[];
  paypal: { clientId: string; env: string };
}) {
  const [customer, setCustomer] = useState<Customer | null>(null);
  const [sessionLoaded, setSessionLoaded] = useState(false);
  const [modalService, setModalService] = useState<HireServiceDto | null>(null);

  useEffect(() => {
    jfetch('/auth/session')
      .then((j) => setCustomer(j.customer))
      .catch(() => {})
      .finally(() => setSessionLoaded(true));
  }, []);

  const subs = services.filter((s) => s.kind !== 'quote');
  const quotes = services.filter((s) => s.kind === 'quote');

  return (
    <div className="wrap" style={{ paddingTop: 44, paddingBottom: 40 }}>
      {/* hero */}
      <div style={{ textAlign: 'center', maxWidth: 760, margin: '0 auto' }}>
        <div className="pill" style={{ marginBottom: 14 }}>🛠️ Managed services · deployed &amp; maintained for you</div>
        <h1 style={{ fontSize: 46, letterSpacing: '-0.035em', lineHeight: 1.05 }}>
          Hire{' '}
          <span style={{ background: 'linear-gradient(120deg,var(--accent-2),var(--accent))', WebkitBackgroundClip: 'text', backgroundClip: 'text', color: 'transparent' }}>
            Me
          </span>
        </h1>
        <p className="muted" style={{ fontSize: 17, marginTop: 12 }}>
          Need infrastructure for your Animica project? Order professionally managed deployments with
          recurring monthly billing.
        </p>
        <p className="muted" style={{ fontSize: 14, marginTop: 8 }}>
          These aren’t marketplace listings — every deployment is set up, monitored and maintained by
          the Animica operator, with ongoing support included.
        </p>
        <div style={{ display: 'flex', flexWrap: 'wrap', justifyContent: 'center', gap: 12, marginTop: 18 }}>
          <a className="btn" href="/dashboard">Customer dashboard →</a>
          {sessionLoaded && customer && <span className="pill">Signed in as {customer.email}</span>}
        </div>
      </div>

      {/* how it works */}
      <section className="section" style={{ paddingBottom: 10 }}>
        <div className="grid" style={{ gridTemplateColumns: 'repeat(auto-fill,minmax(240px,1fr))' }}>
          {[
            ['1️⃣', 'Order', 'Pick a service, sign in and check out with PayPal — recurring monthly billing.'],
            ['2️⃣', 'We deploy', 'Your dedicated infrastructure is provisioned, secured and handed over.'],
            ['3️⃣', 'We maintain', 'Updates, monitoring and monthly maintenance — with a support desk for anything else.'],
          ].map(([ico, h, p]) => (
            <div className="card" key={h as string}>
              <div className="top"><div className="ico">{ico}</div><div><h3>{h}</h3></div></div>
              <p>{p}</p>
            </div>
          ))}
        </div>
      </section>

      {/* services (data-driven) */}
      <section className="section" id="services">
        <h2>Services</h2>
        <div className="sub">Fixed monthly pricing. Cancel any time from PayPal or your dashboard.</div>
        <div className="grid" style={{ gridTemplateColumns: 'repeat(auto-fill,minmax(310px,1fr))' }}>
          {subs.map((s) => (
            <div className="card hire-card" key={s.id}>
              <div className="top">
                <div className="ico">{s.icon}</div>
                <div style={{ minWidth: 0 }}>
                  <h3>{s.name}</h3>
                  {s.tagline && <div className="by">{s.tagline}</div>}
                </div>
                <div className="price-tag" style={{ textAlign: 'right' }}>
                  ${s.priceUsdMonthly}<small>/month</small>
                  {s.setupFeeUsd > 0 && <div className="muted" style={{ fontSize: 11 }}>+ ${s.setupFeeUsd} one-time setup</div>}
                </div>
              </div>
              {s.description && <p>{s.description}</p>}
              <ul className="hire-features">
                {s.features.map((f, i) => <li key={i}>{f}</li>)}
              </ul>
              <div style={{ marginTop: 'auto', display: 'flex', gap: 10, alignItems: 'center' }}>
                <button
                  className="btn primary"
                  disabled={!s.checkoutReady}
                  onClick={() => setModalService(s)}
                  style={{ flex: 1, justifyContent: 'center' }}
                >
                  {s.checkoutReady ? 'Order Now' : 'Coming soon'}
                </button>
              </div>
            </div>
          ))}
          {quotes.map((s) => (
            <div className="card hire-card" key={s.id} style={{ borderColor: 'var(--border-bright)' }}>
              <div className="top">
                <div className="ico">{s.icon}</div>
                <div style={{ minWidth: 0 }}>
                  <h3>{s.name}</h3>
                  {s.tagline && <div className="by">{s.tagline}</div>}
                </div>
                <div className="price-tag" style={{ textAlign: 'right' }}>Custom<small> quote</small></div>
              </div>
              {s.description && <p>{s.description}</p>}
              <ul className="hire-features">
                {s.features.map((f, i) => <li key={i}>{f}</li>)}
              </ul>
              <div style={{ marginTop: 'auto' }}>
                <button className="btn" onClick={() => setModalService(s)} style={{ width: '100%', justifyContent: 'center' }}>
                  Contact for a Quote
                </button>
              </div>
            </div>
          ))}
        </div>
        <p className="muted" style={{ fontSize: 13, marginTop: 14 }}>
          Already a customer? Track deployments and open support tickets on{' '}
          <a href="/dashboard" className="mono">your dashboard →</a>
        </p>
      </section>

      {modalService && (
        <CheckoutModal
          service={modalService}
          services={services}
          customer={customer}
          setCustomer={setCustomer}
          paypalClientId={paypal.clientId}
          onClose={() => setModalService(null)}
          onSwitchService={(s) => setModalService(s)}
        />
      )}

      <style>{`
        .hire-card { min-height: 340px; }
        .hire-features { list-style: none; padding: 0; margin: 4px 0 12px; display: flex; flex-direction: column; gap: 6px; }
        .hire-features li { font-size: 13.5px; color: var(--text-dim); padding-left: 22px; position: relative; }
        .hire-features li::before { content: '✓'; position: absolute; left: 2px; color: var(--good); font-weight: 700; }
        .hire-scrim { position: fixed; inset: 0; z-index: 40; background: rgba(3,4,7,0.62); backdrop-filter: blur(3px); display: grid; place-items: center; padding: 18px; overflow-y: auto; }
        .hire-modal { width: min(560px, 96vw); max-height: 92vh; overflow-y: auto; background: var(--bg-card); border: 1px solid var(--border-bright); border-radius: var(--radius); padding: 22px; }
        /* globals.css has no button reset; without this, UA styles paint button text black on dark. */
        .hire-scrim button, .hire-card button { font-family: var(--font); color: var(--text); }
        .hire-scrim button.chip { color: var(--text-dim); }
        .hire-scrim button.chip.active { color: var(--text); }
        .hire-modal h3 { font-size: 20px; letter-spacing: -0.02em; }
        .hire-field { display: flex; flex-direction: column; gap: 5px; margin-top: 12px; }
        .hire-field label { font-size: 12.5px; color: var(--text-faint); }
        .hire-field input, .hire-field textarea, .hire-field select {
          background: var(--bg-elev); border: 1px solid var(--border-bright); border-radius: 8px;
          color: var(--text); padding: 9px 11px; font-size: 14px; font-family: var(--font); outline: none;
        }
        .hire-field input:focus, .hire-field textarea:focus, .hire-field select:focus { border-color: var(--accent); }
        .hire-err { border: 1px solid rgba(255,92,114,0.45); background: rgba(255,92,114,0.08); color: var(--bad);
          border-radius: 10px; padding: 10px 12px; font-size: 13.5px; margin-top: 12px; }
        .hire-okbox { border: 1px solid rgba(36,209,139,0.4); background: rgba(36,209,139,0.07); border-radius: 10px; padding: 12px; margin-top: 12px; font-size: 14px; }
        .hire-tabs { display: flex; gap: 8px; margin-top: 12px; }
      `}</style>
    </div>
  );
}

// ── checkout modal ─────────────────────────────────────────────────────────────
function CheckoutModal({
  service, services, customer, setCustomer, paypalClientId, onClose, onSwitchService,
}: {
  service: HireServiceDto;
  services: HireServiceDto[];
  customer: Customer | null;
  setCustomer: (c: Customer | null) => void;
  paypalClientId: string;
  onClose: () => void;
  onSwitchService: (s: HireServiceDto) => void;
}) {
  const isQuote = service.kind === 'quote';
  const [step, setStep] = useState<'auth' | 'form' | 'pay' | 'done'>(customer ? 'form' : 'auth');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  // auth state
  const [authMode, setAuthMode] = useState<'login' | 'register'>('login');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [regName, setRegName] = useState('');

  // order form state
  const [fullName, setFullName] = useState(customer?.name || '');
  const [company, setCompany] = useState(customer?.company || '');
  const [domain, setDomain] = useState('');
  const [discord, setDiscord] = useState(customer?.discord || '');
  const [notes, setNotes] = useState('');

  // pay/done state
  const [orderId, setOrderId] = useState('');
  const [payInfo, setPayInfo] = useState<{ planId: string; monthlyUsd: number; setupFeeUsd: number } | null>(null);
  const [doneQuote, setDoneQuote] = useState(false);
  const [payAttempt, setPayAttempt] = useState(0); // bump to re-render the PayPal buttons
  // Set the instant PayPal approves. A second subscription would be a second real charge, so the
  // buttons are torn down permanently once this flips — even if our own /confirm call then fails.
  const [subscribed, setSubscribed] = useState(false);
  const payRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (customer && step === 'auth') {
      setStep('form');
      if (!fullName) setFullName(customer.name);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [customer]);

  const doAuth = useCallback(async () => {
    setBusy(true); setError('');
    try {
      const j = authMode === 'login'
        ? await jfetch('/auth/login', { method: 'POST', body: JSON.stringify({ email, password }) })
        : await jfetch('/auth/register', { method: 'POST', body: JSON.stringify({ email, password, name: regName }) });
      setCustomer(j.customer);
      if (!fullName) setFullName(j.customer?.name || '');
      setStep('form');
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }, [authMode, email, password, regName, fullName, setCustomer]);

  const submitOrder = useCallback(async () => {
    setBusy(true); setError('');
    try {
      if (!fullName.trim()) throw new Error('Please enter your full name.');
      const j = await jfetch('/orders', {
        method: 'POST',
        body: JSON.stringify({ service: service.slug, fullName, company, domain, discord, notes }),
      });
      setOrderId(j.orderId);
      if (j.quote) { setDoneQuote(true); setStep('done'); }
      else { setPayInfo(j.paypal); setStep('pay'); }
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }, [service.slug, fullName, company, domain, discord, notes]);

  // Render PayPal buttons when entering the pay step (never again once approved).
  useEffect(() => {
    if (step !== 'pay' || !payInfo || !payRef.current || subscribed) return;
    let buttons: any = null;
    let cancelled = false;
    setError('');
    loadPayPalSdk(paypalClientId)
      .then((paypal) => {
        if (cancelled || !payRef.current) return;
        payRef.current.innerHTML = '';
        buttons = paypal.Buttons({
          style: { shape: 'pill', color: 'gold', layout: 'vertical', label: 'subscribe' },
          createSubscription: (_d: any, actions: any) =>
            actions.subscription.create({
              plan_id: payInfo.planId,
              custom_id: orderId,
              application_context: {
                brand_name: 'Animica',
                shipping_preference: 'NO_SHIPPING',
                user_action: 'SUBSCRIBE_NOW',
              },
            }),
          onApprove: async (data: any) => {
            // Approved = a real subscription exists. Retiring the buttons here is what stops a
            // retry from creating (and billing) a second one.
            setSubscribed(true);
            try { buttons?.close?.(); } catch {}
            try {
              setBusy(true);
              await jfetch('/orders/confirm', {
                method: 'POST',
                body: JSON.stringify({ orderId, subscriptionId: data.subscriptionID, paypalOrderId: data.orderID }),
              });
              setStep('done');
            } catch (e) {
              setError(`Your subscription was created at PayPal, but we couldn't record it here: ${(e as Error).message}. ` +
                `Do NOT pay again — quote order ${orderId} (subscription ${data.subscriptionID}) to support and we'll reconcile it.`);
            } finally {
              setBusy(false);
            }
          },
          onError: () => setError('PayPal checkout failed. You have not been charged — please try again.'),
          onCancel: () => setError('Checkout cancelled. You can retry whenever you are ready.'),
        });
        return buttons.render(payRef.current);
      })
      .catch((e) => setError((e as Error).message));
    return () => { cancelled = true; try { buttons?.close?.(); } catch {} };
  }, [step, payInfo, orderId, paypalClientId, payAttempt, subscribed]);

  // Modal ergonomics: Escape closes, background scroll is locked while open.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape' && !busy) onClose(); };
    document.addEventListener('keydown', onKey);
    const prev = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => { document.removeEventListener('keydown', onKey); document.body.style.overflow = prev; };
  }, [busy, onClose]);

  const dueToday = (payInfo?.setupFeeUsd ?? service.setupFeeUsd) + (payInfo?.monthlyUsd ?? service.priceUsdMonthly ?? 0);

  return (
    <div className="hire-scrim" onMouseDown={(e) => { if (e.target === e.currentTarget && !busy) onClose(); }}>
      <div className="hire-modal" role="dialog" aria-modal="true">
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <div className="ico" style={{ width: 40, height: 40, borderRadius: 11, background: 'linear-gradient(135deg,#232842,#171b2b)', display: 'grid', placeItems: 'center', border: '1px solid var(--border)' }}>{service.icon}</div>
          <div style={{ minWidth: 0, flex: 1 }}>
            <h3>{isQuote ? 'Request a quote' : `Order ${service.name}`}</h3>
            <div className="muted" style={{ fontSize: 13 }}>
              {isQuote ? 'Tell us what you need — we reply within 24 hours.' :
                <>${service.priceUsdMonthly}/month{service.setupFeeUsd ? ` + $${service.setupFeeUsd} one-time setup` : ''}</>}
            </div>
          </div>
          <button className="btn ghost" onClick={onClose} disabled={busy} aria-label="Close">✕</button>
        </div>

        {step === 'auth' && (
          <div>
            <div className="hire-tabs">
              <button className={`chip ${authMode === 'login' ? 'active' : ''}`} onClick={() => setAuthMode('login')}>Sign in</button>
              <button className={`chip ${authMode === 'register' ? 'active' : ''}`} onClick={() => setAuthMode('register')}>Create account</button>
            </div>
            <p className="muted" style={{ fontSize: 13, marginTop: 10 }}>
              An account lets you track your deployment and open support tickets on your dashboard.
            </p>
            {authMode === 'register' && (
              <div className="hire-field">
                <label>Your name</label>
                <input value={regName} onChange={(e) => setRegName(e.target.value)} placeholder="Jane Doe" autoComplete="name" />
              </div>
            )}
            <div className="hire-field">
              <label>Email address</label>
              <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="you@example.com" autoComplete="email" />
            </div>
            <div className="hire-field">
              <label>Password {authMode === 'register' && <span className="muted">(min 8 characters)</span>}</label>
              <input
                type="password" value={password} onChange={(e) => setPassword(e.target.value)}
                autoComplete={authMode === 'register' ? 'new-password' : 'current-password'}
                onKeyDown={(e) => e.key === 'Enter' && !busy && doAuth()}
              />
            </div>
            {error && <div className="hire-err">{error}</div>}
            <button className="btn primary" style={{ width: '100%', justifyContent: 'center', marginTop: 16 }} onClick={doAuth} disabled={busy}>
              {busy ? 'Please wait…' : authMode === 'login' ? 'Sign in & continue' : 'Create account & continue'}
            </button>
          </div>
        )}

        {step === 'form' && (
          <div>
            <div className="hire-field">
              <label>Full Name *</label>
              <input value={fullName} onChange={(e) => setFullName(e.target.value)} placeholder="Jane Doe" />
            </div>
            <div className="hire-field">
              <label>Email Address</label>
              <input value={customer?.email || ''} disabled style={{ opacity: 0.65 }} />
            </div>
            <div className="hire-field">
              <label>Company <span className="muted">(optional)</span></label>
              <input value={company ?? ''} onChange={(e) => setCompany(e.target.value)} />
            </div>
            <div className="hire-field">
              <label>Service Selected</label>
              <select value={service.slug} onChange={(e) => {
                const s = services.find((x) => x.slug === e.target.value);
                if (s) onSwitchService(s);
              }}>
                {services.map((s) => (
                  <option key={s.slug} value={s.slug}>
                    {s.name}{s.kind === 'quote' ? ' (quote)' : ` — $${s.priceUsdMonthly}/mo`}
                  </option>
                ))}
              </select>
            </div>
            <div className="hire-field">
              <label>Domain Name <span className="muted">(optional — using your own domain? support will help you point DNS)</span></label>
              <input value={domain} onChange={(e) => setDomain(e.target.value)} placeholder="pool.yourproject.com" />
            </div>
            <div className="hire-field">
              <label>Discord Username <span className="muted">(optional)</span></label>
              <input value={discord ?? ''} onChange={(e) => setDiscord(e.target.value)} />
            </div>
            <div className="hire-field">
              <label>Additional Notes <span className="muted">(optional)</span></label>
              <textarea rows={3} value={notes} onChange={(e) => setNotes(e.target.value)}
                placeholder={isQuote ? 'Describe what you need — stack, scale, timeline…' : 'Anything we should know before deploying?'} />
            </div>
            {error && <div className="hire-err">{error}</div>}
            <button className="btn primary" style={{ width: '100%', justifyContent: 'center', marginTop: 16 }} onClick={submitOrder} disabled={busy}>
              {busy ? 'Submitting…' : isQuote ? 'Send quote request' : 'Continue to PayPal'}
            </button>
            {!isQuote && (
              <p className="muted" style={{ fontSize: 12.5, marginTop: 10, textAlign: 'center' }}>
                Due today: ${dueToday} USD{service.setupFeeUsd ? ` ($${service.setupFeeUsd} setup + $${service.priceUsdMonthly} first month)` : ''} · renews at ${service.priceUsdMonthly}/month · cancel anytime
              </p>
            )}
          </div>
        )}

        {step === 'pay' && (
          <div>
            <div className="hire-okbox">
              Order <span className="mono">{orderId}</span> created — complete the subscription below.
              <div className="muted" style={{ fontSize: 12.5, marginTop: 4 }}>
                Due today: ${dueToday} USD{(payInfo?.setupFeeUsd ?? 0) > 0 ? ` ($${payInfo!.setupFeeUsd} setup + $${payInfo!.monthlyUsd} first month)` : ''} · then ${payInfo?.monthlyUsd}/month
              </div>
            </div>
            <div ref={payRef} style={{ marginTop: 16, minHeight: subscribed ? 0 : 150 }}>
              {!subscribed && <div className="muted" style={{ fontSize: 13 }}>Loading PayPal…</div>}
            </div>
            {busy && <div className="muted" style={{ fontSize: 13, marginTop: 8 }}>Confirming your subscription…</div>}
            {error && <div className="hire-err">{error}</div>}
            {error && !subscribed && (
              <button className="btn" style={{ marginTop: 10 }} onClick={() => { setError(''); setPayAttempt((n) => n + 1); }}>
                ↻ Retry PayPal checkout
              </button>
            )}
            {subscribed && error && (
              <a className="btn" style={{ marginTop: 10 }} href="/dashboard">Open dashboard &amp; contact support</a>
            )}
          </div>
        )}

        {step === 'done' && (
          <div style={{ textAlign: 'center', padding: '18px 4px 8px' }}>
            <div style={{ fontSize: 44 }}>🎉</div>
            <h3 style={{ marginTop: 8 }}>Thank you!</h3>
            <p style={{ marginTop: 6 }}>
              {doneQuote ? 'Your quote request has been received.' : 'Your order has been received.'}
            </p>
            <div className="hire-okbox" style={{ textAlign: 'center' }}>
              Order ID: <b className="mono">{orderId}</b>
            </div>
            <p className="muted" style={{ fontSize: 14, marginTop: 10 }}>
              {doneQuote
                ? 'We’ll reply with a quote shortly (usually within 24 hours). A confirmation email is on its way.'
                : 'We’ll begin reviewing your deployment shortly. A confirmation email with your order details is on its way.'}
            </p>
            <div style={{ display: 'flex', gap: 10, justifyContent: 'center', marginTop: 16, flexWrap: 'wrap' }}>
              <a className="btn primary" href="/dashboard">Open your dashboard</a>
              <button className="btn" onClick={onClose}>Close</button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
