'use strict';
/**
 * CDP FACILITATOR AUTHENTICATION.
 *
 * WHY THIS FILE EXISTS, AND WHAT IT CHANGES. Until 2026-08-19 this gateway
 * settled every payment through its own facilitator on loopback, and a guard
 * test in `test/honesty-guards.test.js` existed specifically to keep it that
 * way. The operator has now directed that all endpoints route through the CDP
 * facilitator instead, to get the catalog indexed into the Bazaar — the
 * facilitator indexes an endpoint only after IT settles a payment for one that
 * advertises Bazaar metadata, so being listed there and settling elsewhere are
 * mutually exclusive by design.
 *
 * That is a deliberate, operator-authorised reversal of the original rule. The
 * guard was narrowed to match rather than deleted: third-party settlement is
 * now permitted, but ONLY when named explicitly in configuration, never by
 * default and never as a fallback. `X402_FACILITATOR_MODE` still defaults to
 * `self`, and this module refuses to produce a token unless real credentials
 * are configured.
 *
 * NO SDK. `@coinbase/x402` would do this, but this gateway handles money with
 * three audited dependencies and adding a payment SDK to that surface is a
 * worse trade than implementing a documented JWT. Everything below is Node's
 * own `crypto`.
 *
 * THE TOKEN. CDP authenticates each request with a short-lived JWT bound to the
 * exact method and URI it may be used for:
 *
 *   header  { alg, kid: <api key id>, typ: "JWT", nonce: <random hex> }
 *   claims  { iss: "cdp", sub: <api key id>, aud: ["cdp_service"],
 *             nbf: <now>, exp: <now + 120>,
 *             uris: ["POST api.cdp.coinbase.com/platform/v2/x402/settle"] }
 *
 * The key id is whatever the portal issued. Current CDP Secret API Keys use a
 * bare UUID; older ones use `organizations/<org>/apiKeys/<key>`. Both are just
 * opaque strings here, so both work — the value is only copied into kid/sub.
 *
 * Binding the token to one URI is the property worth preserving carefully: a
 * token minted for `/verify` cannot be replayed against `/settle`. So the URI
 * is derived from the request being made, never from configuration.
 *
 * TWO KEY FORMATS, because CDP issues both:
 *   - Ed25519 (current): the secret is base64, 64 bytes = 32-byte seed followed
 *     by the 32-byte public key. Signed as EdDSA.
 *   - EC P-256 (legacy): the secret is a PEM "EC PRIVATE KEY". Signed as ES256
 *     with IEEE-P1363 (r||s) encoding — NOT DER, which is what `crypto.sign`
 *     returns by default and is the single easiest way to get this wrong.
 *
 * UNVERIFIED AGAINST THE LIVE SERVICE. No CDP credentials existed on this host
 * when this was written, so the JWT structure below follows the documented
 * format and is tested against locally generated keypairs — the signature, the
 * claim set and the URI binding are all checked. What has NOT been proven is
 * that CDP accepts it. Prove that with ONE settlement before switching real
 * traffic; a 401 here fails every payment while health checks stay green,
 * which is exactly how the payTo mismatch broke settlement in August.
 */

const crypto = require('node:crypto');

/** PKCS#8 prefix for an Ed25519 private key: SEQUENCE, version, OID 1.3.101.112. */
const ED25519_PKCS8_PREFIX = Buffer.from('302e020100300506032b657004220420', 'hex');

function b64url(buf) {
  return Buffer.from(buf).toString('base64')
    .replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

/**
 * Turn a configured CDP secret into a signing key plus its JWT algorithm.
 *
 * Throws with a specific message rather than a generic crypto error: an
 * operator pasting the wrong half of a CDP key file is the likely failure, and
 * "invalid key" would not tell them which half.
 */
function loadSigningKey(secret) {
  const s = String(secret || '').trim();
  if (!s) throw new Error('CDP API key secret is empty');

  if (s.includes('BEGIN') && s.includes('PRIVATE KEY')) {
    let key;
    try {
      key = crypto.createPrivateKey(s);
    } catch (e) {
      throw new Error(`CDP API key secret looks like a PEM but could not be parsed: ${e.message}`);
    }
    if (key.asymmetricKeyType !== 'ec') {
      throw new Error(`CDP PEM key must be an EC (P-256) key, got ${key.asymmetricKeyType}`);
    }
    return { key, alg: 'ES256' };
  }

  // Ed25519: base64, 64 bytes (seed || public key). Some exports carry only
  // the 32-byte seed, which is equally usable.
  let raw;
  try {
    raw = Buffer.from(s, 'base64');
  } catch {
    throw new Error('CDP API key secret is neither a PEM nor valid base64');
  }
  if (raw.length !== 64 && raw.length !== 32) {
    throw new Error(
      `CDP API key secret decoded to ${raw.length} bytes; expected 64 (Ed25519 seed+public) `
      + 'or 32 (seed only), or a PEM EC private key. Check you copied the SECRET and not the key id.'
    );
  }
  const seed = raw.subarray(0, 32);
  const der = Buffer.concat([ED25519_PKCS8_PREFIX, seed]);
  const key = crypto.createPrivateKey({ key: der, format: 'der', type: 'pkcs8' });
  return { key, alg: 'EdDSA' };
}

/**
 * Mint a JWT authorising exactly one method+URI.
 *
 * @param method  HTTP method, e.g. 'POST'
 * @param url     absolute URL of the request the token is for
 */
function mintJwt({ apiKeyId, apiKeySecret, method, url, now = Date.now, ttlSeconds = 120 }) {
  if (!apiKeyId) throw new Error('CDP API key id is empty');
  const { key, alg } = loadSigningKey(apiKeySecret);

  const u = new URL(url);
  // CDP binds the token to "<METHOD> <host><path>" with no scheme and no query.
  const uri = `${String(method).toUpperCase()} ${u.host}${u.pathname}`;

  const iat = Math.floor(now() / 1000);
  const header = {
    alg,
    kid: apiKeyId,
    typ: 'JWT',
    // A per-token nonce, so two tokens minted in the same second are distinct.
    nonce: crypto.randomBytes(16).toString('hex'),
  };
  const claims = {
    iss: 'cdp',
    sub: apiKeyId,
    aud: ['cdp_service'],
    nbf: iat,
    iat,
    exp: iat + ttlSeconds,
    uris: [uri],
  };

  const signingInput = `${b64url(JSON.stringify(header))}.${b64url(JSON.stringify(claims))}`;
  let sig;
  if (alg === 'EdDSA') {
    sig = crypto.sign(null, Buffer.from(signingInput, 'utf8'), key);
  } else {
    // ES256 must be raw r||s. Node returns DER unless told otherwise, and a DER
    // signature is silently rejected by every JWT verifier on the far side.
    sig = crypto.sign('sha256', Buffer.from(signingInput, 'utf8'), { key, dsaEncoding: 'ieee-p1363' });
  }
  return `${signingInput}.${b64url(sig)}`;
}

/**
 * An auth-header provider for `facilitatorClient`.
 *
 * Returns null when credentials are absent, which the caller treats as "send no
 * Authorization header" — correct for our own loopback facilitator and for any
 * facilitator that needs none. It never throws for missing credentials, but it
 * DOES throw for malformed ones: a silently unsigned request to CDP would fail
 * every payment with a 401 that looks like an outage rather than a config error.
 */
function cdpAuthProvider(cfg) {
  if (!cfg || !cfg.cdpApiKeyId || !cfg.cdpApiKeySecret) return null;
  return function authHeader(method, url) {
    return `Bearer ${mintJwt({
      apiKeyId: cfg.cdpApiKeyId,
      apiKeySecret: cfg.cdpApiKeySecret,
      method,
      url,
    })}`;
  };
}

module.exports = { mintJwt, loadSigningKey, cdpAuthProvider, b64url };

/**
 * Normalise a payment payload to the x402 v2 schema CDP will accept.
 *
 * OUR DIALECT vs THEIRS. This gateway's clients send
 * `{x402Version, resource, accepted, payload}` where `resource` is a bare URL
 * STRING — ours, and our own facilitator reads it to attribute revenue per
 * product (`facilitator-evm/settlement.js`). The standard v2 payload also has
 * a `resource` slot, but it is a ResourceInfo OBJECT (`{url, description,
 * mimeType, serviceName, tags?, iconUrl?}`). A string where an object belongs
 * matches NEITHER alternative of `[x402V2PaymentPayload, x402V1PaymentPayload]`,
 * producing the singularly unhelpful "x402V1PaymentPayload requires 'scheme'"
 * — a complaint about the wrong alternative, which is why this took bisection
 * against the live API to find.
 *
 * WHY THE STRING IS NOW REPLACED RATHER THAN DROPPED (2026-08-20). The first
 * fix simply deleted the key, which made settlement work and left us invisible:
 * the CDP facilitator indexes a resource into the Bazaar from the metadata it
 * sees at settle time, so a payload with no resource and no extensions gives it
 * nothing to index. We settled real payments through CDP and appeared in 0 of
 * 14,994 Bazaar resources. So instead of dropping the field we send the
 * SCHEMA-CORRECT form of it, plus the `extensions` block (carrying
 * `bazaar.discoverable`) that the standard payload has a slot for.
 *
 * SERVER-AUTHORITATIVE, NOT CLIENT-ECHOED. `resourceInfo` and `extensions` are
 * built from OUR route config by the caller and are byte-identical to what the
 * 402 advertised. The client's `resource` string is never forwarded: a payer
 * must not get to choose the URL we ask a public directory to index under our
 * own payTo address.
 *
 * `accepted` and `payload` pass through untouched: CDP's v2 payload really does
 * use `accepted`, same as ours.
 *
 * Verified against the live CDP facilitator 2026-08-19: with the bare string
 * present the request is a schema error; without it, validation proceeds to the
 * signature (`invalid_payload` for a forged one), which is the shape being
 * accepted.
 *
 * @param {object} paymentPayload  the client's decoded X-PAYMENT
 * @param {object} [discovery]     `{ resource, extensions }` from our own route
 */
function toStandardV2Payload(paymentPayload, discovery) {
  if (!paymentPayload || typeof paymentPayload !== 'object') return paymentPayload;
  const { resource, ...rest } = paymentPayload;
  // A ResourceInfo needs a url to be a ResourceInfo; anything else would be
  // the same schema error in a new costume, so it is left off entirely.
  if (discovery && discovery.resource && typeof discovery.resource.url === 'string') {
    rest.resource = capResourceInfo(discovery.resource);
  }
  if (discovery && discovery.extensions && typeof discovery.extensions === 'object') {
    rest.extensions = discovery.extensions;
  }
  return rest;
}

/**
 * CDP's UNDOCUMENTED ResourceInfo limits.
 *
 * `description` is capped at 500 characters. Nothing says so: over the limit,
 * the whole payload fails the `[x402V2PaymentPayload, x402V1PaymentPayload]`
 * union and CDP reports "x402V1PaymentPayload requires 'scheme'" — a complaint
 * about the OTHER alternative, naming a field we never sent. Found by bisecting
 * the live API on 2026-08-20; the boundary is exact (500 accepted, 501
 * rejected).
 *
 * This matters more than a rounding error: 24 of our 44 product descriptions
 * are longer than 500 characters, so sending them raw would have failed the
 * payment on more than half the catalog. The cap is applied HERE, in the
 * CDP-specific normaliser, and nowhere else — the catalog, the 402 and the
 * OpenAPI document keep the full text, because it is only this one counterparty
 * that cannot take it.
 *
 * Truncation prefers a sentence boundary, then a word boundary, so what a
 * directory shows is a clean shortened description rather than a word sawn in
 * half. `serviceName` (32) and `tags` (5) are enforced upstream by
 * protocol.buildPaymentRequired, so only the description needs shortening.
 */
const CDP_DESCRIPTION_MAX = 500;

function capResourceInfo(info) {
  const d = info.description;
  if (typeof d !== 'string' || d.length <= CDP_DESCRIPTION_MAX) return info;
  const head = d.slice(0, CDP_DESCRIPTION_MAX);
  // Keep the ellipsis inside the budget: a truncation that overshoots the cap
  // it exists to satisfy is worse than none, because it fails silently.
  const sentence = head.lastIndexOf('. ');
  const word = head.lastIndexOf(' ');
  let cut;
  if (sentence > CDP_DESCRIPTION_MAX * 0.6) cut = head.slice(0, sentence + 1);
  else if (word > 0) cut = `${head.slice(0, word).replace(/[,;:—-]$/, '')}…`;
  else cut = head.slice(0, CDP_DESCRIPTION_MAX - 1) + '…';
  return Object.assign({}, info, { description: cut });
}

module.exports.toStandardV2Payload = toStandardV2Payload;
