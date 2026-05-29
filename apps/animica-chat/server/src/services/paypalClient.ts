// PayPal REST client.
//
// We cover the slice we need: get a server access token, create a
// subscription approval link, query a subscription's status, cancel
// one, and verify a webhook signature. The full v2 API is much larger;
// this file intentionally does not wrap things we don't use.

import { env, paypalBaseUrl } from '../env';

type CachedToken = { token: string; expiresAt: number };
let tokenCache: CachedToken | null = null;

async function appToken(): Promise<string> {
  if (tokenCache && Date.now() < tokenCache.expiresAt - 30_000) {
    return tokenCache.token;
  }
  if (!env.PAYPAL_CLIENT_ID || !env.PAYPAL_CLIENT_SECRET) {
    throw new Error('PayPal client credentials are not configured');
  }
  const basic = Buffer.from(
    `${env.PAYPAL_CLIENT_ID}:${env.PAYPAL_CLIENT_SECRET}`,
  ).toString('base64');
  const res = await fetch(`${paypalBaseUrl}/v1/oauth2/token`, {
    method: 'POST',
    headers: {
      Authorization: `Basic ${basic}`,
      'Content-Type': 'application/x-www-form-urlencoded',
    },
    body: 'grant_type=client_credentials',
  });
  if (!res.ok) {
    throw new Error(`paypal token failed: ${res.status} ${await res.text().catch(() => '')}`);
  }
  const j = (await res.json()) as { access_token: string; expires_in: number };
  tokenCache = {
    token: j.access_token,
    expiresAt: Date.now() + j.expires_in * 1000,
  };
  return j.access_token;
}

async function paypalFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = await appToken();
  const res = await fetch(`${paypalBaseUrl}${path}`, {
    ...init,
    headers: {
      Authorization: `Bearer ${token}`,
      'Content-Type': 'application/json',
      Accept: 'application/json',
      ...(init.headers || {}),
    },
  });
  if (!res.ok) {
    const body = await res.text().catch(() => '');
    throw new Error(`paypal ${path} failed (${res.status}): ${body.slice(0, 500)}`);
  }
  return (await res.json()) as T;
}

export interface CreateSubscriptionInput {
  planId: string;
  returnUrl: string;
  cancelUrl: string;
  // Optional: surface our user id back to ourselves via custom_id
  // so the activation webhook can reliably match.
  customId: string;
}

export interface CreatedSubscription {
  id: string;
  status: string;
  approveUrl: string;
}

export async function createSubscription(input: CreateSubscriptionInput): Promise<CreatedSubscription> {
  const j = await paypalFetch<{ id: string; status: string; links: Array<{ rel: string; href: string }> }>(
    '/v1/billing/subscriptions',
    {
      method: 'POST',
      body: JSON.stringify({
        plan_id: input.planId,
        custom_id: input.customId,
        application_context: {
          brand_name: 'Animica Chat',
          locale: 'en-US',
          user_action: 'SUBSCRIBE_NOW',
          payment_method: { payer_selected: 'PAYPAL', payee_preferred: 'IMMEDIATE_PAYMENT_REQUIRED' },
          return_url: input.returnUrl,
          cancel_url: input.cancelUrl,
        },
      }),
    },
  );
  const approve = j.links.find((l) => l.rel === 'approve')?.href;
  if (!approve) throw new Error('paypal create_subscription: missing approve link');
  return { id: j.id, status: j.status, approveUrl: approve };
}

export interface PayPalSubscriptionInfo {
  id: string;
  status: string;
  planId?: string;
  startTime?: string;
  nextBillingTime?: string;
  payerEmail?: string;
  payerId?: string;
}

export async function getSubscription(id: string): Promise<PayPalSubscriptionInfo> {
  const j = await paypalFetch<{
    id: string;
    status: string;
    plan_id?: string;
    start_time?: string;
    billing_info?: { next_billing_time?: string };
    subscriber?: { email_address?: string; payer_id?: string };
  }>(`/v1/billing/subscriptions/${id}`);
  return {
    id: j.id,
    status: j.status,
    planId: j.plan_id,
    startTime: j.start_time,
    nextBillingTime: j.billing_info?.next_billing_time,
    payerEmail: j.subscriber?.email_address,
    payerId: j.subscriber?.payer_id,
  };
}

export async function cancelSubscription(id: string, reason = 'user requested'): Promise<void> {
  await paypalFetch<unknown>(`/v1/billing/subscriptions/${id}/cancel`, {
    method: 'POST',
    body: JSON.stringify({ reason }),
  });
}

// Webhook verification using PayPal's verify-webhook-signature endpoint.
// PayPal sends six headers per event; we forward them all and the
// webhook id we registered to get a verification verdict.
export interface VerifyWebhookInput {
  headers: Record<string, string | string[] | undefined>;
  rawBody: string;
}

export async function verifyWebhook(input: VerifyWebhookInput): Promise<boolean> {
  if (!env.PAYPAL_WEBHOOK_ID) return false;
  const header = (k: string) => {
    const v = input.headers[k] ?? input.headers[k.toLowerCase()];
    return Array.isArray(v) ? v[0] : v ?? '';
  };
  try {
    const j = await paypalFetch<{ verification_status: string }>(
      '/v1/notifications/verify-webhook-signature',
      {
        method: 'POST',
        body: JSON.stringify({
          auth_algo: header('paypal-auth-algo'),
          cert_url: header('paypal-cert-url'),
          transmission_id: header('paypal-transmission-id'),
          transmission_sig: header('paypal-transmission-sig'),
          transmission_time: header('paypal-transmission-time'),
          webhook_id: env.PAYPAL_WEBHOOK_ID,
          webhook_event: JSON.parse(input.rawBody),
        }),
      },
    );
    return j.verification_status === 'SUCCESS';
  } catch {
    return false;
  }
}
