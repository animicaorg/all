/**
 * Subscriptions via NOWPayments (crypto) — and a hook for ANM on-chain payment.
 *
 * Flow: logged-in user → POST /api/billing/nowpayments/create → we open a
 * NOWPayments hosted invoice → user pays in crypto → NOWPayments POSTs a signed
 * IPN to /api/billing/nowpayments/ipn → we verify the HMAC-SHA512 signature and
 * upgrade the user's plan to "pro" for PLAN_DAYS.
 *
 * Needs in studio-host/.env: NOWPAYMENTS_API_KEY (have), NOWPAYMENTS_IPN_SECRET
 * (from the NOWPayments dashboard — required to trust webhooks),
 * NOWPAYMENTS_PRICE_USD (default 10), PLAN_DAYS (default 30).
 */
import crypto from 'node:crypto';

const API = 'https://api.nowpayments.io/v1';
const key = () => process.env.NOWPAYMENTS_API_KEY || '';
const ipnSecret = () => process.env.NOWPAYMENTS_IPN_SECRET || '';
export const PRICE_USD = () => parseFloat(process.env.NOWPAYMENTS_PRICE_USD || '10');
export const PLAN_DAYS = () => parseInt(process.env.PLAN_DAYS || '30', 10);

export function nowpaymentsReady() { return Boolean(key()); }
export function ipnReady() { return Boolean(ipnSecret()); }

export async function createInvoice({ email, base }) {
  const order_id = `studio:${email}:${Date.now()}`;
  const r = await fetch(`${API}/invoice`, {
    method: 'POST',
    headers: { 'x-api-key': key(), 'content-type': 'application/json' },
    body: JSON.stringify({
      price_amount: PRICE_USD(),
      price_currency: 'usd',
      order_id,
      order_description: 'Animica Studio Pro — 1 month',
      ipn_callback_url: `${base}/api/billing/nowpayments/ipn`,
      success_url: `${base}/?paid=1`,
      cancel_url: `${base}/?paid=0`,
    }),
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok || !data.invoice_url) throw new Error(data.message || `NOWPayments invoice failed (HTTP ${r.status})`);
  return { url: data.invoice_url, id: data.id, order_id };
}

/** Verify a NOWPayments IPN: HMAC-SHA512 over the JSON body with keys sorted. */
export function verifyIpn(rawBody, signature) {
  const secret = ipnSecret();
  if (!secret || !signature) return false;
  let obj;
  try { obj = JSON.parse(rawBody); } catch { return false; }
  const sorted = JSON.stringify(sortKeys(obj));
  const expected = crypto.createHmac('sha512', secret).update(sorted).digest('hex');
  const a = Buffer.from(expected);
  const b = Buffer.from(String(signature));
  return a.length === b.length && crypto.timingSafeEqual(a, b);
}

function sortKeys(o) {
  if (Array.isArray(o)) return o.map(sortKeys);
  if (o && typeof o === 'object') {
    const out = {};
    for (const k of Object.keys(o).sort()) out[k] = sortKeys(o[k]);
    return out;
  }
  return o;
}

/** Extract the buyer email from "studio:<email>:<ts>" order ids. */
export function emailFromOrder(orderId) {
  const m = String(orderId || '').match(/^studio:(.+):\d+$/);
  return m ? m[1] : null;
}

/** Payment statuses that should grant access. */
export const PAID_STATUSES = new Set(['finished', 'confirmed']);
