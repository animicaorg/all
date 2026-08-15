'use strict';
/**
 * The spec's "Protocol" test matrix, end to end over real HTTP.
 *
 * Every other suite tests one layer with the next one mocked. This one wires
 * the REAL pieces together — the production gateway (src/server.js) talking
 * over loopback HTTP to the REAL self-hosted exact-EVM facilitator
 * (src/facilitator-evm/server.js), with REAL EIP-3009 signatures produced by
 * a throwaway in-test payer key — and drives the canonical payment flow:
 *
 *   402 with requirements -> client signs locally -> retry with the payload
 *   -> verify -> settle on chain -> resource delivered + settlement metadata.
 *
 * Only the Base JSON-RPC is a mock (evm-helpers) and only the Animica node
 * RPC is a fake (gateway-helpers). Nothing here touches a network, and no
 * key in this file exists outside the process that generated it.
 *
 * Spec test list covered here, at the product surface:
 *   402 w/o payment | valid unlocks | insufficient | wrong-token |
 *   wrong-chain | wrong-recipient | expired | malformed
 * plus the replay + concurrency lines exercised through the gateway rather
 * than against the facilitator API directly.
 */

const test = require('node:test');
const assert = require('node:assert/strict');
const crypto = require('node:crypto');

const cfgMod = require('../src/config');
const protocol = require('../src/protocol');
const evm = require('../src/facilitator-evm/evm');
const usdc = require('../src/facilitator-evm/usdc');
const { facilitatorClient } = require('../src/middleware');
const { createGateway } = require('../src/server');
const { createGatewayStore } = require('../src/store/gateway');
const { createReceiptSigner } = require('../src/receipts');
const { createEvmFacilitatorServer } = require('../src/facilitator-evm/server');
const { scene, ethSign, kp, quietLogger } = require('./evm-helpers');
const { fakeNodeFetch, chainHandlers, request, BASE } = require('./gateway-helpers');

/** Chain time in the mock is pinned at 1.7e9; windows are relative to it. */
const VALID_AFTER = 1_699_999_000n;
const VALID_BEFORE = 1_700_003_600n;

function listen(server) {
  return new Promise((resolve) => server.listen(0, '127.0.0.1', () => resolve(`http://127.0.0.1:${server.address().port}`)));
}

/**
 * Gateway (real) -> facilitator (real, loopback HTTP) -> mock Base RPC.
 * The gateway's own node RPC is the fake Animica node; its facilitator
 * client is the production HTTP client, not a stub.
 */
async function buildE2E({ rpcOpts = {}, gatewayOverrides = {}, handlers, availabilityTtlMs = 0 } = {}) {
  const sc = scene({ rpcOpts });
  const facServer = createEvmFacilitatorServer(sc.facilitator);
  const facUrl = await listen(facServer);

  const cfg = cfgMod.loadGatewayConfig({}, Object.assign({
    enabled: true,
    networkEvm: BASE,
    // The gateway advertises exactly what the facilitator will accept;
    // anything else and the facilitator refuses on principle (verify.js
    // re-checks requirements against its OWN config).
    usdcAsset: sc.cfg.asset,
    basePayTo: sc.cfg.settlementAddress,
    evmFacilitatorUrl: facUrl,
    resourceBaseUrl: 'http://127.0.0.1:0',
    wanmMint: '', wanmTreasury: '', wanmFeePayerPubkey: '', wanmUsdPrice: '',
  allowRetiredWanmLane: true, // exercise multi-lane rendering; the lane is retired in production
  }, gatewayOverrides));

  const gw = createGateway({
    cfg,
    logger: quietLogger,
    fetchImpl: fakeNodeFetch(handlers || chainHandlers()),
    facilitatorClientFactory: (url) => facilitatorClient(url, { fetchImpl: fetch }),
    gatewayStore: createGatewayStore(':memory:'),
    chainIndex: null, // no address index in the protocol e2e: no sqlite file, no walker
    chainIndexer: null,
    receiptSigner: createReceiptSigner({ secret: 'e2e-receipt-secret' }),
    sleep: async () => {},
    availabilityTtlMs,
  });
  const baseUrl = await listen(gw.server);

  return {
    sc, gw, baseUrl, facUrl,
    /** Rows in the facilitator's persistent replay/settlement store. */
    rows: () => sc.store.db.prepare('SELECT * FROM payments').all(),
    async close() {
      gw.capacity.stop();
      await new Promise((r) => gw.server.close(r));
      await new Promise((r) => facServer.close(r));
      sc.store.close();
    },
  };
}

/**
 * Sign an EIP-3009 authorization exactly as a compliant x402 client would,
 * against the terms the server just offered, and return the
 * PAYMENT-SIGNATURE header value.
 *
 * `acceptedOverrides` tampers with the QUOTED TERMS (what the gateway must
 * catch before the facilitator is ever called); the auth-level knobs tamper
 * with the SIGNED AUTHORIZATION (what only the facilitator can catch).
 */
function signPayment(sc, res402, {
  acceptedOverrides = null,
  to = null,
  value = null,
  validAfter = VALID_AFTER,
  validBefore = VALID_BEFORE,
  nonce = null,
  payerKey = null,
  domain = null,
  tamper = null,
} = {}) {
  const required = protocol.decodeHeader(res402.headers.get('payment-required'));
  const accepted = acceptedOverrides
    ? Object.assign({}, required.accepts[0], acceptedOverrides)
    : required.accepts[0];

  const priv = payerKey || sc.payer.priv;
  const auth = {
    from: evm.privateKeyToAddress(priv),
    to: to || accepted.payTo,
    value: BigInt(value === null ? required.accepts[0].amount : value),
    validAfter,
    validBefore,
    nonce: (nonce || '0x' + crypto.randomBytes(32).toString('hex')).toLowerCase(),
  };
  // The mock chain builds its AuthorizationUsed/Transfer receipt logs from
  // the scene's current authorization, mirroring what the token would emit.
  sc.lastAuth = auth;

  const digest = usdc.transferAuthDigest(domain || sc.domainSepBytes, auth);
  const payload = {
    x402Version: 2,
    resource: required.resource.url,
    accepted,
    payload: {
      signature: ethSign(digest, priv),
      authorization: {
        from: auth.from,
        to: auth.to,
        value: auth.value.toString(),
        validAfter: auth.validAfter.toString(),
        validBefore: auth.validBefore.toString(),
        nonce: auth.nonce,
      },
    },
  };
  if (tamper) tamper(payload);
  return protocol.encodeHeader(payload);
}

const QRNG = '/x402/qrng/draw';

/** 402 -> sign -> retry. Returns { first, paid }. */
async function payFor(e2e, path = QRNG, signOpts = {}, extraHeaders = {}) {
  const first = await request(e2e.baseUrl, path);
  assert.equal(first.status, 402, `expected a 402 offer, got ${first.status}: ${first.text}`);
  const paid = await request(e2e.baseUrl, path, {
    headers: Object.assign({ 'payment-signature': signPayment(e2e.sc, first, signOpts) }, extraHeaders),
  });
  return { first, paid };
}

/* --------------------------------------------- 1. 402 without a payment -- */

test('protocol e2e: an unpaid request is answered 402 with signable Base USDC terms', async () => {
  const e2e = await buildE2E();
  try {
    const rpcCallsBefore = e2e.sc.rpc.calls.length;
    const res = await request(e2e.baseUrl, QRNG);
    assert.equal(res.status, 402);

    const offer = protocol.decodeHeader(res.headers.get('payment-required'));
    assert.equal(offer.x402Version, 2);
    assert.equal(offer.accepts.length, 1, 'only the Base USDC lane is configured here');
    assert.deepEqual(offer.accepts[0], {
      scheme: 'exact',
      network: 'eip155:8453',
      amount: '50000', // $0.05 USDC, 6 decimals, integer atomic units
      asset: e2e.sc.cfg.asset,
      payTo: e2e.sc.cfg.settlementAddress,
      maxTimeoutSeconds: 60,
    });
    // the v1 body rides along for legacy clients
    assert.equal(res.json.x402Version, 1);
    assert.equal(res.json.accepts[0].maxAmountRequired, '50000');
    assert.equal(res.json.accepts[0].network, 'base');

    // nothing was sold and nothing was asked of the chain
    assert.equal(res.json.randomness, undefined);
    assert.equal(e2e.sc.rpc.calls.length, rpcCallsBefore, 'an unpaid 402 never touches the facilitator/chain');
    assert.equal(e2e.rows().length, 0);
  } finally {
    await e2e.close();
  }
});

/* -------------------------------------------------- 2. a valid payment -- */

test('protocol e2e: a valid EIP-3009 payment unlocks the product and settles on chain', async () => {
  const e2e = await buildE2E();
  try {
    const { paid } = await payFor(e2e);
    assert.equal(paid.status, 200, paid.text);

    // the product was actually delivered
    assert.equal(typeof paid.json.randomness, 'string');
    assert.ok(paid.json.randomness.length > 0);

    // settlement metadata travels on the v2 channel
    const receipt = protocol.decodeHeader(paid.headers.get('payment-response'));
    assert.equal(receipt.success, true);
    assert.equal(receipt.network, 'eip155:8453');
    assert.equal(receipt.payer, e2e.sc.payer.address, 'payer recovered from the real signature');
    assert.match(receipt.transaction, /^0x[0-9a-f]{64}$/);
    assert.equal(receipt.amount, '50000');

    // ...and inside the JSON body as payment metadata
    assert.equal(paid.json.payment.amount_atomic, '50000');
    assert.equal(paid.json.payment.asset, e2e.sc.cfg.asset);
    assert.equal(paid.json.payment.settlement_tx, receipt.transaction);

    // exactly one broadcast, one settled row, correct money
    assert.equal(e2e.sc.rpc.sent.length, 1);
    const rows = e2e.rows();
    assert.equal(rows.length, 1);
    assert.equal(rows[0].status, 'settled');
    assert.equal(rows[0].amount, '50000');
    assert.equal(rows[0].payer, e2e.sc.payer.address);
    assert.equal(rows[0].asset, e2e.sc.cfg.asset);
    assert.equal(rows[0].network, 'eip155:8453');
    assert.equal(rows[0].settlement_tx_hash, receipt.transaction);
    assert.equal(e2e.sc.store.settledRevenueAtomic(), 50_000n);
    assert.match(e2e.gw.renderMetrics(), /x402_revenue_usdc\{product="qrng"\} 0\.05/);
  } finally {
    await e2e.close();
  }
});

/* ------------------------------------------------------ 3. insufficient -- */

test('protocol e2e: insufficient payment is refused — signed short AND quoted short', async () => {
  const e2e = await buildE2E();
  try {
    // (a) terms accepted verbatim, but the signed authorization pays less.
    //     Only the facilitator can see this; it must reject before any tx.
    const short = await payFor(e2e, QRNG, { value: '9999' });
    assert.equal(short.paid.status, 402);
    assert.equal(short.paid.json.randomness, undefined);
    assert.match(
      protocol.decodeHeader(short.paid.headers.get('payment-required')).error,
      /authorization_value_mismatch/
    );

    // (b) the client edits the QUOTED price down. The gateway compares against
    //     its own freshly built offer, so this dies before the facilitator.
    const before = e2e.sc.rpc.calls.length;
    const cheap = await payFor(e2e, QRNG, { acceptedOverrides: { amount: '1' }, value: '1' });
    assert.equal(cheap.paid.status, 402);
    assert.equal(e2e.sc.rpc.calls.length, before, 'tampered terms never reach verify');

    // (c) payer simply cannot afford the draw.
    const poor = kp();
    e2e.sc.rpc.state.balances[poor.address.toLowerCase()] = 9_999n;
    const broke = await payFor(e2e, QRNG, { payerKey: poor.priv });
    assert.equal(broke.paid.status, 402);
    assert.match(
      protocol.decodeHeader(broke.paid.headers.get('payment-required')).error,
      /insufficient_funds/
    );

    assert.equal(e2e.sc.rpc.sent.length, 0, 'no underpayment ever broadcast');
    assert.equal(e2e.rows().length, 0, 'and none of them claimed an authorization');
  } finally {
    await e2e.close();
  }
});

/* -------------------------------------------------------- 4. wrong token -- */

test('protocol e2e: wrong token is refused by the gateway and again by the facilitator', async () => {
  const e2e = await buildE2E();
  try {
    const impostor = '0x' + '44'.repeat(20);
    const before = e2e.sc.rpc.calls.length;
    const { paid } = await payFor(e2e, QRNG, { acceptedOverrides: { asset: impostor } });
    assert.equal(paid.status, 402);
    assert.equal(e2e.sc.rpc.calls.length, before, 'asset substitution dies at the gateway');

    // Defense in depth: even if a compromised gateway forwarded it, the
    // facilitator re-checks requirements against its OWN allowlisted config.
    const direct = await fetch(`${e2e.facUrl}/verify`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(e2e.sc.payment({ asset: impostor })),
    });
    const verdict = await direct.json();
    assert.equal(verdict.isValid, false);
    assert.equal(verdict.invalidReason, 'invalid_payment_requirements');

    assert.equal(e2e.sc.rpc.sent.length, 0);
    assert.equal(e2e.rows().length, 0);
  } finally {
    await e2e.close();
  }
});

/* -------------------------------------------------------- 5. wrong chain -- */

test('protocol e2e: wrong chain is refused by the gateway and again by the facilitator', async () => {
  const e2e = await buildE2E();
  try {
    const before = e2e.sc.rpc.calls.length;
    const { paid } = await payFor(e2e, QRNG, { acceptedOverrides: { network: 'eip155:1' } });
    assert.equal(paid.status, 402);
    assert.equal(e2e.sc.rpc.calls.length, before, 'network substitution dies at the gateway');

    const direct = await fetch(`${e2e.facUrl}/verify`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(e2e.sc.payment({ network: 'eip155:1' })),
    });
    const verdict = await direct.json();
    assert.equal(verdict.isValid, false);
    assert.equal(verdict.invalidReason, 'invalid_network');

    // A signature made over the WRONG chain's EIP-712 domain (same terms,
    // sepolia domain) cannot recover to the payer on mainnet either.
    const sepoliaDomain = evm.domainSeparator({
      name: 'USDC', version: '2', chainId: 84532,
      verifyingContract: cfgMod.EVM_NETWORKS['base-sepolia'].usdc.address,
    });
    const crossDomain = await payFor(e2e, QRNG, { domain: sepoliaDomain });
    assert.equal(crossDomain.paid.status, 402);
    assert.match(
      protocol.decodeHeader(crossDomain.paid.headers.get('payment-required')).error,
      /payload_signature/
    );

    assert.equal(e2e.sc.rpc.sent.length, 0);
    assert.equal(e2e.rows().length, 0);
  } finally {
    await e2e.close();
  }
});

/* ---------------------------------------------------- 6. wrong recipient -- */

test('protocol e2e: wrong recipient is refused — quoted payTo and signed payee alike', async () => {
  const e2e = await buildE2E();
  try {
    const attacker = evm.toChecksumAddress('0x' + '99'.repeat(20));

    // (a) the client rewrites payTo in the quoted terms
    const before = e2e.sc.rpc.calls.length;
    const quoted = await payFor(e2e, QRNG, { acceptedOverrides: { payTo: attacker } });
    assert.equal(quoted.paid.status, 402);
    assert.equal(e2e.sc.rpc.calls.length, before, 'payTo substitution dies at the gateway');

    // (b) terms verbatim, but the SIGNED authorization pays someone else —
    //     only the facilitator's recipient check catches this one.
    const signed = await payFor(e2e, QRNG, { to: attacker });
    assert.equal(signed.paid.status, 402);
    assert.match(
      protocol.decodeHeader(signed.paid.headers.get('payment-required')).error,
      /recipient_mismatch/
    );

    assert.equal(e2e.sc.rpc.sent.length, 0, 'money never moves to a client-supplied address');
    assert.equal(e2e.rows().length, 0);
  } finally {
    await e2e.close();
  }
});

/* ------------------------------------------------------------ 7. expired -- */

test('protocol e2e: an expired (or not-yet-valid, or expiring-mid-settle) authorization is refused', async () => {
  const e2e = await buildE2E();
  try {
    const expired = await payFor(e2e, QRNG, { validBefore: 1_600_000_000n });
    assert.equal(expired.paid.status, 402);
    assert.match(
      protocol.decodeHeader(expired.paid.headers.get('payment-required')).error,
      /valid_before/
    );

    const notYet = await payFor(e2e, QRNG, { validAfter: 1_900_000_000n, validBefore: 1_900_003_600n });
    assert.equal(notYet.paid.status, 402);
    assert.match(
      protocol.decodeHeader(notYet.paid.headers.get('payment-required')).error,
      /valid_after/
    );

    // Expiring inside the settle margin is as unusable as expired: the tx
    // would revert on arrival, so it must never be claimed or broadcast.
    const knife = await payFor(e2e, QRNG, { validBefore: 1_700_000_003n });
    assert.equal(knife.paid.status, 402);
    assert.match(
      protocol.decodeHeader(knife.paid.headers.get('payment-required')).error,
      /valid_before/
    );

    assert.equal(e2e.sc.rpc.sent.length, 0);
    assert.equal(e2e.rows().length, 0);
  } finally {
    await e2e.close();
  }
});

/* ---------------------------------------------------------- 8. malformed -- */

test('protocol e2e: malformed payment payloads are refused, never crash, never serve', async () => {
  const e2e = await buildE2E();
  try {
    const first = await request(e2e.baseUrl, QRNG);
    assert.equal(first.status, 402);

    const cases = {
      'not base64 at all': '@@@not-base64@@@',
      'base64 of non-JSON': Buffer.from('hello', 'utf8').toString('base64'),
      'v1 payload on the v2 header': protocol.encodeHeader({ x402Version: 1, payload: {} }),
      'no accepted terms': protocol.encodeHeader({ x402Version: 2, payload: {} }),
      'authorization missing': signPayment(e2e.sc, first, {
        tamper: (p) => { delete p.payload.authorization; },
      }),
      'signature is not hex': signPayment(e2e.sc, first, {
        tamper: (p) => { p.payload.signature = 'hello'; },
      }),
      'signature is the wrong length': signPayment(e2e.sc, first, {
        tamper: (p) => { p.payload.signature = '0x' + '11'.repeat(64); },
      }),
      'nonce is not 32 bytes': signPayment(e2e.sc, first, {
        tamper: (p) => { p.payload.authorization.nonce = '0x1234'; },
      }),
      'value is a float': signPayment(e2e.sc, first, {
        tamper: (p) => { p.payload.authorization.value = '0.01'; },
      }),
      'from is not an address': signPayment(e2e.sc, first, {
        tamper: (p) => { p.payload.authorization.from = 'anim1notanevmaddress'; },
      }),
      'signature belongs to someone else': signPayment(e2e.sc, first, { payerKey: kp().priv, to: null }),
    };

    for (const [label, header] of Object.entries(cases)) {
      const res = await request(e2e.baseUrl, QRNG, { headers: { 'payment-signature': header } });
      assert.equal(res.status, 402, `${label}: expected 402, got ${res.status} ${res.text.slice(0, 120)}`);
      assert.equal(res.json.randomness, undefined, `${label}: must not serve the product`);
      assert.ok(res.headers.get('payment-required'), `${label}: must re-offer terms`);
    }

    // 'signature belongs to someone else' recovers to a real (funded-less)
    // address, so it can also die on funds — either way, never a settlement.
    assert.equal(e2e.sc.rpc.sent.length, 0, 'no malformed payload ever broadcast');
    assert.equal(e2e.rows().length, 0, 'no malformed payload ever claimed an authorization');
  } finally {
    await e2e.close();
  }
});

/* ------------------------------------------------ replay + concurrency -- */

test('replay e2e: a settled authorization cannot buy a second draw', async () => {
  const e2e = await buildE2E();
  try {
    const nonce = '0x' + 'a7'.repeat(32);
    const first = await request(e2e.baseUrl, QRNG);
    const header = signPayment(e2e.sc, first, { nonce });

    const one = await request(e2e.baseUrl, QRNG, { headers: { 'payment-signature': header } });
    assert.equal(one.status, 200, one.text);

    // byte-identical replay, no Idempotency-Key: the chain nonce is consumed
    // and the DB row is settled, so verify refuses before settle is reached.
    const two = await request(e2e.baseUrl, QRNG, { headers: { 'payment-signature': header } });
    assert.equal(two.status, 402);
    assert.equal(two.json.randomness, undefined);

    assert.equal(e2e.sc.rpc.sent.length, 1, 'exactly one broadcast for one authorization');
    const rows = e2e.rows();
    assert.equal(rows.length, 1, 'exactly one settlement row');
    assert.equal(rows[0].status, 'settled');
    assert.equal(e2e.sc.store.settledRevenueAtomic(), 50_000n, 'a replay never adds revenue');
  } finally {
    await e2e.close();
  }
});

test('concurrency e2e: two simultaneous requests sharing ONE authorization deliver once and settle once', async () => {
  const e2e = await buildE2E();
  try {
    const first = await request(e2e.baseUrl, QRNG);
    const header = signPayment(e2e.sc, first, { nonce: '0x' + 'b3'.repeat(32) });
    const send = () => request(e2e.baseUrl, QRNG, { headers: { 'payment-signature': header } });

    // A genuine race: both requests are in flight before either settles, so
    // both pass verify (the chain nonce is still unused) and both reach the
    // facilitator's atomic claim. The DB is the only arbiter.
    const results = await Promise.all([send(), send(), send()]);
    const served = results.filter((r) => r.status === 200);
    const refused = results.filter((r) => r.status !== 200);

    assert.equal(served.length, 1, `exactly one delivery, got ${results.map((r) => r.status).join(',')}`);
    assert.equal(refused.length, 2);
    for (const r of refused) {
      assert.equal(r.status, 402);
      assert.equal(r.json.randomness, undefined, 'a loser never receives the product');
    }

    assert.equal(e2e.sc.rpc.sent.length, 1, 'exactly one tx broadcast (counting mock RPC)');
    const rows = e2e.rows();
    assert.equal(rows.length, 1, 'exactly one settlement row');
    assert.equal(rows[0].status, 'settled');
    assert.equal(e2e.sc.store.settledRevenueAtomic(), 50_000n, 'charged exactly once');
    assert.match(e2e.gw.renderMetrics(), /x402_settlements_total\{product="qrng"\} 1/);
  } finally {
    await e2e.close();
  }
});

/* ------------------------------------------------- free surfaces intact -- */

test('free surfaces e2e: unpaid routes stay free and paid gating never calls the node', async () => {
  const nodeMethods = [];
  const handlers = chainHandlers();
  const spy = new Proxy(handlers, {
    get(target, prop) {
      if (typeof target[prop] === 'function') {
        return (params) => { nodeMethods.push(prop); return target[prop](params); };
      }
      return target[prop];
    },
    has(target, prop) { return prop in target; },
  });
  // Production availability memoization (registry default), deliberately NOT
  // the 0 the other tests use — this test is about load on the free node.
  const e2e = await buildE2E({ handlers: spy, availabilityTtlMs: 5000 });
  try {
    // Discovery + health are free and never demand payment.
    for (const p of ['/x402', '/.well-known/x402', '/x402/healthz']) {
      const res = await request(e2e.baseUrl, p);
      assert.equal(res.status, 200, p);
      assert.equal(res.headers.get('payment-required'), null, p);
    }
    // Anything the gateway does not own is a 404 — it never inserts itself
    // in front of a free API and starts charging for it.
    const stray = await request(e2e.baseUrl, '/rpc');
    assert.equal(stray.status, 404);
    assert.equal(stray.headers.get('payment-required'), null);

    // Unpaid traffic on a PAID route: the spec requires a readiness probe
    // before any 402 (never sell a service known unavailable), and that probe
    // is a real 8-byte draw. What must hold is that (a) not one random byte is
    // ever delivered unpaid and (b) the probe is memoized, so unpaid traffic
    // cannot hammer the free node through us.
    for (let i = 0; i < 5; i++) {
      const unpaid = await request(e2e.baseUrl, QRNG);
      assert.equal(unpaid.status, 402);
      assert.equal(unpaid.json.randomness, undefined, 'never a free draw');
    }
    // One probe for everything above: 3 catalog/health hits + 5 unpaid 402s.
    const probes = nodeMethods.filter((m) => m === 'rand.quantumRandomBytes').length;
    assert.equal(probes, 1, `unpaid traffic must share ONE cached readiness probe, saw ${probes}`);

    // And when it IS paid, only read-only node methods are used.
    nodeMethods.length = 0;
    const { paid } = await payFor(e2e);
    assert.equal(paid.status, 200, paid.text);
    assert.ok(nodeMethods.includes('rand.quantumRandomBytes'), 'the paid draw is a real fetch');
    for (const m of nodeMethods) {
      assert.match(m, /^(chain\.get|rand\.)/, `paid work must stay read-only, saw ${m}`);
    }
  } finally {
    await e2e.close();
  }
});
