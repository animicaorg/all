'use strict';
/**
 * Signed machine-readable error receipts — the "payment-then-service
 * failure" policy. When a downstream service fails AFTER a payment settled
 * (bounded retries exhausted), the payer must walk away with cryptographic
 * evidence of exactly what they paid for and what failed, and the operator
 * must find an incident row waiting for reconciliation/refund.
 *
 * The receipt is HMAC-SHA256 over the canonical JSON of the receipt body
 * (sorted keys, protocol.canonicalJson). HMAC rather than the facilitator
 * key because the gateway may run against a REMOTE facilitator and must
 * never hold that key; the receipt secret is the gateway's own
 * (X402_RECEIPT_HMAC_KEY, 0600 env file). `key_id` (a hash prefix of the
 * secret) lets the operator confirm which key signed a receipt after a
 * rotation without revealing anything.
 *
 * An empty secret yields an EPHEMERAL per-boot key: receipts stay
 * verifiable only while the process lives, and the signer says so loudly —
 * fine for dev, set the env var in production.
 */

const crypto = require('node:crypto');

const { canonicalJson } = require('./protocol');

function keyIdOf(secretBuf) {
  return crypto.createHash('sha256').update(secretBuf).digest('hex').slice(0, 16);
}

function hmacHex(secretBuf, message) {
  return crypto.createHmac('sha256', secretBuf).update(message, 'utf8').digest('hex');
}

/**
 * createReceiptSigner({ secret, now }) ->
 *   { sign(fields) -> receipt, verify(receipt) -> bool, keyId, ephemeral }
 *
 * sign() requires { payment_id, resource, error }; everything else
 * (payer, amount_atomic, asset, network, payment_fingerprint, attempts)
 * is carried verbatim when provided. Output:
 *   { receipt_version: 1, alg: 'HMAC-SHA256', key_id, ts, ...fields,
 *     signature }
 * with `signature` = HMAC over canonicalJson of everything except itself.
 */
function createReceiptSigner({ secret = '', now = Date.now } = {}) {
  const ephemeral = !secret;
  const secretBuf = ephemeral ? crypto.randomBytes(32) : Buffer.from(String(secret), 'utf8');
  const keyId = keyIdOf(secretBuf);

  function sign(fields) {
    for (const req of ['payment_id', 'resource', 'error']) {
      if (fields[req] === undefined || fields[req] === null || fields[req] === '') {
        throw new Error(`receipt requires ${req}`);
      }
    }
    const body = Object.assign(
      {
        receipt_version: 1,
        alg: 'HMAC-SHA256',
        key_id: keyId,
        ts: Math.floor(now() / 1000),
      },
      fields
    );
    return Object.assign({}, body, { signature: hmacHex(secretBuf, canonicalJson(body)) });
  }

  function verify(receipt) {
    if (!receipt || typeof receipt.signature !== 'string') return false;
    const { signature, ...body } = receipt;
    const expected = hmacHex(secretBuf, canonicalJson(body));
    const a = Buffer.from(signature, 'utf8');
    const b = Buffer.from(expected, 'utf8');
    return a.length === b.length && crypto.timingSafeEqual(a, b);
  }

  return { sign, verify, keyId, ephemeral };
}

module.exports = { createReceiptSigner };
