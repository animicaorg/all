// Minimal NOWPayments client — pay-in invoices + IPN verification.
// Mass payouts (custody/conversion) are added in P8.
import { createHmac } from "node:crypto";
import { env } from "../config/env";

const PROD = "https://api.nowpayments.io/v1";
const SANDBOX = "https://api-sandbox.nowpayments.io/v1";

function base(): string {
  return env().NOWPAYMENTS_SANDBOX ? SANDBOX : PROD;
}

export interface NpInvoice {
  id: string;
  invoice_url: string;
  order_id: string;
  price_amount: string;
  price_currency: string;
}

export interface NpPayment {
  payment_id: string;
  payment_status:
    | "waiting" | "confirming" | "confirmed" | "sending"
    | "partially_paid" | "finished" | "failed" | "refunded" | "expired";
  price_amount: number;
  pay_currency: string;
  actually_paid?: number;
  order_id: string;
}

async function npFetch<T>(path: string, init: RequestInit & { method: string }): Promise<T> {
  const res = await fetch(`${base()}${path}`, {
    ...init,
    headers: { "content-type": "application/json", "x-api-key": env().NOWPAYMENTS_API_KEY, ...(init.headers || {}) },
  });
  const text = await res.text();
  if (!res.ok) throw new Error(`NOWPayments ${init.method} ${path} → ${res.status}: ${text.slice(0, 200)}`);
  return (text ? JSON.parse(text) : {}) as T;
}

export function createInvoice(input: {
  priceAmountUsd: number;
  orderId: string;
  orderDescription?: string;
}): Promise<NpInvoice> {
  const e = env();
  return npFetch<NpInvoice>("/invoice", {
    method: "POST",
    body: JSON.stringify({
      price_amount: input.priceAmountUsd,
      price_currency: "usd",
      order_id: input.orderId,
      order_description: input.orderDescription,
      ipn_callback_url: e.NOWPAYMENTS_PUBLIC_CALLBACK_URL || undefined,
      success_url: `${e.NEXT_PUBLIC_APP_URL}/credits?status=success`,
      cancel_url: `${e.NEXT_PUBLIC_APP_URL}/credits?status=cancel`,
    }),
  });
}

export function getPayment(paymentId: string): Promise<NpPayment> {
  return npFetch<NpPayment>(`/payment/${paymentId}`, { method: "GET" });
}

// ---- Mass payouts (require JWT login: NOWPAYMENTS_EMAIL + _PASSWORD) ----
let jwt: { token: string; at: number } | null = null;

async function jwtToken(): Promise<string> {
  const e = env();
  if (!e.NOWPAYMENTS_EMAIL || !e.NOWPAYMENTS_PASSWORD) {
    throw new Error("NOWPAYMENTS_EMAIL + NOWPAYMENTS_PASSWORD required for payouts");
  }
  // NOWPayments JWTs live ~5 min; cache for 4.
  if (jwt && Date.now() - jwt.at < 4 * 60 * 1000) return jwt.token;
  const res = await fetch(`${base()}/auth`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ email: e.NOWPAYMENTS_EMAIL, password: e.NOWPAYMENTS_PASSWORD }),
  });
  if (!res.ok) throw new Error(`NOWPayments auth → ${res.status}`);
  const json: any = await res.json();
  jwt = { token: json.token, at: Date.now() };
  return jwt.token;
}

export interface MassPayoutItem {
  address: string;
  currency: string;
  amount: string | number;
  unique_external_id?: string;
  payout_description?: string;
}

export interface NpMassPayoutBatch {
  id: string;
  status: "WAITING" | "PROCESSING" | "SENDING" | "FINISHED" | "EXPIRED" | "FAILED" | "REJECTED";
}

export async function createMassPayout(input: { withdrawals: MassPayoutItem[]; ipnCallbackUrl?: string }): Promise<NpMassPayoutBatch> {
  const token = await jwtToken();
  const res = await fetch(`${base()}/payout`, {
    method: "POST",
    headers: { "content-type": "application/json", "x-api-key": env().NOWPAYMENTS_API_KEY, authorization: `Bearer ${token}` },
    body: JSON.stringify({ ipn_callback_url: input.ipnCallbackUrl, withdrawals: input.withdrawals }),
  });
  const text = await res.text();
  if (!res.ok) throw new Error(`NOWPayments payout → ${res.status}: ${text.slice(0, 200)}`);
  return JSON.parse(text);
}

export async function getMassPayout(batchId: string): Promise<NpMassPayoutBatch> {
  const token = await jwtToken();
  const res = await fetch(`${base()}/payout/${batchId}`, {
    headers: { "x-api-key": env().NOWPAYMENTS_API_KEY, authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw new Error(`NOWPayments getPayout → ${res.status}`);
  return (await res.json()) as NpMassPayoutBatch;
}

// NOWPayments signs the IPN body with HMAC-SHA512 over the JSON with keys
// sorted recursively. We re-sort the parsed body and compare — no raw body
// capture needed. Returns false if the secret isn't configured.
function sortDeep(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(sortDeep);
  if (value && typeof value === "object") {
    return Object.keys(value as Record<string, unknown>)
      .sort()
      .reduce<Record<string, unknown>>((acc, k) => {
        acc[k] = sortDeep((value as Record<string, unknown>)[k]);
        return acc;
      }, {});
  }
  return value;
}

export function verifyIpnSignature(body: unknown, signature: string | null): boolean {
  const secret = env().NOWPAYMENTS_IPN_SECRET;
  if (!secret || !signature) return false;
  const sorted = JSON.stringify(sortDeep(body));
  const computed = createHmac("sha512", secret).update(sorted).digest("hex");
  // constant-time-ish compare
  if (computed.length !== signature.length) return false;
  let diff = 0;
  for (let i = 0; i < computed.length; i++) diff |= computed.charCodeAt(i) ^ signature.charCodeAt(i);
  return diff === 0;
}
