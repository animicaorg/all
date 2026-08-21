'use strict';
/**
 * THE PAYER — Animica buying somebody else's x402 endpoint.
 *
 * Everything else in this gateway is the merchant side: we quote 402 and
 * collect. This is the opposite direction, and it is the only module in the
 * tree that spends money, so it is written to be boring and auditable.
 *
 * HOW AN x402 PURCHASE WORKS. Call the resource with no payment; it answers
 * 402 with `accepts[]`. Pick a lane, sign an EIP-3009 `transferWithAuthorization`
 * for EXACTLY the offered terms, retry with the signed payload in the payment
 * header. The merchant's facilitator submits the transfer and pays the gas —
 * which is why a spender wallet needs USDC and NO ETH.
 *
 * WHAT THIS MODULE REFUSES TO DO:
 *
 *  - **Sign with the facilitator key.** The key that settles our incoming
 *    payments must never be the key that spends. Reusing it would mean one
 *    confused purchase can drain the float that every one of our own products
 *    depends on to settle. This is checked at construction and again before
 *    every signature, because a config edit must not be able to quietly merge
 *    the two roles.
 *  - **Pay more than it was quoted.** The amount signed is copied from the
 *    merchant's own 402, then checked against the caller's ceiling before
 *    signing. A merchant that raises its price between the quote and the
 *    signature gets a refusal, not a signature.
 *  - **Sign an open-ended authorization.** `validBefore` is minutes away, and
 *    the nonce is fresh random, so a leaked payload is worthless shortly after
 *    it is made.
 */

const crypto = require('node:crypto');
const evm = require('./facilitator-evm/evm.js');
const usdc = require('./facilitator-evm/usdc.js');

class PayerError extends Error {
  constructor(message, code, detail) {
    super(message);
    this.name = 'PayerError';
    this.code = code;
    this.detail = detail || null;
  }
}

function b64(obj) {
  return Buffer.from(JSON.stringify(obj), 'utf8').toString('base64');
}

function decodeHeader(h) {
  return JSON.parse(Buffer.from(String(h), 'base64').toString('utf8'));
}

/** USDC and its friends are 6-decimal; `extra.decimals` wins when declared. */
function atomicToUsd(amount, lane) {
  const n = Number(amount);
  if (!Number.isFinite(n)) return null;
  const d = Number(lane && lane.extra && lane.extra.decimals);
  return n / 10 ** (Number.isFinite(d) ? d : 6);
}

/**
 * Build a payer bound to one key.
 *
 * `forbiddenAddresses` are keys this payer must never be: in practice the
 * facilitator. Passing them in rather than reaching for config keeps the rule
 * testable and makes the refusal explicit at the call site.
 */
function createPayer({ privateKeyHex, forbiddenAddresses = [], fetchImpl = fetch, now = Date.now, timeoutMs = 30000, authTtlSeconds = 600 }) {
  if (!privateKeyHex) throw new PayerError('no spender key configured', 'no_key');
  const key = Buffer.from(String(privateKeyHex).trim().replace(/^0x/, ''), 'hex');
  if (key.length !== 32) throw new PayerError('spender key must be 32 bytes', 'bad_key');
  const address = evm.privateKeyToAddress(key);

  const forbidden = new Set(forbiddenAddresses.filter(Boolean).map((a) => String(a).toLowerCase()));
  function assertNotForbidden() {
    if (forbidden.has(address.toLowerCase())) {
      throw new PayerError(
        `refusing to spend from ${address}: it is also the settlement/facilitator key. `
        + 'The key that settles incoming payments must never be the key that spends, or one bad purchase '
        + 'takes down the rail every product settles through. Fund a separate wallet with USDC only.',
        'spender_is_facilitator',
        { address },
      );
    }
  }
  assertNotForbidden();

  /**
   * Buy one resource. Returns the delivered body plus exactly what was spent.
   *
   * `maxSpendUsd` is a hard ceiling checked AFTER the merchant states its
   * price and BEFORE anything is signed — the only point where refusing is
   * still free.
   */
  async function buy({ resource, method = 'GET', body = null, maxSpendUsd, dryRun = false, chainId = 8453 }) {
    assertNotForbidden();

    const init = { method, headers: { accept: 'application/json' } };
    if (method !== 'GET' && body !== null && body !== undefined) {
      init.headers['content-type'] = 'application/json';
      init.body = typeof body === 'string' ? body : JSON.stringify(body);
    }

    // 1. Unpaid request. A 402 is what we want; anything else is worth saying.
    let r1;
    try {
      r1 = await fetchImpl(resource, { ...init, signal: AbortSignal.timeout(timeoutMs) });
    } catch (e) {
      throw new PayerError(`could not reach ${resource}: ${e.message}`, 'unreachable');
    }
    if (r1.status !== 402) {
      const text = await r1.text().catch(() => '');
      if (r1.status >= 200 && r1.status < 300) {
        return {
          paid: false, spent_usd: 0, status: r1.status,
          note: 'the resource answered without requiring payment, so nothing was spent',
          body: text.slice(0, 200_000),
        };
      }
      throw new PayerError(`${resource} answered ${r1.status}, not a 402 payment challenge`, 'not_paywalled', { status: r1.status });
    }

    const header = r1.headers.get('payment-required') || r1.headers.get('x-payment-required');
    let offer = null;
    if (header) {
      try { offer = decodeHeader(header); } catch { /* fall through to the body */ }
    }
    if (!offer) {
      try { offer = JSON.parse(await r1.text()); } catch { /* handled below */ }
    }
    const accepts = offer && (offer.accepts || offer.paymentRequirements);
    if (!Array.isArray(accepts) || !accepts.length) {
      throw new PayerError(`${resource} answered 402 but published no readable payment terms`, 'no_terms');
    }
    const lane = accepts.find((a) => String(a.network || '').startsWith('eip155:'))
      || accepts.find((a) => String(a.network || '').includes('base'));
    if (!lane) {
      throw new PayerError(
        `${resource} offers no EVM lane we can pay — it wants: ${accepts.map((a) => a.network).join(', ')}`,
        'no_evm_lane',
      );
    }

    const amount = lane.maxAmountRequired ?? lane.amount;
    const priceUsd = atomicToUsd(amount, lane);
    if (priceUsd === null || !(priceUsd > 0)) {
      throw new PayerError(`${resource} quoted an unusable amount (${amount})`, 'bad_amount');
    }
    // The ceiling check happens here, between the quote and the signature —
    // the last moment where refusing costs nothing.
    if (Number.isFinite(maxSpendUsd) && priceUsd > maxSpendUsd) {
      throw new PayerError(
        `${resource} costs $${priceUsd.toFixed(6)}, above the $${Number(maxSpendUsd).toFixed(6)} ceiling for this purchase. Nothing was signed.`,
        'over_ceiling',
        { quoted_usd: priceUsd, ceiling_usd: maxSpendUsd },
      );
    }
    if (dryRun) {
      return {
        paid: false, dry_run: true, spent_usd: 0, quoted_usd: priceUsd,
        would_pay_to: lane.payTo || lane.recipient, network: lane.network, scheme: lane.scheme,
        note: 'dry run: the merchant was asked for its terms and nothing was signed or sent',
      };
    }

    // 2. Sign for EXACTLY the offered terms, with a short-lived authorization.
    const nowSec = Math.floor(now() / 1000);
    const auth = {
      from: address,
      to: lane.payTo || lane.recipient,
      value: BigInt(amount),
      validAfter: 0n,
      validBefore: BigInt(nowSec + authTtlSeconds),
      nonce: '0x' + crypto.randomBytes(32).toString('hex'),
    };
    // transferAuthDigest takes the 32-byte EIP-712 DOMAIN SEPARATOR, not a
    // domain object. Passing the object throws inside Buffer.concat, so every
    // signature would have failed — tests caught it before any money moved.
    const domainSep = evm.domainSeparator({
      name: (lane.extra && lane.extra.name) || 'USD Coin',
      version: (lane.extra && lane.extra.version) || '2',
      chainId: Number(String(lane.network || '').split(':')[1] || chainId),
      verifyingContract: lane.asset,
    });
    const digest = usdc.transferAuthDigest(domainSep, auth);
    const sig = evm.signDigest(digest, key);
    const signature = '0x'
      + Buffer.from(sig.rWord).toString('hex')
      + Buffer.from(sig.sWord).toString('hex')
      + sig.v.toString(16).padStart(2, '0');

    const payload = {
      x402Version: offer.x402Version || 2,
      // The gateway keys its offer on the request PATH, and echoing a full
      // absolute URL is not the same string.
      resource: offer.resource || new URL(resource).pathname,
      accepted: lane,
      payload: {
        signature,
        authorization: {
          from: auth.from, to: auth.to, value: auth.value.toString(),
          validAfter: auth.validAfter.toString(), validBefore: auth.validBefore.toString(),
          nonce: auth.nonce,
        },
      },
    };

    // 3. Retry with payment.
    // ONE payment header only. Sending both `payment-signature` (v2) and
    // `x-payment` (v1) made the gateway take its v1 path with a v2 payload in
    // it and reject the whole thing as invalid_payment_requirements — the
    // response even came back stamped x402Version: 1. Belt and braces is not
    // a virtue when the two belts are different protocol versions.
    const init2 = { ...init, headers: { ...init.headers, 'payment-signature': b64(payload) } };
    let r2;
    try {
      r2 = await fetchImpl(resource, { ...init2, signal: AbortSignal.timeout(timeoutMs) });
    } catch (e) {
      // A signed authorization is loose but unsubmitted. It expires on its own.
      throw new PayerError(
        `${resource} did not answer the paid request: ${e.message}. An authorization was signed but we have no confirmation it was used; it expires in ${authTtlSeconds}s.`,
        'paid_request_failed',
        { quoted_usd: priceUsd, authorization_expires_in_s: authTtlSeconds },
      );
    }
    const text = await r2.text().catch(() => '');
    const settled = Boolean(r2.headers.get('payment-response') || r2.headers.get('x-payment-response'));
    if (r2.status < 200 || r2.status >= 300) {
      throw new PayerError(
        `${resource} rejected the paid request with ${r2.status}${settled ? ' AFTER settling the payment' : ' and reported no settlement'}`,
        settled ? 'paid_but_not_delivered' : 'payment_rejected',
        { status: r2.status, settled, spent_usd: settled ? priceUsd : 0, body: text.slice(0, 500) },
      );
    }
    return {
      paid: true,
      spent_usd: priceUsd,
      settled,
      status: r2.status,
      paid_to: auth.to,
      network: lane.network,
      body: text.slice(0, 200_000),
      content_type: r2.headers.get('content-type') || null,
    };
  }

  return { address, buy, assertNotForbidden };
}

module.exports = { createPayer, PayerError, atomicToUsd, b64, decodeHeader };
