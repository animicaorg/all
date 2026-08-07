// Minimal PayPal REST client for the Hire Me subscriptions (plans, subscriptions, webhook
// verification). Uses the live REST app creds already present in .env.production. Webhook
// verification is done via PayPal's verify-webhook-signature API and FAILS CLOSED when
// HIRE_PAYPAL_WEBHOOK_ID is unset (same posture as buy-sol's on-ramp).

const PAYPAL_ENV = (process.env.PAYPAL_ENV || 'live') === 'sandbox' ? 'sandbox' : 'live';
export const PAYPAL_BASE =
  process.env.PAYPAL_BASE_URL ||
  (PAYPAL_ENV === 'live' ? 'https://api-m.paypal.com' : 'https://api-m.sandbox.paypal.com');
export const PAYPAL_CLIENT_ID = process.env.PAYPAL_CLIENT_ID || '';
const PAYPAL_CLIENT_SECRET = process.env.PAYPAL_CLIENT_SECRET || '';
export const HIRE_PAYPAL_WEBHOOK_ID = process.env.HIRE_PAYPAL_WEBHOOK_ID || '';
export const paypalEnv = PAYPAL_ENV;

export function paypalConfigured(): boolean {
  return !!(PAYPAL_CLIENT_ID && PAYPAL_CLIENT_SECRET);
}

let tokenCache: { token: string; exp: number } | null = null;

async function accessToken(): Promise<string> {
  const now = Date.now();
  if (tokenCache && tokenCache.exp > now + 30_000) return tokenCache.token;
  if (!paypalConfigured()) throw new Error('paypal not configured (PAYPAL_CLIENT_ID/SECRET missing)');
  const res = await fetch(`${PAYPAL_BASE}/v1/oauth2/token`, {
    method: 'POST',
    headers: {
      authorization: 'Basic ' + Buffer.from(`${PAYPAL_CLIENT_ID}:${PAYPAL_CLIENT_SECRET}`).toString('base64'),
      'content-type': 'application/x-www-form-urlencoded',
    },
    body: 'grant_type=client_credentials',
    cache: 'no-store',
  });
  if (!res.ok) throw new Error(`paypal oauth failed: ${res.status} ${await res.text().catch(() => '')}`);
  const j = await res.json();
  tokenCache = { token: j.access_token, exp: now + Math.max(60, Number(j.expires_in || 300)) * 1000 };
  return tokenCache.token;
}

export async function ppFetch(
  method: 'GET' | 'POST' | 'PATCH' | 'DELETE',
  path: string,
  body?: unknown,
): Promise<{ status: number; json: any }> {
  const token = await accessToken();
  const res = await fetch(`${PAYPAL_BASE}${path}`, {
    method,
    headers: {
      authorization: `Bearer ${token}`,
      'content-type': 'application/json',
      // Idempotent-ish create calls; harmless elsewhere.
      prefer: 'return=representation',
    },
    body: body === undefined ? undefined : JSON.stringify(body),
    cache: 'no-store',
  });
  const text = await res.text();
  let json: any = null;
  try { json = text ? JSON.parse(text) : null; } catch { json = { raw: text }; }
  return { status: res.status, json };
}

// ── subscriptions ────────────────────────────────────────────────────────────
export async function getSubscription(id: string): Promise<any | null> {
  const r = await ppFetch('GET', `/v1/billing/subscriptions/${encodeURIComponent(id)}`);
  if (r.status === 404) return null;
  if (r.status >= 400) throw new Error(`paypal getSubscription ${id}: ${r.status} ${JSON.stringify(r.json)}`);
  return r.json;
}

// PayPal's Create Subscription API accepts an inline `plan` object that overrides the stored
// plan's prices while keeping the same plan_id (the response then carries plan_overridden:true).
// Since the create call is built in the buyer's browser, plan_id equality alone proves nothing
// about the money — this asserts the effective amounts match what we quoted.
export function assertSubscriptionMoney(
  sub: any,
  expectedMonthlyUsd: number,
  expectedSetupUsd: number,
): { ok: true } | { ok: false; reason: string } {
  if (sub?.plan_overridden === true) return { ok: false, reason: 'plan prices were overridden' };
  const plan = sub?.plan;
  // No inline plan snapshot => nothing was overridden; the stored plan's prices apply.
  if (!plan) return { ok: true };

  const setup = plan?.payment_preferences?.setup_fee;
  if (setup) {
    if (String(setup.currency_code) !== 'USD') return { ok: false, reason: 'setup fee currency is not USD' };
    if (Number(setup.value) !== expectedSetupUsd) {
      return { ok: false, reason: `setup fee ${setup.value} != ${expectedSetupUsd.toFixed(2)}` };
    }
  }
  const cycles = Array.isArray(plan?.billing_cycles) ? plan.billing_cycles : [];
  for (const c of cycles) {
    const price = c?.pricing_scheme?.fixed_price;
    if (!price) continue;
    if (String(price.currency_code) !== 'USD') return { ok: false, reason: 'billing currency is not USD' };
    if (String(c?.tenure_type).toUpperCase() === 'REGULAR' && Number(price.value) !== expectedMonthlyUsd) {
      return { ok: false, reason: `monthly price ${price.value} != ${expectedMonthlyUsd.toFixed(2)}` };
    }
  }
  return { ok: true };
}

async function subscriptionAction(id: string, action: 'suspend' | 'cancel' | 'activate', reason: string) {
  const r = await ppFetch('POST', `/v1/billing/subscriptions/${encodeURIComponent(id)}/${action}`, { reason });
  // 204 = done; 422 usually means already in the target/terminal state — treat as soft success.
  if (r.status === 204 || r.status === 200) return { ok: true as const };
  if (r.status === 422) return { ok: false as const, soft: true as const, detail: r.json };
  throw new Error(`paypal ${action} ${id}: ${r.status} ${JSON.stringify(r.json)}`);
}
export const suspendSubscription = (id: string, reason = 'Suspended by Animica operator') =>
  subscriptionAction(id, 'suspend', reason);
export const cancelSubscription = (id: string, reason = 'Cancelled by Animica operator') =>
  subscriptionAction(id, 'cancel', reason);
export const activateSubscription = (id: string, reason = 'Reactivated by Animica operator') =>
  subscriptionAction(id, 'activate', reason);

export async function subscriptionTransactions(id: string, sinceMs: number): Promise<any[]> {
  const start = new Date(sinceMs).toISOString();
  const end = new Date(Date.now() + 5 * 60_000).toISOString();
  const r = await ppFetch(
    'GET',
    `/v1/billing/subscriptions/${encodeURIComponent(id)}/transactions?start_time=${encodeURIComponent(start)}&end_time=${encodeURIComponent(end)}`,
  );
  if (r.status >= 400) return [];
  return Array.isArray(r.json?.transactions) ? r.json.transactions : [];
}

// ── catalog products + billing plans (used by scripts/hire-setup.ts + admin) ─
export async function createProduct(name: string, description: string): Promise<string> {
  const r = await ppFetch('POST', '/v1/catalogs/products', {
    name, description, type: 'SERVICE', category: 'SOFTWARE',
  });
  if (r.status >= 400) throw new Error(`paypal createProduct: ${r.status} ${JSON.stringify(r.json)}`);
  return r.json.id as string;
}

export async function createMonthlyPlan(opts: {
  productId: string;
  name: string;
  description: string;
  monthlyUsd: number;
  setupFeeUsd: number;
}): Promise<string> {
  const r = await ppFetch('POST', '/v1/billing/plans', {
    product_id: opts.productId,
    name: opts.name,
    description: opts.description,
    status: 'ACTIVE',
    billing_cycles: [
      {
        frequency: { interval_unit: 'MONTH', interval_count: 1 },
        tenure_type: 'REGULAR',
        sequence: 1,
        total_cycles: 0, // infinite until cancelled
        pricing_scheme: { fixed_price: { value: opts.monthlyUsd.toFixed(2), currency_code: 'USD' } },
      },
    ],
    payment_preferences: {
      auto_bill_outstanding: true,
      setup_fee: { value: opts.setupFeeUsd.toFixed(2), currency_code: 'USD' },
      setup_fee_failure_action: 'CANCEL',
      payment_failure_threshold: 2,
    },
  });
  if (r.status >= 400) throw new Error(`paypal createPlan: ${r.status} ${JSON.stringify(r.json)}`);
  return r.json.id as string;
}

export async function getPlan(id: string): Promise<any | null> {
  const r = await ppFetch('GET', `/v1/billing/plans/${encodeURIComponent(id)}`);
  if (r.status === 404) return null;
  if (r.status >= 400) throw new Error(`paypal getPlan: ${r.status} ${JSON.stringify(r.json)}`);
  return r.json;
}

// Cents-priced monthly plan (platform tiers: $9.99 etc. — the hire helper above takes whole
// USD ints). No setup fee on tiers.
export async function createMonthlyPlanCents(opts: {
  productId: string;
  name: string;
  description: string;
  monthlyCents: number;
}): Promise<string> {
  const value = (Math.round(opts.monthlyCents) / 100).toFixed(2);
  const r = await ppFetch('POST', '/v1/billing/plans', {
    product_id: opts.productId,
    name: opts.name,
    description: opts.description,
    status: 'ACTIVE',
    billing_cycles: [
      {
        frequency: { interval_unit: 'MONTH', interval_count: 1 },
        tenure_type: 'REGULAR',
        sequence: 1,
        total_cycles: 0, // infinite until cancelled
        pricing_scheme: { fixed_price: { value, currency_code: 'USD' } },
      },
    ],
    payment_preferences: {
      auto_bill_outstanding: true,
      setup_fee: { value: '0', currency_code: 'USD' },
      setup_fee_failure_action: 'CONTINUE',
      payment_failure_threshold: 2,
    },
  });
  if (r.status >= 400) throw new Error(`paypal createPlanCents: ${r.status} ${JSON.stringify(r.json)}`);
  return r.json.id as string;
}

// Upgrade/downgrade an existing subscription to a different plan. Returns the payer approval
// link when PayPal requires re-approval (the Buttons revise flow normally handles approval
// client-side; the confirm route re-verifies plan + money server-side either way).
export async function reviseSubscription(
  id: string,
  planId: string,
): Promise<{ ok: boolean; approveUrl?: string; detail?: any }> {
  const r = await ppFetch('POST', `/v1/billing/subscriptions/${encodeURIComponent(id)}/revise`, {
    plan_id: planId,
  });
  if (r.status >= 400) return { ok: false, detail: r.json };
  const approve = Array.isArray(r.json?.links) ? r.json.links.find((l: any) => l?.rel === 'approve') : null;
  return { ok: true, approveUrl: approve?.href };
}

// ── webhooks ─────────────────────────────────────────────────────────────────
export async function createWebhook(url: string, eventTypes: string[]): Promise<string> {
  const r = await ppFetch('POST', '/v1/notifications/webhooks', {
    url,
    event_types: eventTypes.map((name) => ({ name })),
  });
  if (r.status >= 400) throw new Error(`paypal createWebhook: ${r.status} ${JSON.stringify(r.json)}`);
  return r.json.id as string;
}

export async function listWebhooks(): Promise<any[]> {
  const r = await ppFetch('GET', '/v1/notifications/webhooks');
  if (r.status >= 400) return [];
  return Array.isArray(r.json?.webhooks) ? r.json.webhooks : [];
}

// Platform-tier subscriptions webhook (separate registration from the hire one).
export const SUBS_PAYPAL_WEBHOOK_ID = process.env.SUBS_PAYPAL_WEBHOOK_ID || '';

// Verify a webhook delivery against a SPECIFIC webhook registration via PayPal's API.
// FAIL CLOSED: no webhook id configured => invalid (callers 503 so PayPal retries).
export async function verifyWebhookSignatureFor(
  webhookId: string,
  headers: Headers,
  event: any,
): Promise<{ valid: boolean; error?: string }> {
  if (!webhookId) return { valid: false, error: 'webhook id not configured' };
  const required = {
    auth_algo: headers.get('paypal-auth-algo'),
    cert_url: headers.get('paypal-cert-url'),
    transmission_id: headers.get('paypal-transmission-id'),
    transmission_sig: headers.get('paypal-transmission-sig'),
    transmission_time: headers.get('paypal-transmission-time'),
  };
  if (Object.values(required).some((v) => !v)) return { valid: false, error: 'missing signature headers' };
  const r = await ppFetch('POST', '/v1/notifications/verify-webhook-signature', {
    ...required,
    webhook_id: webhookId,
    webhook_event: event,
  });
  if (r.status >= 400) return { valid: false, error: `verify call failed: ${r.status}` };
  return { valid: r.json?.verification_status === 'SUCCESS' };
}

// Back-compat wrapper for the hire webhook route.
export async function verifyWebhookSignature(
  headers: Headers,
  event: any,
): Promise<{ valid: boolean; error?: string }> {
  if (!HIRE_PAYPAL_WEBHOOK_ID) return { valid: false, error: 'HIRE_PAYPAL_WEBHOOK_ID not configured' };
  return verifyWebhookSignatureFor(HIRE_PAYPAL_WEBHOOK_ID, headers, event);
}
