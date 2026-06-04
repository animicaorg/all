// Typed NOWPayments client.
//
// Surfaces the subset of endpoints the gateway needs:
//   - createPayment / createInvoice  (buy flow)
//   - getPayment / listPayments       (poll for status)
//   - getCustodyBalances              (sell flow: do we have enough?)
//   - createConversion / getConversion (sell flow: top up payout coin)
//   - createMassPayout / getPayout    (sell flow: ship funds to user)
//   - getMinAmount / estimatePrice    (UI quote bounds)
//   - verifyIpnSignature              (webhook hardening)
//
// Endpoints requiring the user JWT (custody/conversion/payout) lazily
// log in once per process and cache the bearer token; the simpler
// payment endpoints only need the API key.
//
// IPv4 is forced because NOWPayments' IP whitelist accepts the server's
// IPv4 address but Node.js's default DNS resolver prefers IPv6, and the
// IPv6 address is not (and cannot easily be) whitelisted. Every
// outbound fetch in this module routes through an explicit IPv4 agent.

import { createHmac } from "node:crypto";
import dns from "node:dns";
import { env } from "./env";

// Force every outbound fetch in the process to resolve A records first.
// NOWPayments' IP whitelist is set up for this server's IPv4 address but
// Node's default DNS order prefers IPv6 (AAAA), which lands us on a
// different egress IP that the whitelist rejects ("Invalid IP").
// Calling this once per cold start is enough; it's process-global.
dns.setDefaultResultOrder("ipv4first");

const PROD_BASE = "https://api.nowpayments.io/v1";
const SANDBOX_BASE = "https://api-sandbox.nowpayments.io/v1";

function baseUrl(): string {
  return env().NOWPAYMENTS_SANDBOX ? SANDBOX_BASE : PROD_BASE;
}

// ---------------------------------------------------------------------------
// Shared types
// ---------------------------------------------------------------------------

export interface NpEstimate {
  currency_from: string;
  amount_from: number;
  currency_to: string;
  estimated_amount: number;
}

export interface NpMinAmount {
  currency_from: string;
  currency_to: string;
  min_amount: number;
  fiat_equivalent?: number;
}

export interface NpPayment {
  payment_id: string;
  invoice_id?: string;
  payment_status:
    | "waiting"
    | "confirming"
    | "confirmed"
    | "sending"
    | "partially_paid"
    | "finished"
    | "failed"
    | "refunded"
    | "expired";
  pay_address: string;
  price_amount: number;
  price_currency: string;
  pay_amount: number;
  actually_paid?: number;
  pay_currency: string;
  order_id: string;
  order_description?: string;
  purchase_id?: string;
  created_at: string;
  updated_at?: string;
  outcome_amount?: number;
  outcome_currency?: string;
}

export interface NpInvoice {
  id: string;
  invoice_url: string;
  order_id: string;
  pay_currency?: string;
  price_amount: string;
  price_currency: string;
  ipn_callback_url?: string;
  success_url?: string;
  cancel_url?: string;
  created_at: string;
}

export interface NpCustodyBalance {
  currency: string;
  amount: number;
  pendingAmount?: number;
}

export interface NpConversion {
  id: string;
  from_currency: string;
  to_currency: string;
  from_amount: number;
  to_amount?: number;
  status: "WAITING" | "CONFIRMING" | "CONFIRMED" | "EXPIRED" | "FAILED" | "FINISHED";
  created_at: string;
  updated_at?: string;
}

export interface NpMassPayoutBatch {
  id: string;                       // batch_withdrawal_id
  status:
    | "WAITING"
    | "PROCESSING"
    | "SENDING"
    | "FINISHED"
    | "EXPIRED"
    | "FAILED"
    | "REJECTED";
  withdrawals: NpPayoutItem[];
  created_at: string;
  updated_at?: string;
}

export interface NpPayoutItem {
  id: string;
  address: string;
  currency: string;
  amount: string | number;
  status: string;
  hash?: string;
  payout_description?: string;
}

// ---------------------------------------------------------------------------
// HTTP plumbing
// ---------------------------------------------------------------------------

async function jsonFetch<T>(
  path: string,
  init: RequestInit & { auth?: "apikey" | "jwt" | "both" },
): Promise<T> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
  };
  if (init.auth === "apikey" || init.auth === "both") {
    headers["x-api-key"] = env().NOWPAYMENTS_API_KEY;
  }
  if (init.auth === "jwt" || init.auth === "both") {
    headers.Authorization = `Bearer ${await jwtToken()}`;
  }
  const res = await fetch(`${baseUrl()}${path}`, {
    ...init,
    headers: { ...headers, ...(init.headers ?? {}) },
  });
  const text = await res.text();
  let body: unknown = null;
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      body = text;
    }
  }
  if (!res.ok) {
    const message = typeof body === "object" && body && "message" in body
      ? (body as { message: unknown }).message
      : res.statusText;
    throw new Error(`NOWPayments ${path} → ${res.status}: ${String(message)}`);
  }
  return body as T;
}

// JWT login is required for custody / conversion / mass-payout endpoints.
// Cache the token for ~4 minutes (NOWPayments tokens are 5-minute lived).
let cachedJwt: { token: string; expiresAt: number } | null = null;

async function jwtToken(): Promise<string> {
  const now = Date.now();
  if (cachedJwt && cachedJwt.expiresAt > now + 10_000) {
    return cachedJwt.token;
  }
  const e = env();
  if (!e.NOWPAYMENTS_EMAIL || !e.NOWPAYMENTS_PASSWORD) {
    throw new Error(
      "NOWPAYMENTS_EMAIL + NOWPAYMENTS_PASSWORD must be set for custody / conversion / payout calls",
    );
  }
  const res = await fetch(`${baseUrl()}/auth`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      email: e.NOWPAYMENTS_EMAIL,
      password: e.NOWPAYMENTS_PASSWORD,
    }),
  });
  if (!res.ok) throw new Error(`NOWPayments /auth → ${res.status}`);
  const body = (await res.json()) as { token: string };
  if (!body.token) throw new Error("NOWPayments /auth missing token");
  cachedJwt = { token: body.token, expiresAt: now + 4 * 60 * 1000 };
  return body.token;
}

// ---------------------------------------------------------------------------
// Payments (buy flow)
// ---------------------------------------------------------------------------

export interface CreatePaymentInput {
  price_amount: number;
  price_currency: string;        // "usd" usually
  pay_currency: string;          // "btc" / "eth" / …
  order_id: string;
  order_description?: string;
  ipn_callback_url?: string;
  success_url?: string;
  cancel_url?: string;
  payout_address?: string;       // optional: forward outcome to a final wallet
  payout_currency?: string;
}

export function createPayment(input: CreatePaymentInput): Promise<NpPayment> {
  return jsonFetch<NpPayment>("/payment", {
    method: "POST",
    auth: "apikey",
    body: JSON.stringify(input),
  });
}

export interface CreateInvoiceInput {
  price_amount: number;
  price_currency: string;
  pay_currency?: string;         // optional — null lets the user pick at NP
  order_id: string;
  order_description?: string;
  ipn_callback_url?: string;
  success_url?: string;
  cancel_url?: string;
  // Forward converted proceeds straight to this address; NOWPayments
  // will auto-convert pay_currency → payout_currency before forwarding.
  payout_address?: string;
  payout_currency?: string;
}

export function createInvoice(input: CreateInvoiceInput): Promise<NpInvoice> {
  return jsonFetch<NpInvoice>("/invoice", {
    method: "POST",
    auth: "apikey",
    body: JSON.stringify(input),
  });
}

export function getPayment(paymentId: string): Promise<NpPayment> {
  return jsonFetch<NpPayment>(`/payment/${paymentId}`, {
    method: "GET",
    auth: "apikey",
  });
}

// ---------------------------------------------------------------------------
// Custody / conversion / payout (sell flow)
// ---------------------------------------------------------------------------

export async function getCustodyBalances(): Promise<NpCustodyBalance[]> {
  // /v1/balance requires BOTH the JWT session AND the merchant
  // x-api-key header (apikey alone gives INVALID_API_KEY, JWT alone
  // gives the same — only the combo works for personal merchant
  // accounts). Response shape varies by account vintage: either a
  // flat `{ btc: { amount, pendingAmount } }` map OR
  // `{ currencies: [{ code, amount }] }`. sub-partner/balance only
  // works for accounts using the sub-partner integration; ours is a
  // plain merchant, so don't try it (404s and pollutes logs).
  const body = await jsonFetch<
    | Record<string, { amount: number; pendingAmount?: number }>
    | { currencies: Array<{ code: string; amount: number }> }
  >("/balance", { method: "GET", auth: "both" });
  if (Array.isArray((body as { currencies?: unknown[] }).currencies)) {
    const v1 = body as { currencies: Array<{ code: string; amount: number }> };
    return v1.currencies.map((c) => ({ currency: c.code, amount: c.amount }));
  }
  return Object.entries(body as Record<string, { amount: number; pendingAmount?: number }>).map(
    ([currency, v]) => ({
      currency,
      amount: v.amount,
      pendingAmount: v.pendingAmount,
    }),
  );
}

export interface CreateConversionInput {
  from_currency: string;          // e.g. "usdt"
  to_currency: string;            // e.g. "btc"
  amount: string | number;        // amount of from_currency to convert
}

export function createConversion(input: CreateConversionInput): Promise<NpConversion> {
  return jsonFetch<NpConversion>("/conversion", {
    method: "POST",
    auth: "both",
    body: JSON.stringify(input),
  });
}

export function getConversion(conversionId: string): Promise<NpConversion> {
  return jsonFetch<NpConversion>(`/conversion/${conversionId}`, {
    method: "GET",
    auth: "both",
  });
}

export interface MassPayoutItemInput {
  address: string;
  currency: string;
  amount: string | number;
  payout_description?: string;
  unique_external_id?: string;
}

export interface CreateMassPayoutInput {
  ipn_callback_url?: string;
  withdrawals: MassPayoutItemInput[];
}

export function createMassPayout(input: CreateMassPayoutInput): Promise<NpMassPayoutBatch> {
  return jsonFetch<NpMassPayoutBatch>("/payout", {
    method: "POST",
    auth: "both",
    body: JSON.stringify(input),
  });
}

export function getMassPayout(batchId: string): Promise<NpMassPayoutBatch> {
  return jsonFetch<NpMassPayoutBatch>(`/payout/${batchId}`, {
    method: "GET",
    auth: "both",
  });
}

// ---------------------------------------------------------------------------
// Quote helpers
// ---------------------------------------------------------------------------

export function estimatePrice(opts: {
  amount: number;
  currency_from: string;
  currency_to: string;
}): Promise<NpEstimate> {
  const qs = new URLSearchParams({
    amount: String(opts.amount),
    currency_from: opts.currency_from,
    currency_to: opts.currency_to,
  }).toString();
  return jsonFetch<NpEstimate>(`/estimate?${qs}`, { method: "GET", auth: "apikey" });
}

export function getMinAmount(opts: {
  currency_from: string;
  currency_to: string;
}): Promise<NpMinAmount> {
  const qs = new URLSearchParams({
    currency_from: opts.currency_from,
    currency_to: opts.currency_to,
  }).toString();
  return jsonFetch<NpMinAmount>(`/min-amount?${qs}`, { method: "GET", auth: "apikey" });
}

// ---------------------------------------------------------------------------
// Webhook (IPN) verification
// ---------------------------------------------------------------------------

/**
 * NOWPayments signs IPN payloads with HMAC-SHA512 of the JSON body keyed
 * by the IPN secret. The `x-nowpayments-sig` header carries the hex digest.
 *
 * Callers MUST pass the raw request body (string), not the parsed JSON,
 * so the canonical serialisation matches what NOWPayments signed.
 */
export function verifyIpnSignature(rawBody: string, signature: string | null): boolean {
  if (!signature) return false;
  const computed = createHmac("sha512", env().NOWPAYMENTS_IPN_SECRET)
    .update(sortJsonString(rawBody))
    .digest("hex");
  return constantTimeEq(computed, signature);
}

function constantTimeEq(a: string, b: string): boolean {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i += 1) {
    diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  }
  return diff === 0;
}

/** NOWPayments expects the body sorted by key (recursively) before signing.
 *  Mirror that here so the HMAC matches. */
function sortJsonString(raw: string): string {
  try {
    const obj = JSON.parse(raw);
    return JSON.stringify(sortObject(obj));
  } catch {
    return raw;
  }
}

function sortObject(input: unknown): unknown {
  if (Array.isArray(input)) return input.map(sortObject);
  if (input && typeof input === "object") {
    const entries = Object.entries(input as Record<string, unknown>);
    entries.sort(([a], [b]) => a.localeCompare(b));
    return Object.fromEntries(entries.map(([k, v]) => [k, sortObject(v)]));
  }
  return input;
}
