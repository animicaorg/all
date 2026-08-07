'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { hasWallet, loginWithPurpose } from '@/components/wallet';
import { planName } from '@/components/PlanUpsell';

// Client half of /pricing: the five tier cards + the PayPal checkout modal (cloned from
// /hire's CheckoutModal, incl. the `subscribed` latch and "Do NOT pay again" recovery copy).
// Identity is the wallet-keyed Account: checkout signs a single-purpose `subscribe` login.
// The browser never chooses plan ids or prices — it names a plan key; /billing/subscribe and
// /billing/change resolve everything money-related server-side, and /billing/confirm
// re-verifies the subscription at PayPal.

export interface PricingPlanDto {
  key: string;
  name: string;
  tagline: string;
  icon: string;
  features: string[];
  priceUsdCents: number;
  featured: boolean;
  checkoutReady: boolean;
}

interface SummaryDto {
  plan: { key: string; subscribedKey: string; state: string; billingWarning: boolean };
  subscription: { id: string; planKey: string; status: string; currentPeriodEnd: string | null } | null;
}

const API = '/api/mkt/v1/billing';

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

// Fire-and-forget funnel beacon — must never affect the page.
function track(kind: 'pricing_view' | 'checkout_abandoned' | 'checkout_error', planKey?: string) {
  api('/track', { method: 'POST', body: JSON.stringify({ kind, planKey }) }).catch(() => {});
}

function usd(cents: number): string {
  const s = (cents / 100).toFixed(2);
  return s.endsWith('.00') ? s.slice(0, -3) : s;
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

export default function PricingClient({
  plans,
  paypal,
  highlight,
}: {
  plans: PricingPlanDto[];
  paypal: { clientId: string; env: string };
  highlight: string;
}) {
  const [summary, setSummary] = useState<SummaryDto | null>(null);
  const [modalPlan, setModalPlan] = useState<PricingPlanDto | null>(null);
  const [notice, setNotice] = useState('');
  const [pageErr, setPageErr] = useState('');
  const [busyKey, setBusyKey] = useState('');

  const loadSummary = useCallback(() => {
    api('/summary').then(setSummary).catch(() => setSummary(null)); // 401 => browsing signed-out
  }, []);

  useEffect(() => {
    track('pricing_view');
    loadSummary();
  }, [loadSummary]);

  // A live paid subscription flips card CTAs into the switch/cancel flow.
  const hasPaid = !!summary && summary.plan.subscribedKey !== 'free' && summary.plan.state !== 'FREE';
  const currentKey = hasPaid ? summary!.plan.subscribedKey : 'free';

  // Downgrade to free = cancel (access runs until the paid period ends). /billing/change is
  // still the mode oracle so the client never guesses the account's live PayPal state.
  const switchToFree = useCallback(async () => {
    setPageErr(''); setNotice(''); setBusyKey('free');
    try {
      const j = await api('/change', { method: 'POST', body: JSON.stringify({ planKey: 'free' }) });
      if (j.mode !== 'cancel') { setBusyKey(''); return; }
      if (!window.confirm(
        'Cancel your subscription? You keep paid access until the end of the period you already paid for. ' +
        'Nothing you built is deleted — over-limit resources are paused, and you can pick which stay active in Settings → Billing.',
      )) { setBusyKey(''); return; }
      await api('/cancel', { method: 'POST' });
      setNotice('Subscription cancelled. Paid access continues until the end of your current billing period.');
      loadSummary();
    } catch (e) {
      setPageErr((e as Error).message);
      track('checkout_error', 'free');
    } finally {
      setBusyKey('');
    }
  }, [loadSummary]);

  return (
    <div className="wrap pricing-page" style={{ paddingTop: 44, paddingBottom: 40 }}>
      {/* hero */}
      <div style={{ textAlign: 'center', maxWidth: 780, margin: '0 auto' }}>
        <div className="pill" style={{ marginBottom: 14 }}>💠 Animica plans · cancel anytime</div>
        <h1 style={{ fontSize: 46, letterSpacing: '-0.035em', lineHeight: 1.05, margin: 0 }}>
          AI is free.{' '}
          <span style={{ background: 'linear-gradient(120deg,var(--accent-2),var(--accent))', WebkitBackgroundClip: 'text', backgroundClip: 'text', color: 'transparent' }}>
            Put it to work.
          </span>
        </h1>
        <p className="muted" style={{ fontSize: 17, marginTop: 14, lineHeight: 1.55 }}>
          Build, experiment and create with Animica for free. Upgrade when you need autonomous Workers,
          scheduled execution, production APIs, larger deployments, marketplace monetization, teams,
          and business infrastructure.
        </p>
        {hasPaid && (
          <p className="pill" style={{ marginTop: 14 }}>
            Current plan: <b style={{ marginLeft: 4 }}>{planName(currentKey)}</b>
            {summary!.plan.billingWarning && <span style={{ color: 'var(--warn)', marginLeft: 8 }}>billing attention needed</span>}
          </p>
        )}
      </div>

      {notice && <div className="pricing-okbox" style={{ maxWidth: 640, margin: '18px auto 0' }}>{notice}</div>}
      {pageErr && <div className="pricing-err" style={{ maxWidth: 640, margin: '18px auto 0' }}>{pageErr}</div>}

      {/* cards */}
      <section className="section">
        <div className="grid" style={{ gridTemplateColumns: 'repeat(auto-fill,minmax(228px,1fr))', alignItems: 'stretch' }}>
          {plans.map((p) => {
            const isCurrent = hasPaid && currentKey === p.key && summary!.plan.state === 'ACTIVE';
            const isFree = p.priceUsdCents === 0;
            const highlighted = highlight === p.key;
            return (
              <div
                className={`card pricing-card ${p.featured ? 'featured' : ''}`}
                key={p.key}
                style={highlighted ? { boxShadow: '0 0 0 2px var(--accent), 0 6px 30px var(--accent-glow)' } : undefined}
              >
                {p.featured && <div className="pricing-ribbon">MOST POPULAR</div>}
                <div className="top">
                  <div className="ico">{p.icon}</div>
                  <div style={{ minWidth: 0 }}>
                    <h3>{p.name}</h3>
                    {p.tagline && <div className="by">{p.tagline}</div>}
                  </div>
                </div>
                <div className="pricing-price">
                  ${usd(p.priceUsdCents)}<small>/month</small>
                </div>
                <ul className="pricing-features">
                  {p.features.map((f, i) => <li key={i}>{f}</li>)}
                </ul>
                <div style={{ marginTop: 'auto' }}>
                  {isFree ? (
                    hasPaid ? (
                      <button className="btn" style={{ width: '100%', justifyContent: 'center' }}
                        onClick={switchToFree} disabled={busyKey !== ''}>
                        {busyKey === 'free' ? 'Please wait…' : 'Switch to Free'}
                      </button>
                    ) : (
                      <a className="btn" href="/marketplace" style={{ width: '100%', justifyContent: 'center' }}>
                        Start creating
                      </a>
                    )
                  ) : isCurrent ? (
                    <button className="btn" style={{ width: '100%', justifyContent: 'center' }} disabled>
                      Current plan
                    </button>
                  ) : (
                    <button
                      className={`btn ${p.featured ? 'primary' : ''}`}
                      style={{ width: '100%', justifyContent: 'center' }}
                      disabled={!p.checkoutReady || busyKey !== ''}
                      onClick={() => { setNotice(''); setPageErr(''); setModalPlan(p); }}
                    >
                      {!p.checkoutReady ? 'Coming soon' : hasPaid ? `Switch to ${p.name}` : `Get ${p.name}`}
                    </button>
                  )}
                </div>
              </div>
            );
          })}
        </div>
        <p className="muted" style={{ fontSize: 13, marginTop: 16, textAlign: 'center' }}>
          Prices in USD, billed monthly via PayPal. Downgrades and cancellations never delete anything you
          built — over-limit resources are paused, not destroyed. Manage everything in{' '}
          <a className="mono" href="/settings/billing">Settings → Billing</a>.
        </p>
      </section>

      {modalPlan && (
        <CheckoutModal
          plan={modalPlan}
          authed={summary !== null}
          paypalClientId={paypal.clientId}
          onClose={(changed) => { setModalPlan(null); if (changed) loadSummary(); }}
        />
      )}

      <style>{`
        .pricing-card { min-height: 380px; position: relative; }
        .pricing-card.featured { border-color: var(--accent); box-shadow: 0 6px 30px rgba(108,92,255,0.18); }
        .pricing-ribbon { position: absolute; top: -11px; left: 50%; transform: translateX(-50%);
          background: linear-gradient(135deg, var(--accent), #7d6dff); color: #fff; font-size: 10.5px;
          font-weight: 700; letter-spacing: 0.08em; padding: 4px 12px; border-radius: 999px; white-space: nowrap; }
        .pricing-price { font-size: 30px; font-weight: 800; letter-spacing: -0.03em; margin: 2px 0 4px; }
        .pricing-price small { font-size: 13px; font-weight: 500; color: var(--text-faint); }
        .pricing-features { list-style: none; padding: 0; margin: 4px 0 14px; display: flex; flex-direction: column; gap: 6px; }
        .pricing-features li { font-size: 13px; color: var(--text-dim); padding-left: 20px; position: relative; line-height: 1.4; }
        .pricing-features li::before { content: '✓'; position: absolute; left: 0; color: var(--good); font-weight: 700; }
        .pricing-scrim { position: fixed; inset: 0; z-index: 40; background: rgba(3,4,7,0.62); backdrop-filter: blur(3px); display: grid; place-items: center; padding: 18px; overflow-y: auto; }
        .pricing-modal { width: min(520px, 96vw); max-height: 92vh; overflow-y: auto; background: var(--bg-card); border: 1px solid var(--border-bright); border-radius: var(--radius); padding: 22px; }
        /* globals.css has no button reset; without this, UA styles paint button text black on dark. */
        .pricing-page button, .pricing-scrim button { font-family: var(--font); color: var(--text); }
        .pricing-page button:disabled { opacity: 0.6; cursor: default; }
        .pricing-modal h3 { font-size: 20px; letter-spacing: -0.02em; margin: 0; }
        .pricing-err { border: 1px solid rgba(255,92,114,0.45); background: rgba(255,92,114,0.08); color: var(--bad);
          border-radius: 10px; padding: 10px 12px; font-size: 13.5px; margin-top: 12px; }
        .pricing-okbox { border: 1px solid rgba(36,209,139,0.4); background: rgba(36,209,139,0.07); border-radius: 10px; padding: 12px; margin-top: 12px; font-size: 14px; }
      `}</style>
    </div>
  );
}

// ── checkout modal ─────────────────────────────────────────────────────────────
// connect (wallet gate) → decide (/billing/change picks revise vs fresh subscribe) → pay
// (PayPal Buttons) → done. The `subscribed` latch is load-bearing: once PayPal approves, a
// REAL subscription exists — the buttons are torn down permanently so a retry can never
// create (and bill) a second one, even if our own /confirm call then fails.
function CheckoutModal({
  plan,
  authed,
  paypalClientId,
  onClose,
}: {
  plan: PricingPlanDto;
  authed: boolean;
  paypalClientId: string;
  onClose: (changed: boolean) => void;
}) {
  const [step, setStep] = useState<'connect' | 'decide' | 'pay' | 'done'>(authed ? 'decide' : 'connect');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [pay, setPay] = useState<{
    mode: 'subscribe' | 'revise';
    draftId?: string; // our PlanSubscription row id — PayPal custom_id (subscribe mode)
    subscriptionId?: string; // live PayPal sub being revised (revise mode)
    planId: string;
    clientId: string;
  } | null>(null);
  const [payAttempt, setPayAttempt] = useState(0); // bump to re-render the PayPal buttons
  const [subscribed, setSubscribed] = useState(false);
  const [doneIds, setDoneIds] = useState<{ subscriptionId: string } | null>(null);
  const payRef = useRef<HTMLDivElement | null>(null);

  const fail = useCallback((e: unknown) => {
    setError((e as Error).message);
    track('checkout_error', plan.key);
  }, [plan.key]);

  // Ask the server how to move this account to the target plan, then stage the pay step.
  const decide = useCallback(async () => {
    setBusy(true); setError('');
    try {
      const j = await api('/change', { method: 'POST', body: JSON.stringify({ planKey: plan.key }) });
      if (j.mode === 'revise') {
        setPay({ mode: 'revise', subscriptionId: j.subscriptionId, planId: j.paypal.planId, clientId: j.paypal.clientId || paypalClientId });
        setStep('pay');
      } else if (j.mode === 'subscribe') {
        const s = await api('/subscribe', { method: 'POST', body: JSON.stringify({ planKey: plan.key }) });
        setPay({ mode: 'subscribe', draftId: s.id, planId: s.paypal.planId, clientId: s.paypal.clientId || paypalClientId });
        setStep('pay');
      } else {
        // mode 'cancel' can only happen for target=free, which never opens this modal.
        setError('Unexpected change mode — please retry from the pricing page.');
      }
    } catch (e) {
      const status = (e as ApiErr).status;
      if (status === 401 || status === 403) setStep('connect');
      else fail(e);
    } finally {
      setBusy(false);
    }
  }, [plan.key, paypalClientId, fail]);

  useEffect(() => {
    if (step === 'decide') decide();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [step]);

  const connect = useCallback(async () => {
    setBusy(true); setError('');
    try {
      await loginWithPurpose('subscribe');
      setStep('decide');
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }, []);

  // Render PayPal buttons when entering the pay step (never again once approved).
  useEffect(() => {
    if (step !== 'pay' || !pay || !payRef.current || subscribed) return;
    let buttons: any = null;
    let cancelled = false;
    setError('');
    loadPayPalSdk(pay.clientId)
      .then((paypal) => {
        if (cancelled || !payRef.current) return;
        payRef.current.innerHTML = '';
        buttons = paypal.Buttons({
          style: { shape: 'pill', color: 'gold', layout: 'vertical', label: 'subscribe' },
          createSubscription: (_d: any, actions: any) =>
            pay.mode === 'revise'
              ? actions.subscription.revise(pay.subscriptionId, { plan_id: pay.planId })
              : actions.subscription.create({
                  plan_id: pay.planId,
                  custom_id: pay.draftId,
                  application_context: {
                    brand_name: 'Animica',
                    shipping_preference: 'NO_SHIPPING',
                    user_action: 'SUBSCRIBE_NOW',
                  },
                }),
          onApprove: async (data: any) => {
            // Approved = a real subscription (or revision) exists at PayPal. Retiring the
            // buttons here is what stops a retry from creating and billing a second one.
            setSubscribed(true);
            try { buttons?.close?.(); } catch {}
            const subscriptionId = data.subscriptionID || pay.subscriptionId || '';
            try {
              setBusy(true);
              await api('/confirm', {
                method: 'POST',
                body: JSON.stringify(
                  pay.mode === 'revise' ? { subscriptionId } : { id: pay.draftId, subscriptionId },
                ),
              });
              setDoneIds({ subscriptionId });
              setStep('done');
            } catch (e) {
              track('checkout_error', plan.key);
              setError(
                `Your subscription was approved at PayPal, but we couldn't record it here: ${(e as Error).message}. ` +
                `Do NOT pay again — quote subscription ${subscriptionId} to support and we'll reconcile it. ` +
                'Your plan will usually activate automatically within a few minutes via PayPal webhooks.',
              );
            } finally {
              setBusy(false);
            }
          },
          onError: () => {
            track('checkout_error', plan.key);
            setError('PayPal checkout failed. You have not been charged — please try again.');
          },
          onCancel: () => {
            track('checkout_abandoned', plan.key);
            setError('Checkout cancelled. You can retry whenever you are ready.');
          },
        });
        return buttons.render(payRef.current);
      })
      .catch((e) => fail(e));
    return () => { cancelled = true; try { buttons?.close?.(); } catch {} };
  }, [step, pay, payAttempt, subscribed, plan.key, fail]);

  // Modal ergonomics: Escape closes, background scroll is locked while open.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape' && !busy) onClose(step === 'done' || subscribed); };
    document.addEventListener('keydown', onKey);
    const prev = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => { document.removeEventListener('keydown', onKey); document.body.style.overflow = prev; };
  }, [busy, onClose, step, subscribed]);

  const installed = typeof window !== 'undefined' ? hasWallet() : false;
  const close = () => onClose(step === 'done' || subscribed);

  return (
    <div className="pricing-scrim" onMouseDown={(e) => { if (e.target === e.currentTarget && !busy) close(); }}>
      <div className="pricing-modal" role="dialog" aria-modal="true">
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <div className="ico" style={{ width: 40, height: 40, borderRadius: 11, background: 'linear-gradient(135deg,#232842,#171b2b)', display: 'grid', placeItems: 'center', border: '1px solid var(--border)' }}>{plan.icon}</div>
          <div style={{ minWidth: 0, flex: 1 }}>
            <h3>{pay?.mode === 'revise' ? `Switch to ${plan.name}` : `Get ${plan.name}`}</h3>
            <div className="muted" style={{ fontSize: 13 }}>
              ${usd(plan.priceUsdCents)}/month · billed via PayPal · cancel anytime
            </div>
          </div>
          <button className="btn ghost" onClick={close} disabled={busy} aria-label="Close">✕</button>
        </div>

        {step === 'connect' && (
          <div style={{ marginTop: 14 }}>
            {installed ? (
              <>
                <p className="muted" style={{ fontSize: 13.5, lineHeight: 1.55 }}>
                  Your plan attaches to your Animica wallet identity — the same account that owns your
                  Workers, API keys, listings and .anm names. You&apos;ll sign a single-purpose{' '}
                  <code className="inline">subscribe</code> login message (no transaction, nothing is charged
                  by signing).
                </p>
                <button className="btn primary" style={{ width: '100%', justifyContent: 'center', marginTop: 14 }} onClick={connect} disabled={busy}>
                  {busy ? 'Waiting for wallet…' : 'Connect wallet & continue'}
                </button>
              </>
            ) : (
              <>
                <p className="muted" style={{ fontSize: 13.5, lineHeight: 1.55 }}>
                  Subscribing needs the Animica wallet — plans attach to your wallet identity. Install the
                  browser extension (or open this page in the mobile wallet&apos;s dapp browser), then retry.
                </p>
                <div style={{ display: 'flex', gap: 10, marginTop: 14, flexWrap: 'wrap' }}>
                  <a className="btn primary" href="/browser">Get the extension</a>
                  <button className="btn" onClick={connect} disabled={busy}>I&apos;ve installed it — retry</button>
                </div>
              </>
            )}
            {error && <div className="pricing-err">{error}</div>}
          </div>
        )}

        {step === 'decide' && (
          <div style={{ marginTop: 16 }}>
            <div className="muted" style={{ fontSize: 13.5 }}>{busy ? 'Preparing your checkout…' : ' '}</div>
            {error && <div className="pricing-err">{error}</div>}
            {error && (
              <button className="btn" style={{ marginTop: 10 }} onClick={decide} disabled={busy}>↻ Retry</button>
            )}
          </div>
        )}

        {step === 'pay' && pay && (
          <div style={{ marginTop: 14 }}>
            {pay.mode === 'revise' ? (
              <div className="pricing-okbox">
                Switching your existing subscription to <b>{plan.name}</b>. There is no charge today:
                your current plan stays active for the rest of the period you have already paid for,
                and ${usd(plan.priceUsdCents)}/month begins at your next billing date. Approve the
                change below.
              </div>
            ) : (
              <div className="pricing-okbox">
                Complete the <b>{plan.name}</b> subscription below — ${usd(plan.priceUsdCents)}/month,
                starting today. Cancel anytime.
              </div>
            )}
            <div ref={payRef} style={{ marginTop: 16, minHeight: subscribed ? 0 : 150 }}>
              {!subscribed && <div className="muted" style={{ fontSize: 13 }}>Loading PayPal…</div>}
            </div>
            {busy && <div className="muted" style={{ fontSize: 13, marginTop: 8 }}>Confirming your subscription…</div>}
            {error && <div className="pricing-err">{error}</div>}
            {error && !subscribed && (
              <button className="btn" style={{ marginTop: 10 }} onClick={() => { setError(''); setPayAttempt((n) => n + 1); }}>
                ↻ Retry PayPal checkout
              </button>
            )}
            {subscribed && error && (
              <a className="btn" style={{ marginTop: 10 }} href="/settings/billing">Open Billing settings</a>
            )}
          </div>
        )}

        {step === 'done' && (
          <div style={{ textAlign: 'center', padding: '18px 4px 8px' }}>
            <div style={{ fontSize: 44 }}>🎉</div>
            <h3 style={{ marginTop: 8 }}>You&apos;re on {plan.name}!</h3>
            <p className="muted" style={{ fontSize: 14, marginTop: 8, lineHeight: 1.55 }}>
              Your entitlements are live. Time to put the AI to work.
            </p>
            {doneIds && (
              <div className="pricing-okbox" style={{ textAlign: 'center' }}>
                Subscription <b className="mono">{doneIds.subscriptionId}</b>
              </div>
            )}
            <div style={{ display: 'flex', gap: 10, justifyContent: 'center', marginTop: 16, flexWrap: 'wrap' }}>
              <a className="btn primary" href="/workers">Create a Worker</a>
              <a className="btn" href="/settings/billing">Billing settings</a>
              <button className="btn ghost" onClick={close}>Close</button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
