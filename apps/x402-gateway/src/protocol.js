'use strict';
/**
 * x402 wire shapes. Spec in force is v2 (specs/x402-specification-v2.md,
 * "Protocol Version: 2", 2025-12-09) with the HTTP transport of
 * specs/transports-v2/http.md: protocol data rides in headers as
 * base64-encoded JSON.
 *
 *   server -> client   402 + PAYMENT-REQUIRED: base64(PaymentRequired)
 *   client -> server   PAYMENT-SIGNATURE:      base64(PaymentPayload)
 *   server -> client   PAYMENT-RESPONSE:       base64(SettlementResponse)
 *
 * The older v1 wire (specs/transports-v1/http.md) is still widely deployed:
 * 402 with a JSON *body* {x402Version:1, accepts:[...]} and request header
 * X-PAYMENT / response header X-PAYMENT-RESPONSE. This gateway speaks both:
 * v2 through the headers the spec assigns them, v1 through the body and the
 * X-PAYMENT headers — each version's canonical channel carries that version,
 * so neither client generation sees a mixed dialect.
 */

const HEADER_PAYMENT_REQUIRED = 'payment-required';
const HEADER_PAYMENT_SIGNATURE = 'payment-signature';
const HEADER_PAYMENT_RESPONSE = 'payment-response';
const HEADER_X_PAYMENT = 'x-payment'; // v1 request
const HEADER_X_PAYMENT_RESPONSE = 'x-payment-response'; // v1 response

const { V1_NETWORK_SLUGS } = require('./config');

function encodeHeader(obj) {
  return Buffer.from(JSON.stringify(obj), 'utf8').toString('base64');
}

function decodeHeader(value) {
  return JSON.parse(Buffer.from(String(value), 'base64').toString('utf8'));
}

/**
 * decodeHeader for CLIENT-SUPPLIED headers. Garbage base64 / garbage JSON is
 * a malformed payment, not a server fault: it must produce a structured 402
 * re-offer, never an unhandled exception (which the HTTP layer would answer
 * 500). Kept separate from decodeHeader so our own encode/decode round-trips
 * still fail loudly on a real bug.
 */
function decodeClientHeader(value, what) {
  let obj;
  try {
    obj = JSON.parse(Buffer.from(String(value), 'base64').toString('utf8'));
  } catch (e) {
    throw new PaymentParseError(`${what} is not base64-encoded JSON`);
  }
  if (!obj || typeof obj !== 'object' || Array.isArray(obj)) {
    throw new PaymentParseError(`${what} must decode to a JSON object`);
  }
  return obj;
}

/**
 * v2 PaymentRequired.
 * resource: { url (required), description?, mimeType?, serviceName? (<=32
 * ascii), tags? (<=5), iconUrl? }
 * accepts[]: PaymentRequirements { scheme, network (CAIP-2), amount (atomic
 * string), asset, payTo, maxTimeoutSeconds, extra? }
 */
function buildPaymentRequired({ resource, accepts, error, extensions }) {
  if (!resource || typeof resource.url !== 'string') throw new Error('resource.url is required');
  if (resource.serviceName && !/^[\x20-\x7e]{1,32}$/.test(resource.serviceName)) {
    throw new Error('serviceName must be <=32 printable ascii chars');
  }
  if (!Array.isArray(accepts) || accepts.length === 0) throw new Error('accepts must be non-empty');
  for (const a of accepts) validatePaymentRequirements(a);
  const out = { x402Version: 2, resource, accepts };
  if (extensions && typeof extensions === 'object') out.extensions = extensions;
  if (error) out.error = String(error);
  return out;
}

function validatePaymentRequirements(a) {
  for (const field of ['scheme', 'network', 'amount', 'asset', 'payTo']) {
    if (typeof a[field] !== 'string' || a[field] === '') {
      throw new Error(`PaymentRequirements.${field} must be a non-empty string`);
    }
  }
  if (!/^\d+$/.test(a.amount)) throw new Error('amount must be an atomic-unit integer string');
  if (!/^[a-z0-9-]{3,8}:[A-Za-z0-9_.-]{1,32}$/.test(a.network)) {
    throw new Error(`network must be CAIP-2, got ${a.network}`);
  }
  if (typeof a.maxTimeoutSeconds !== 'number' || !(a.maxTimeoutSeconds > 0)) {
    throw new Error('maxTimeoutSeconds must be a positive number');
  }
  return a;
}

/**
 * v2 -> v1 body. v1 renames: amount -> maxAmountRequired, CAIP-2 -> slug,
 * top-level resource object -> per-accepts `resource` string URL, and carries
 * description/mimeType per entry.
 */
function toV1Body(paymentRequired, outputSchema) {
  return {
    x402Version: 1,
    error: paymentRequired.error || 'Payment required',
    accepts: paymentRequired.accepts.map((a) => ({
      scheme: a.scheme,
      network: V1_NETWORK_SLUGS[a.network] || a.network,
      maxAmountRequired: a.amount,
      asset: a.asset,
      payTo: a.payTo,
      resource: paymentRequired.resource.url,
      description: paymentRequired.resource.description || '',
      mimeType: paymentRequired.resource.mimeType || 'application/json',
      // Bazaar-style discovery schema ({input:{type:'http',method,...},output})
      // — indexers (x402scan, CDP Bazaar) require it to know what to send.
      outputSchema: outputSchema || null,
      maxTimeoutSeconds: a.maxTimeoutSeconds,
      extra: a.extra || undefined,
    })),
  };
}

/**
 * Read an inbound payment from a request's headers. Returns null when the
 * request carries no payment, else a normalized v2 PaymentPayload:
 * { x402Version: 2, accepted: PaymentRequirements, payload: <scheme data> }.
 *
 * v1 X-PAYMENT carries {x402Version:1, scheme, network:<slug>, payload}; the
 * chosen requirements are not echoed, so `acceptsForMatch` (what WE offered)
 * is consulted to recover them — the client cannot fabricate cheaper terms
 * because the match source is our own offer, never their input.
 */
function parsePaymentHeaders(headers, acceptsForMatch) {
  const v2 = headers[HEADER_PAYMENT_SIGNATURE];
  if (v2) {
    const payload = decodeClientHeader(v2, 'PAYMENT-SIGNATURE');
    if (payload.x402Version !== 2) throw new PaymentParseError('unsupported x402Version in PAYMENT-SIGNATURE');
    if (!payload.accepted || typeof payload.accepted !== 'object' || Array.isArray(payload.accepted)) {
      throw new PaymentParseError('PAYMENT-SIGNATURE missing accepted requirements');
    }
    return { wireVersion: 2, paymentPayload: payload };
  }
  const v1 = headers[HEADER_X_PAYMENT];
  if (v1) {
    const p = decodeClientHeader(v1, 'X-PAYMENT');
    if (p.x402Version !== 1) throw new PaymentParseError('unsupported x402Version in X-PAYMENT');
    const match = (acceptsForMatch || []).find(
      (a) => a.scheme === p.scheme &&
        (a.network === p.network || V1_NETWORK_SLUGS[a.network] === p.network)
    );
    if (!match) throw new PaymentParseError(`no offered accepts entry matches ${p.scheme}/${p.network}`);
    return {
      wireVersion: 1,
      paymentPayload: { x402Version: 2, accepted: match, payload: p.payload },
    };
  }
  return null;
}

class PaymentParseError extends Error {}

/** Stable stringify (sorted keys) so requirements can be compared verbatim. */
function canonicalJson(value) {
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(',')}]`;
  if (value && typeof value === 'object') {
    const keys = Object.keys(value).filter((k) => value[k] !== undefined).sort();
    return `{${keys.map((k) => `${JSON.stringify(k)}:${canonicalJson(value[k])}`).join(',')}}`;
  }
  return JSON.stringify(value);
}

function requirementsEqual(a, b) {
  return canonicalJson(a) === canonicalJson(b);
}

/** SettlementResponse: {success, transaction ("" on failure), network, payer?, errorReason?, amount?} */
function buildSettlementResponse({ success, transaction, network, payer, errorReason, amount }) {
  const out = { success: Boolean(success), transaction: transaction || '', network };
  if (payer) out.payer = payer;
  if (errorReason) out.errorReason = errorReason;
  if (amount) out.amount = amount;
  return out;
}

module.exports = {
  HEADER_PAYMENT_REQUIRED,
  HEADER_PAYMENT_SIGNATURE,
  HEADER_PAYMENT_RESPONSE,
  HEADER_X_PAYMENT,
  HEADER_X_PAYMENT_RESPONSE,
  encodeHeader,
  decodeHeader,
  decodeClientHeader,
  buildPaymentRequired,
  validatePaymentRequirements,
  toV1Body,
  parsePaymentHeaders,
  PaymentParseError,
  canonicalJson,
  requirementsEqual,
  buildSettlementResponse,
};
