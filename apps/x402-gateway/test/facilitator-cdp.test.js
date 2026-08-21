'use strict';
/**
 * CDP facilitator auth tests.
 *
 * No CDP credentials existed when this was written, so the live service has
 * NOT accepted one of these tokens. What is proven here is everything that can
 * be proven locally: the signature verifies under the matching public key, the
 * claim set matches the documented shape, and the token is bound to exactly one
 * method+URI so a /verify token cannot be replayed against /settle.
 *
 * What remains unproven is whether CDP accepts the format. Prove that with ONE
 * settlement before switching real traffic — a 401 fails every payment while
 * every health check stays green.
 */

const { test } = require('node:test');
const assert = require('node:assert/strict');
const crypto = require('node:crypto');

const { mintJwt, loadSigningKey, cdpAuthProvider, b64url } = require('../src/facilitator-cdp/auth');
const { facilitatorClient, evmAuthHeaderFor } = require('../src/middleware');

/** A CDP-style Ed25519 secret: base64 of seed(32) || publicKey(32). */
function ed25519Secret() {
  const { privateKey, publicKey } = crypto.generateKeyPairSync('ed25519');
  const pkcs8 = privateKey.export({ format: 'der', type: 'pkcs8' });
  const seed = pkcs8.subarray(pkcs8.length - 32);
  const spki = publicKey.export({ format: 'der', type: 'spki' });
  const pub = spki.subarray(spki.length - 32);
  return { secret: Buffer.concat([seed, pub]).toString('base64'), publicKey };
}

function ecSecret() {
  const { privateKey, publicKey } = crypto.generateKeyPairSync('ec', { namedCurve: 'P-256' });
  return { secret: privateKey.export({ format: 'pem', type: 'sec1' }).toString(), publicKey };
}

function parts(jwt) {
  const [h, c, s] = jwt.split('.');
  return {
    header: JSON.parse(Buffer.from(h, 'base64url').toString('utf8')),
    claims: JSON.parse(Buffer.from(c, 'base64url').toString('utf8')),
    signingInput: `${h}.${c}`,
    sig: Buffer.from(s, 'base64url'),
  };
}

// ---------------------------------------------------------------------------
// Key loading
// ---------------------------------------------------------------------------

test('an Ed25519 CDP secret (seed+public, base64) loads as EdDSA', () => {
  const { secret } = ed25519Secret();
  const { alg, key } = loadSigningKey(secret);
  assert.equal(alg, 'EdDSA');
  assert.equal(key.asymmetricKeyType, 'ed25519');
});

test('a bare 32-byte Ed25519 seed also loads', () => {
  const { secret } = ed25519Secret();
  const seedOnly = Buffer.from(secret, 'base64').subarray(0, 32).toString('base64');
  assert.equal(loadSigningKey(seedOnly).alg, 'EdDSA');
});

test('a legacy EC P-256 PEM loads as ES256', () => {
  const { secret } = ecSecret();
  const { alg, key } = loadSigningKey(secret);
  assert.equal(alg, 'ES256');
  assert.equal(key.asymmetricKeyType, 'ec');
});

test('pasting the key ID instead of the secret fails with a message that says so', () => {
  // The likely operator error, and "invalid key" would not tell them which half
  // of the CDP key file they copied.
  assert.throws(() => loadSigningKey('organizations/abc/apiKeys/def'), /expected 64.*or 32|neither a PEM/s);
  assert.throws(() => loadSigningKey(''), /empty/);
  assert.throws(() => loadSigningKey('-----BEGIN PRIVATE KEY-----\nnope\n-----END PRIVATE KEY-----'), /could not be parsed/);
});

// ---------------------------------------------------------------------------
// The token
// ---------------------------------------------------------------------------

test('the Ed25519 signature verifies under the matching public key', () => {
  const { secret, publicKey } = ed25519Secret();
  const jwt = mintJwt({ apiKeyId: 'kid-1', apiKeySecret: secret, method: 'POST', url: 'https://api.cdp.coinbase.com/platform/v2/x402/settle' });
  const p = parts(jwt);
  assert.ok(crypto.verify(null, Buffer.from(p.signingInput, 'utf8'), publicKey, p.sig),
    'a token CDP cannot verify fails every payment');
});

test('the ES256 signature is raw r||s (64 bytes), not DER', () => {
  // Node returns DER unless told otherwise, and every JWT verifier rejects DER.
  // This is the single easiest way to get ES256 wrong.
  const { secret, publicKey } = ecSecret();
  const jwt = mintJwt({ apiKeyId: 'kid-1', apiKeySecret: secret, method: 'POST', url: 'https://api.cdp.coinbase.com/platform/v2/x402/verify' });
  const p = parts(jwt);
  assert.equal(p.sig.length, 64, 'ES256 JWT signatures are exactly 64 bytes of r||s');
  assert.ok(crypto.verify('sha256', Buffer.from(p.signingInput, 'utf8'), { key: publicKey, dsaEncoding: 'ieee-p1363' }, p.sig));
});

test('the claim set matches the documented CDP shape', () => {
  const { secret } = ed25519Secret();
  const now = () => 1_700_000_000_000;
  const jwt = mintJwt({ apiKeyId: 'kid-9', apiKeySecret: secret, method: 'POST', url: 'https://api.cdp.coinbase.com/platform/v2/x402/settle', now });
  const { header, claims } = parts(jwt);
  assert.equal(header.alg, 'EdDSA');
  assert.equal(header.kid, 'kid-9');
  assert.equal(header.typ, 'JWT');
  assert.match(header.nonce, /^[0-9a-f]{32}$/);
  assert.equal(claims.iss, 'cdp');
  assert.equal(claims.sub, 'kid-9');
  assert.deepEqual(claims.aud, ['cdp_service']);
  assert.equal(claims.nbf, 1_700_000_000);
  assert.equal(claims.exp, 1_700_000_120, 'a 2-minute token limits the damage of one leaking');
});

test('the token is bound to ONE method and URI, so /verify cannot be replayed at /settle', () => {
  const { secret } = ed25519Secret();
  const v = parts(mintJwt({ apiKeyId: 'k', apiKeySecret: secret, method: 'POST', url: 'https://api.cdp.coinbase.com/platform/v2/x402/verify' }));
  const s = parts(mintJwt({ apiKeyId: 'k', apiKeySecret: secret, method: 'POST', url: 'https://api.cdp.coinbase.com/platform/v2/x402/settle' }));
  assert.deepEqual(v.claims.uris, ['POST api.cdp.coinbase.com/platform/v2/x402/verify']);
  assert.deepEqual(s.claims.uris, ['POST api.cdp.coinbase.com/platform/v2/x402/settle']);
  assert.notDeepEqual(v.claims.uris, s.claims.uris);
});

test('the URI binding carries no scheme and no query string', () => {
  const { secret } = ed25519Secret();
  const p = parts(mintJwt({ apiKeyId: 'k', apiKeySecret: secret, method: 'post', url: 'https://api.cdp.coinbase.com/platform/v2/x402/settle?x=1' }));
  assert.deepEqual(p.claims.uris, ['POST api.cdp.coinbase.com/platform/v2/x402/settle']);
});

test('two tokens minted in the same second are distinct', () => {
  const { secret } = ed25519Secret();
  const now = () => 1_700_000_000_000;
  const a = mintJwt({ apiKeyId: 'k', apiKeySecret: secret, method: 'POST', url: 'https://x.test/a', now });
  const b = mintJwt({ apiKeyId: 'k', apiKeySecret: secret, method: 'POST', url: 'https://x.test/a', now });
  assert.notEqual(a, b, 'the per-token nonce must make them differ');
});

// ---------------------------------------------------------------------------
// Wiring
// ---------------------------------------------------------------------------

test('no credentials means no provider, so our own facilitator gets no auth header', () => {
  assert.equal(cdpAuthProvider({ cdpApiKeyId: '', cdpApiKeySecret: '' }), null);
  assert.equal(cdpAuthProvider({}), null);
  assert.equal(cdpAuthProvider(null), null);
});

test('the client mints a FRESH token per call, bound to the path it is calling', async () => {
  const { secret } = ed25519Secret();
  const seen = [];
  const fetchImpl = async (url, init) => {
    seen.push({ url, auth: init.headers.authorization });
    return { ok: true, status: 200, json: async () => ({ isValid: true }) };
  };
  const client = facilitatorClient('https://api.cdp.coinbase.com/platform/v2/x402', {
    fetchImpl,
    authHeader: cdpAuthProvider({ cdpApiKeyId: 'k', cdpApiKeySecret: secret }),
  });
  await client.verify({}, {});
  await client.settle({}, {});
  assert.equal(seen.length, 2);
  const uris = seen.map((s) => parts(s.auth.replace(/^Bearer /, '')).claims.uris[0]);
  assert.match(uris[0], /\/verify$/);
  assert.match(uris[1], /\/settle$/);
  assert.notEqual(seen[0].auth, seen[1].auth, 'a token must not be reused across endpoints');
});

test('a 401 from the facilitator is reported as a CREDENTIAL failure, not an outage', async () => {
  // "facilitator settle http 401" reads as the service being down. It is not:
  // it is every payment failing, silently, while health checks stay green.
  const fetchImpl = async () => ({ ok: false, status: 401, json: async () => ({}) });
  const client = facilitatorClient('https://api.cdp.coinbase.com/platform/v2/x402', { fetchImpl });
  await assert.rejects(() => client.settle({}, {}), /rejected our credentials.*X402_CDP_API_KEY_ID/s);
});

test('b64url output is unpadded and URL-safe', () => {
  assert.equal(b64url(Buffer.from([251, 255, 190])), '-_--');
  assert.ok(!b64url(Buffer.from('any padding here')).includes('='));
});

// ---------------------------------------------------------------------------
// The bug this section exists for
// ---------------------------------------------------------------------------

test('EVERY facilitator construction site gets credentials, not just middleware', () => {
  // THE LIVE FAILURE, 2026-08-19. Auth was wired into `facilitatorFor` in
  // middleware.js, but `paywall.js` builds its OWN client and that is the one a
  // paid request actually uses. Result: preflight passed, the gateway booted
  // logging `facilitator_mode: remote`, and the first real payment 401'd —
  // "facilitator_unreachable" — because the verifying client sent no
  // Authorization header. Exactly the two-construction-sites shape as the
  // two-payTo-variables incident.
  const fs = require('node:fs');
  const path = require('node:path');
  const APP = path.join(__dirname, '..');
  for (const f of ['src/middleware.js', 'src/paywall.js']) {
    const text = fs.readFileSync(path.join(APP, f), 'utf8');
    if (!/facilitatorClient\(/.test(text)) continue;
    assert.match(text, /evmAuthHeaderFor\(/,
      `${f} builds a facilitator client but never calls evmAuthHeaderFor — it would send no credentials`);
  }
});

test('the paywall\'s facilitator client sends an Authorization header when CDP is configured', async () => {
  const { secret } = ed25519Secret();
  const cfg = { facilitatorMode: 'remote', cdpApiKeyId: 'k', cdpApiKeySecret: secret };
  const authHeader = evmAuthHeaderFor(cfg);
  assert.ok(authHeader, 'remote + credentials must yield a provider');

  let seen = null;
  const fetchImpl = async (url, init) => {
    seen = init.headers.authorization;
    return { ok: true, status: 200, json: async () => ({ isValid: true }) };
  };
  await facilitatorClient('https://api.cdp.coinbase.com/platform/v2/x402', { fetchImpl, authHeader })
    .verify({}, {});
  assert.match(seen || '', /^Bearer ey/, 'the verifying client must present a JWT');
});

test('self mode sends no Authorization header to our own loopback facilitator', () => {
  assert.equal(evmAuthHeaderFor({ facilitatorMode: 'self', cdpApiKeyId: 'k', cdpApiKeySecret: 'x' }), null);
});

// ---------------------------------------------------------------------------
// Remote settlements must stay visible to the operator
// ---------------------------------------------------------------------------

test('a payment settled REMOTELY is recorded locally, or revenue reporting goes blind', async () => {
  // Our own facilitator writes every settlement to its payments ledger, and
  // that DB is what `animica-x402 settlements|revenue|reconcile` read. Once the
  // USDC lane settles at CDP nothing local records the sale, so the operator
  // sees the last self-settled payment and reads it as "nothing has sold".
  const { buildTestGateway, paidRequest } = require('./gateway-helpers');
  const t = await buildTestGateway({ overrides: { facilitatorMode: 'remote' } });
  try {
    const { paid } = await paidRequest(t.baseUrl, '/x402/qrng/draw');
    assert.equal(paid.status, 200);

    const rows = t.store.listRemoteSettlements(10);
    assert.equal(rows.length, 1, 'the sale must be recorded when a third party settles it');
    assert.equal(rows[0].product, 'qrng');
    assert.ok(rows[0].amount_atomic, 'the amount is what revenue is summed from');
    assert.ok(rows[0].tx, 'the settlement tx is the only way to reconcile against the chain');

    const rev = t.store.remoteRevenue(0);
    assert.equal(rev.length, 1);
    assert.equal(rev[0].product, 'qrng');
    assert.equal(String(rev[0].atomic), String(rows[0].amount_atomic));
  } finally {
    await t.close();
  }
});

test('in self mode nothing is double-recorded — our facilitator already has it', async () => {
  const { buildTestGateway, paidRequest } = require('./gateway-helpers');
  const t = await buildTestGateway();   // default: facilitatorMode 'self'
  try {
    const { paid } = await paidRequest(t.baseUrl, '/x402/qrng/draw');
    assert.equal(paid.status, 200);
    assert.equal(t.store.listRemoteSettlements(10).length, 0,
      'recording a self-settled payment here would double-count it against the facilitator ledger');
  } finally {
    await t.close();
  }
});

/* ------------------------------------------------- Bazaar discovery shape -- */

// We settled real payments through CDP and appeared in 0 of 14,994 Bazaar
// resources. The facilitator indexes what it can see at settle time, and what
// it could see was a payload with the resource key deleted — the first fix for
// CDP's schema rejection dropped the field instead of correcting its shape.
test('the CDP payload carries a schema-correct ResourceInfo, not a bare URL string', async () => {
  const { secret } = ed25519Secret();
  const sent = [];
  const fetchImpl = async (url, init) => {
    sent.push(JSON.parse(init.body));
    return { ok: true, status: 200, json: async () => ({ isValid: true }) };
  };
  const client = facilitatorClient('https://api.cdp.coinbase.com/platform/v2/x402', {
    fetchImpl,
    authHeader: cdpAuthProvider({ cdpApiKeyId: 'k', cdpApiKeySecret: secret }),
  });
  const discovery = {
    resource: {
      url: 'https://animica.dev/x402/qrng/draw',
      description: 'random bytes',
      mimeType: 'application/json',
      serviceName: 'Animica',
    },
    extensions: { bazaar: { discoverable: true, info: { input: {}, output: {} } } },
  };
  // Our own dialect: `resource` is a bare URL string, which matches neither
  // alternative of CDP's payload union.
  const payload = { x402Version: 2, resource: 'https://animica.dev/x402/qrng/draw', accepted: {}, payload: {} };

  await client.verify(payload, {}, discovery);
  await client.settle(payload, {}, null, discovery);

  assert.equal(sent.length, 2);
  for (const body of sent) {
    const p = body.paymentPayload;
    assert.equal(typeof p.resource, 'object', 'a string here is the schema error that cost us the listing');
    assert.equal(p.resource.url, 'https://animica.dev/x402/qrng/draw');
    assert.equal(p.extensions.bazaar.discoverable, true, 'no bazaar block means nothing to index');
    // `accepted` and `payload` are the money and must pass through untouched.
    assert.deepEqual(p.accepted, {});
    assert.deepEqual(p.payload, {});
  }
});

// A payer must not choose the URL we ask a public directory to index under our
// own payTo address.
test('the client resource string is never forwarded to CDP as our metadata', async () => {
  const { secret } = ed25519Secret();
  const sent = [];
  const fetchImpl = async (url, init) => {
    sent.push(JSON.parse(init.body));
    return { ok: true, status: 200, json: async () => ({ isValid: true }) };
  };
  const client = facilitatorClient('https://api.cdp.coinbase.com/platform/v2/x402', {
    fetchImpl,
    authHeader: cdpAuthProvider({ cdpApiKeyId: 'k', cdpApiKeySecret: secret }),
  });
  const hostile = { x402Version: 2, resource: 'https://evil.example/pwn', accepted: {}, payload: {} };

  // No discovery context supplied: the field is dropped, exactly as before.
  await client.verify(hostile, {});
  assert.equal(sent[0].paymentPayload.resource, undefined);

  // With one supplied, OUR url wins — the payer's string never appears.
  await client.verify(hostile, {}, { resource: { url: 'https://animica.dev/x402/qrng/draw' } });
  assert.equal(sent[1].paymentPayload.resource.url, 'https://animica.dev/x402/qrng/draw');
  assert.doesNotMatch(JSON.stringify(sent[1]), /evil\.example/);
});

// Settlement succeeds identically whether the Bazaar block was accepted,
// rejected or ignored, so without this header a catalog can be invisible for
// days while every payment works.
test('the facilitator EXTENSION-RESPONSES header is decoded and logged', async () => {
  const logged = [];
  const responses = { bazaar: { status: 'rejected', rejectedReason: 'missing input schema' } };
  const fetchImpl = async () => ({
    ok: true,
    status: 200,
    headers: { get: (h) => (h === 'extension-responses' ? Buffer.from(JSON.stringify(responses)).toString('base64') : null) },
    json: async () => ({ isValid: true }),
  });
  const client = facilitatorClient('https://api.cdp.coinbase.com/platform/v2/x402', {
    fetchImpl,
    logger: { info: (event, fields) => logged.push({ event, fields }) },
  });
  await client.verify({}, {});
  const entry = logged.find((l) => l.event === 'facilitator_extension_responses');
  assert.ok(entry, 'the only feedback channel for discovery must not be swallowed');
  assert.equal(entry.fields.responses.bazaar.rejectedReason, 'missing input schema');
});

// A facilitator that omits the header (CDP did not emit it as of 2026-08-20,
// x402-foundation/x402#2112) must not break a payment that settled.
test('a missing EXTENSION-RESPONSES header is not an error', async () => {
  const fetchImpl = async () => ({ ok: true, status: 200, json: async () => ({ success: true }) });
  const client = facilitatorClient('https://api.cdp.coinbase.com/platform/v2/x402', {
    fetchImpl,
    logger: { info: () => { throw new Error('nothing to log'); } },
  });
  assert.deepEqual(await client.settle({}, {}), { success: true });
});

// CDP caps ResourceInfo.description at 500 chars and says so by failing the
// whole payload union with "x402V1PaymentPayload requires 'scheme'" — a
// complaint about the other alternative, naming a field we never sent. 24 of
// our 44 product descriptions are longer than that, so an uncapped description
// fails the PAYMENT on more than half the catalog.
test('an over-long description is capped for CDP, and stays whole everywhere else', async () => {
  const { secret } = ed25519Secret();
  const sent = [];
  const fetchImpl = async (url, init) => {
    sent.push(JSON.parse(init.body));
    return { ok: true, status: 200, json: async () => ({ isValid: true }) };
  };
  const client = facilitatorClient('https://api.cdp.coinbase.com/platform/v2/x402', {
    fetchImpl,
    authHeader: cdpAuthProvider({ cdpApiKeyId: 'k', cdpApiKeySecret: secret }),
  });
  const long = `${'Spot ANM price with provenance. '.repeat(40)}tail`;
  assert.ok(long.length > 500, 'fixture must exceed the cap it is testing');
  const resource = { url: 'https://animica.dev/x402/oracle/price', description: long, serviceName: 'Animica' };

  await client.verify({ x402Version: 2, accepted: {}, payload: {} }, {}, { resource });

  const outDesc = sent[0].paymentPayload.resource.description;
  assert.ok(outDesc.length <= 500, `capped to <=500, got ${outDesc.length}`);
  assert.ok(long.startsWith(outDesc.replace(/…$/, '').trim()), 'the cap truncates, it never rewrites');
  // The caller's object is descriptive metadata reused across surfaces; capping
  // it in place would silently shorten the catalog and the 402 as well.
  assert.equal(resource.description, long, 'the CDP cap must not mutate the shared route metadata');
});

test('a description at or under the cap is passed through untouched', async () => {
  const { secret } = ed25519Secret();
  const sent = [];
  const fetchImpl = async (url, init) => {
    sent.push(JSON.parse(init.body));
    return { ok: true, status: 200, json: async () => ({ isValid: true }) };
  };
  const client = facilitatorClient('https://api.cdp.coinbase.com/platform/v2/x402', {
    fetchImpl,
    authHeader: cdpAuthProvider({ cdpApiKeyId: 'k', cdpApiKeySecret: secret }),
  });
  const exact = 'x'.repeat(500);
  await client.verify({ x402Version: 2, accepted: {}, payload: {} }, {},
    { resource: { url: 'https://animica.dev/x402/qrng/draw', description: exact } });
  assert.equal(sent[0].paymentPayload.resource.description, exact, '500 is accepted by CDP, so it must not be trimmed');
});

/* ------------------------------------------- the CDP dialect of `bazaar` -- */

const { buildCdpBazaarExtension } = require('../src/facilitator-cdp/bazaar');

// Our 402 publishes `extensions.bazaar` as FIELD DESCRIPTORS, which is what
// x402scan and 402 Index parse — all 44 products are listed there off exactly
// that shape. CDP reads the same key expecting example VALUES plus a JSON
// Schema, and answers "invalid discovery configuration" in EXTENSION-RESPONSES
// while settling the payment normally. Two consumers, two builders.
test('the CDP bazaar block carries a validating schema, not field descriptors', () => {
  const route = {
    productId: 'holder_snapshot',
    outputSchema: {
      input: {
        type: 'http',
        method: 'POST',
        bodyType: 'json',
        bodyFields: { limit: { type: 'integer', required: true, description: '1..1000' } },
      },
      output: { type: 'json', description: 'ranked holders' },
    },
  };
  const ext = buildCdpBazaarExtension(route);
  assert.ok(ext.schema, 'the schema IS the thing CDP validates; without it the config is invalid');
  assert.equal(ext.schema.$schema, 'https://json-schema.org/draft/2020-12/schema');
  assert.equal(ext.info.input.type, 'http');
  assert.equal(ext.info.input.method, 'POST');
  // Descriptors belong in the schema, never in info — info holds values.
  assert.equal(ext.schema.properties.input.properties.body.properties.limit.type, 'integer');
  assert.deepEqual(ext.schema.properties.input.properties.body.required, ['limit']);
  assert.equal(JSON.stringify(ext.info).includes('required'), false, 'info carries values, not descriptors');
});

// Every property the schema declares for `input` must be one we actually send,
// and vice versa: validation fails on either mismatch.
test('the schema describes exactly the keys info emits', () => {
  const ext = buildCdpBazaarExtension({
    productId: 'random_int',
    outputSchema: {
      input: { type: 'http', method: 'POST', bodyType: 'json', bodyFields: { count: { type: 'integer', required: true } } },
      output: { type: 'json', description: 'ints' },
    },
  });
  const declared = Object.keys(ext.schema.properties.input.properties);
  for (const k of Object.keys(ext.info.input)) {
    assert.ok(declared.includes(k), `info.input.${k} is sent but not described`);
  }
  for (const req of ext.schema.properties.input.required) {
    assert.ok(ext.info.input[req] !== undefined, `schema requires input.${req} but info omits it`);
  }
});

// A plausible-looking invented example is exactly what an agent would then send
// us, so examples come only from VERIFIED sources: a captured response sample,
// or a request body that actually returned 200 from the live paid route.
test('examples come only from verified sources, never invented', () => {
  const shape = {
    input: { type: 'http', method: 'POST', bodyType: 'json', bodyFields: { url: { type: 'string', required: true } } },
    output: { type: 'json', description: 'a score' },
  };
  const withBoth = buildCdpBazaarExtension({ productId: 'random_int', outputSchema: shape });
  const reqOnly = buildCdpBazaarExtension({ productId: 'geo_audit', outputSchema: shape });
  const neither = buildCdpBazaarExtension({ productId: 'not_a_product', outputSchema: shape });

  assert.ok(withBoth.info.input.body, 'a captured sample supplies a real request body');
  assert.ok(withBoth.info.output.example, 'and a real response');
  assert.ok(reqOnly.info.input.body, 'a verified request example stands on its own');
  assert.equal(reqOnly.info.output.example, undefined, 'but does not invent a response');
  assert.equal(neither.info.input.body, undefined, 'nothing verified, nothing shown');
  // Still a valid, useful listing: the declared shape is published either way.
  assert.ok(neither.schema.properties.input.properties.body.properties.url);
  assert.equal(neither.info.output.type, 'json');
});

// CDP's BodyDiscoveryInfo is "body input AND type specification". Declaring the
// type of a body that is not there was rejected as an "invalid discovery
// configuration" on 30 products, while the same products without the orphan key
// were accepted.
test('bodyType is never emitted without a body beside it', () => {
  const shape = {
    input: { type: 'http', method: 'POST', bodyType: 'json', bodyFields: { url: { type: 'string', required: true } } },
    output: { type: 'json', description: 'x' },
  };
  const withBody = buildCdpBazaarExtension({ productId: 'geo_audit', outputSchema: shape });
  const noBody = buildCdpBazaarExtension({ productId: 'not_a_product', outputSchema: shape });
  assert.equal(withBody.info.input.bodyType, 'json');
  assert.equal(noBody.info.input.bodyType, undefined, 'an orphan bodyType invalidates the whole block');
  assert.equal(noBody.schema.properties.input.properties.bodyType, undefined, 'and must not be described either');
});

test('a product declaring no input shape yields no extension at all', () => {
  assert.equal(buildCdpBazaarExtension({ productId: 'x' }), null);
  assert.equal(buildCdpBazaarExtension({ productId: 'x', outputSchema: {} }), null);
});

// A GET product's example query string comes from the captured sample's path.
// A query example must be TYPED, not URL-shaped: parsing `?bytes=32` yields the
// STRING "32" while the schema beside it says integer, so the example fails
// validation against its own schema. That is exactly how the one GET product
// with an example got rejected while the ones with none were accepted.
test('a GET query example is typed, not the string a URL would carry', () => {
  const ext = buildCdpBazaarExtension({
    productId: 'qrng',
    outputSchema: {
      input: { type: 'http', method: 'GET', queryParams: { bytes: { type: 'integer', description: '1..1024' } } },
      output: { type: 'json', description: 'random bytes' },
    },
  });
  assert.equal(ext.info.input.method, 'GET');
  assert.equal(typeof ext.info.input.queryParams.bytes, 'number', 'a string here fails the schema beside it');
  assert.equal(ext.info.input.queryParams.bytes, 32);
  assert.equal(ext.schema.properties.input.properties.queryParams.properties.bytes.type, 'integer');
});
