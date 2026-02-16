import { env } from "@/src/server/env";

async function paypalAccessToken() {
  const creds = Buffer.from(`${env.PAYPAL_CLIENT_ID}:${env.PAYPAL_SECRET}`).toString("base64");
  const res = await fetch(`${env.PAYPAL_BASE_URL}/v1/oauth2/token`, {
    method: "POST",
    headers: {
      Authorization: `Basic ${creds}`,
      "Content-Type": "application/x-www-form-urlencoded"
    },
    body: "grant_type=client_credentials"
  });
  if (!res.ok) throw new Error(`PayPal token failed: ${res.status}`);
  const data = await res.json();
  return data.access_token as string;
}

export async function createSubscriptionLink(returnUrl: string, cancelUrl: string) {
  if (!env.PAYPAL_PLAN_ID) throw new Error("PAYPAL_PLAN_ID is required");
  const token = await paypalAccessToken();
  const res = await fetch(`${env.PAYPAL_BASE_URL}/v1/billing/subscriptions`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json"
    },
    body: JSON.stringify({
      plan_id: env.PAYPAL_PLAN_ID,
      application_context: {
        brand_name: "Animica Studio",
        user_action: "SUBSCRIBE_NOW",
        return_url: returnUrl,
        cancel_url: cancelUrl
      }
    })
  });
  if (!res.ok) throw new Error(`PayPal subscription failed: ${res.status}`);
  const payload = await res.json();
  return payload.links?.find((l: { rel: string }) => l.rel === "approve")?.href as string;
}

export type ParsedWebhook = {
  subscriptionId: string;
  status: string;
  nextBillingTime?: string;
  payerEmail?: string;
  payerId?: string;
};

export function parseSubscriptionWebhook(event: any): ParsedWebhook | null {
  const subId = event.resource?.id;
  if (!subId) return null;
  return {
    subscriptionId: subId,
    status: event.resource?.status ?? "UNKNOWN",
    nextBillingTime: event.resource?.billing_info?.next_billing_time,
    payerEmail: event.resource?.subscriber?.email_address,
    payerId: event.resource?.subscriber?.payer_id
  };
}

export async function verifyWebhookSignature(headers: Record<string, string>, body: unknown) {
  if (!env.PAYPAL_WEBHOOK_ID) throw new Error("PAYPAL_WEBHOOK_ID missing");
  const token = await paypalAccessToken();
  const payload = {
    auth_algo: headers["paypal-auth-algo"],
    cert_url: headers["paypal-cert-url"],
    transmission_id: headers["paypal-transmission-id"],
    transmission_sig: headers["paypal-transmission-sig"],
    transmission_time: headers["paypal-transmission-time"],
    webhook_id: env.PAYPAL_WEBHOOK_ID,
    webhook_event: body
  };

  const res = await fetch(`${env.PAYPAL_BASE_URL}/v1/notifications/verify-webhook-signature`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json"
    },
    body: JSON.stringify(payload)
  });
  if (!res.ok) return false;
  const data = await res.json();
  return data.verification_status === "SUCCESS";
}
