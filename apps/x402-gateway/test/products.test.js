'use strict';
/**
 * Product-level tests: catalog correctness, QRNG (real captured RPC shape,
 * readiness-before-payment, execute-then-settle ordering), bulk chain
 * (caps before settlement, BigInt-exact amounts, cursor truncation,
 * formats), priority inference (never sells below the capacity floor,
 * activates at threshold, pre-settle re-check).
 */

const { test } = require('node:test');
const assert = require('node:assert/strict');

const protocol = require('../src/protocol');
const { normalizeLastSeenSec, createCapacityGate } = require('../src/capacity');
const bech32m = require('../src/bech32m');
const {
  QRNG_FIXTURE, buildTestGateway, request, paidRequest, chainHandlers, rawBlockJson, textResponse,
} = require('./gateway-helpers');

// ---------------------------------------------------------------- catalog

test('catalog: /x402 and /.well-known/x402 list products with live availability', async () => {
  const t = await buildTestGateway();
  try {
    const a = await request(t.baseUrl, '/x402');
    const b = await request(t.baseUrl, '/.well-known/x402');
    assert.equal(a.status, 200);
    // Discovery-spec §1 identity: the catalog names itself, its provider and
    // the lane it settles on in the human-readable forms a directory quotes.
    assert.equal(a.json.name, 'Animica x402');
    assert.equal(a.json.provider, 'Animica');
    assert.equal(a.json.service_name, t.gw.cfg.serviceName);
    assert.equal(a.json.payment_protocol, 'x402');
    assert.equal(a.json.network, 'base');
    assert.equal(a.json.chain_id, 8453);
    assert.equal(a.json.asset, 'USDC');
    // ...and the machine-exact forms a payer needs, from the same config that
    // builds every 402's accepts entry.
    assert.equal(a.json.network_caip2, 'eip155:8453');
    assert.equal(a.json.asset_address, t.gw.cfg.usdcAsset);
    assert.equal(a.json.pay_to, t.gw.cfg.basePayTo);
    assert.equal(a.json.discovery.well_known, `${t.gw.cfg.resourceBaseUrl}/.well-known/x402`);
    assert.equal(a.json.discovery.openapi, `${t.gw.cfg.resourceBaseUrl}/x402/openapi.json`);
    const ids = a.json.products.map((p) => p.id).sort();
    assert.deepEqual(ids, [
      'bulk_chain', 'chain_address_history', 'chain_batch_balances', 'echo', 'priority_inference', 'qrng',
      'random_bulk', 'random_commit', 'random_int', 'random_pick', 'random_shuffle',
    ]);
    const byId = Object.fromEntries(a.json.products.map((p) => [p.id, p]));
    assert.equal(byId.qrng.available, true);
    assert.equal(byId.qrng.price, '0.01');
    assert.equal(byId.qrng.price_atomic, '10000');
    assert.equal(byId.bulk_chain.available, true);
    assert.equal(byId.bulk_chain.price, '0.02');
    // priority inference: disabled by default => catalog says available:false
    assert.equal(byId.priority_inference.available, false);
    assert.equal(byId.priority_inference.price, '0.02');
    // per-product discovery schema for indexers
    assert.equal(byId.qrng.outputSchema.input.type, 'http');
    assert.ok(byId.bulk_chain.outputSchema.input.queryParams.from);
    // same catalog on the well-known path
    assert.deepEqual(
      b.json.products.map((p) => p.id).sort(),
      a.json.products.map((p) => p.id).sort()
    );
  } finally {
    await t.close();
  }
});

test('catalog: production disables echo (route 410 gone, not listed) unless X402_ENABLE_ECHO=1', async () => {
  const t = await buildTestGateway({ overrides: { env: 'production', echoEnabled: false } });
  try {
    const cat = await request(t.baseUrl, '/x402');
    assert.ok(!cat.json.products.some((p) => p.id === 'echo'));
    const echo = await request(t.baseUrl, '/x402/paid/echo');
    // 410 (retired), not 404 (missing): it was listed with an indexer
    // before the real products existed, so probes must be forwarded.
    assert.equal(echo.status, 410);
    assert.equal(echo.json.catalog, '/.well-known/x402');
  } finally {
    await t.close();
  }
});

test('catalog: qrng shows available:false when the node is down', async () => {
  const t = await buildTestGateway({
    handlers: {
      // no rand.quantumRandomBytes handler -> Method not found -> unavailable
      'chain.getHead': () => ({ height: 100 }),
    },
  });
  try {
    const cat = await request(t.baseUrl, '/x402');
    const qrng = cat.json.products.find((p) => p.id === 'qrng');
    assert.equal(qrng.available, false);
    assert.ok(qrng.unavailable_reason);
  } finally {
    await t.close();
  }
});

// ------------------------------------------------------------------ QRNG

test('qrng: unpaid request gets a spec 402 with $0.01 terms and v1 outputSchema', async () => {
  const t = await buildTestGateway();
  try {
    const res = await request(t.baseUrl, '/x402/qrng/draw');
    assert.equal(res.status, 402);
    const required = protocol.decodeHeader(res.headers.get('payment-required'));
    assert.equal(required.x402Version, 2);
    assert.equal(required.accepts[0].amount, '10000'); // $0.01 at 6 decimals
    assert.equal(required.accepts[0].network, 'eip155:8453');
    // v1 body carries outputSchema for x402scan-class indexers
    assert.equal(res.json.x402Version, 1);
    assert.equal(res.json.accepts[0].outputSchema.input.type, 'http');
    assert.equal(res.json.accepts[0].maxAmountRequired, '10000');
  } finally {
    await t.close();
  }
});

test('qrng: price is config-driven (X402_QRNG_PRICE_USDC)', async () => {
  const t = await buildTestGateway({ overrides: { qrngPriceUsd: '0.02' } });
  try {
    const res = await request(t.baseUrl, '/x402/qrng/draw');
    const required = protocol.decodeHeader(res.headers.get('payment-required'));
    assert.equal(required.accepts[0].amount, '20000');
  } finally {
    await t.close();
  }
});

test('qrng: paid draw returns the REAL RPC fields verbatim + payment metadata + verification pointer', async () => {
  const t = await buildTestGateway();
  try {
    const { paid } = await paidRequest(t.baseUrl, '/x402/qrng/draw?bytes=32');
    assert.equal(paid.status, 200);
    const body = paid.json;
    // fields that actually exist, passed through verbatim
    assert.equal(body.randomness, QRNG_FIXTURE.bytes_hex);
    assert.equal(body.bytes, QRNG_FIXTURE.n);
    assert.deepEqual(body.source, QRNG_FIXTURE.source);
    assert.deepEqual(body.health, QRNG_FIXTURE.health);
    assert.deepEqual(body.attestation, QRNG_FIXTURE.attestation);
    // no fabricated proof surface
    assert.equal(body.proof, undefined);
    assert.equal(body.beacon, undefined);
    // honesty: software fallback today
    assert.equal(body.attestation.attested, false);
    assert.equal(body.verification.attested, false);
    assert.ok(body.verification.rules.length >= 2);
    assert.match(body.verification.verifier, /verify\.js/);
    // payment metadata
    assert.equal(body.payment.amount_atomic, '10000');
    assert.equal(body.payment.price_usd, '0.01');
    assert.match(body.payment.settlement_tx, /^0x0+1$/);
    // settlement receipt header present
    const settlement = protocol.decodeHeader(paid.headers.get('payment-response'));
    assert.equal(settlement.success, true);
    // execute-then-settle: the randomness fetch happened BEFORE settle
    const fetchIdx = t.events.indexOf('node:rand.quantumRandomBytes');
    const settleIdx = t.events.indexOf('fac:settle');
    assert.ok(fetchIdx !== -1 && settleIdx !== -1 && fetchIdx < settleIdx,
      `expected randomness fetch before settle, got ${t.events.join(',')}`);
    assert.equal(t.fac.calls.settle.length, 1);
  } finally {
    await t.close();
  }
});

test('qrng: node down => 503 readiness refusal, no 402, no facilitator calls', async () => {
  const t = await buildTestGateway({ handlers: { 'chain.getHead': () => ({ height: 1 }) } });
  try {
    const res = await request(t.baseUrl, '/x402/qrng/draw');
    assert.equal(res.status, 503);
    assert.equal(res.headers.get('payment-required'), null);
    assert.equal(t.fac.calls.verify.length, 0);
    assert.equal(t.fac.calls.settle.length, 0);
  } finally {
    await t.close();
  }
});

test('qrng: sick entropy source (health.passed=false) => 503, never sold', async () => {
  const sick = JSON.parse(JSON.stringify(QRNG_FIXTURE));
  sick.health.passed = false;
  const t = await buildTestGateway({ handlers: chainHandlers({ qrng: sick }) });
  try {
    const res = await request(t.baseUrl, '/x402/qrng/draw');
    assert.equal(res.status, 503);
    assert.equal(res.headers.get('payment-required'), null);
    assert.equal(t.fac.calls.settle.length, 0);
  } finally {
    await t.close();
  }
});

test('qrng: bad params answer 400 before any payment is requested', async () => {
  const t = await buildTestGateway();
  try {
    const res = await request(t.baseUrl, '/x402/qrng/draw?bytes=999999');
    assert.equal(res.status, 400);
    assert.equal(res.headers.get('payment-required'), null);
  } finally {
    await t.close();
  }
});

test('qrng: POST reads its JSON body — a body parameter is never silently discarded', async () => {
  // 64 bytes so the body value is distinguishable from the 32-byte default.
  const draw = (n) => {
    const buf = Buffer.alloc(n, 7);
    return { bytes_hex: buf.toString('hex'), n, source: QRNG_FIXTURE.source, health: QRNG_FIXTURE.health, attestation: QRNG_FIXTURE.attestation };
  };
  const t = await buildTestGateway({
    handlers: Object.assign(chainHandlers(), { 'rand.quantumRandomBytes': (p) => draw(p.n) }),
  });
  try {
    // 1. the body IS honoured (this used to answer 32 bytes and charge $0.01)
    const { paid } = await paidRequest(t.baseUrl, '/x402/qrng', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ bytes: 256 }),
    });
    assert.equal(paid.status, 200);
    assert.equal(paid.json.bytes, 256);
    assert.equal(paid.json.randomness.length, 512);

    // 2. an out-of-range body value is a 400 BEFORE payment, not a silent 32
    const over = await request(t.baseUrl, '/x402/qrng', {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ bytes: 99999 }),
    });
    assert.equal(over.status, 400);
    assert.equal(over.headers.get('payment-required'), null);

    // 3. unknown body fields are refused rather than ignored
    const unknown = await request(t.baseUrl, '/x402/qrng', {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ byte: 64 }),
    });
    assert.equal(unknown.status, 400);
    assert.match(unknown.json.detail, /unknown body field/);

    // 4. query and body disagreeing is a 400, not an undocumented winner
    const conflict = await request(t.baseUrl, '/x402/qrng?bytes=16', {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ bytes: 64 }),
    });
    assert.equal(conflict.status, 400);
    assert.equal(conflict.json.error, 'conflicting_params');

    // 5. the POST shape is advertised, so an indexer can describe it
    const cat = await request(t.baseUrl, '/x402');
    const qrng = cat.json.products.find((p) => p.id === 'qrng');
    const alt = qrng.outputSchema.input.alternateMethods[0];
    assert.equal(alt.method, 'POST');
    assert.equal(alt.bodyType, 'json');
    assert.ok(alt.bodyFields.bytes);
  } finally {
    await t.close();
  }
});

// ------------------------------------------------------------ bulk chain

test('bulk: window over the block cap answers 400 with cap detail, no payment demanded', async () => {
  const t = await buildTestGateway();
  try {
    const res = await request(t.baseUrl, '/x402/chain/blocks?from=0&to=5000');
    assert.equal(res.status, 400);
    assert.equal(res.json.error, 'window_too_large');
    assert.equal(res.json.caps.max_blocks, 1000);
    assert.equal(res.headers.get('payment-required'), null);
    assert.equal(t.fac.calls.verify.length, 0);
  } finally {
    await t.close();
  }
});

test('bulk: paid NDJSON block export — meta/summary lines, stripped duplication, exact big amounts', async () => {
  const big = '40182236992334270'; // > 2^53: JSON.parse into Number would corrupt it
  const t = await buildTestGateway({
    handlers: chainHandlers({ head: 50, txValues: { 11: [big], 12: ['765000000000'] } }),
  });
  try {
    const { first, paid } = await paidRequest(t.baseUrl, '/x402/chain/blocks?from=10&to=13');
    assert.equal(first.status, 402);
    const required = protocol.decodeHeader(first.headers.get('payment-required'));
    assert.equal(required.accepts[0].amount, '20000'); // $0.02
    assert.equal(paid.status, 200);
    assert.match(paid.headers.get('content-type'), /application\/x-ndjson/);
    const lines = paid.text.trim().split('\n').map((l) => JSON.parse(l));
    const meta = lines[0];
    const summary = lines[lines.length - 1];
    assert.equal(meta.type, 'meta');
    assert.equal(meta.unit, 'nANM');
    assert.equal(meta.chain_id, 1);
    assert.equal(meta.head_height, 50);
    assert.equal(meta.payment.amount_atomic, '20000');
    assert.ok(meta.payment.settlement_tx);
    assert.equal(summary.type, 'summary');
    assert.equal(summary.blocks, 4);
    assert.equal(summary.txs, 2);
    assert.equal(summary.complete, true);
    assert.equal(summary.next_cursor, null);
    const block11 = lines.find((l) => l.height === 11);
    // the >2^53 value survives as an exact decimal string
    assert.equal(block11.transactions[0].value, big);
    assert.ok(paid.text.includes(`"${big}"`));
    // node duplication stripped
    assert.equal(block11.txs, undefined);
    assert.equal(block11.header, undefined);
    assert.equal(block11.receipts, undefined);
  } finally {
    await t.close();
  }
});

test('bulk: JSON format via ?format=json', async () => {
  const t = await buildTestGateway({ handlers: chainHandlers({ head: 50 }) });
  try {
    const { paid } = await paidRequest(t.baseUrl, '/x402/chain/blocks?from=1&count=3&format=json');
    assert.equal(paid.status, 200);
    assert.match(paid.headers.get('content-type'), /application\/json/);
    assert.equal(paid.json.blocks.length, 3);
    assert.equal(paid.json.meta.export, 'blocks');
    assert.equal(paid.json.summary.complete, true);
  } finally {
    await t.close();
  }
});

test('bulk: head pinning clamps to head - margin and reports the resume cursor', async () => {
  const t = await buildTestGateway({ handlers: chainHandlers({ head: 100 }) });
  try {
    // head=100, margin=6 => pinned to 94
    const { paid } = await paidRequest(t.baseUrl, '/x402/chain/blocks?from=90&to=99&format=json');
    assert.equal(paid.status, 200);
    assert.equal(paid.json.meta.to_height, 94);
    assert.equal(paid.json.blocks.length, 5); // 90..94
    assert.equal(paid.json.summary.truncated_reason, 'pinned_below_requested_to');
    assert.equal(paid.json.summary.next_cursor, 95);
    assert.equal(paid.json.summary.complete, false);
  } finally {
    await t.close();
  }
});

test('bulk: a window inside the head margin is refused pre-settlement, never sold empty', async () => {
  const t = await buildTestGateway({ handlers: chainHandlers({ head: 200 }) });
  try {
    // head=200, margin=6 => nothing above 194 is exportable. A window that
    // starts at 197 can only ever return zero rows.
    //
    // COLD PROCESS: the very first request lands before any head has ever
    // been read, so the 402 goes out — but the PAID retry is refused with the
    // same 400, before verify and before settle. No money can be taken for a
    // window that cannot contain a row.
    const cold = await paidRequest(t.baseUrl, '/x402/chain/blocks?from=197&count=2');
    assert.equal(cold.first.status, 402);
    assert.equal(cold.paid.status, 400);
    assert.equal(cold.paid.json.error, 'window_not_yet_final');
    assert.equal(t.fac.calls.verify.length, 0, 'the facilitator was never asked');
    assert.equal(t.fac.settled(), 0);

    // WARM (the steady state — the free catalog alone keeps the head fresh):
    // the refusal happens up front and no terms are ever offered.
    const first = await request(t.baseUrl, '/x402/chain/blocks?from=197&count=2');
    assert.equal(first.status, 400, 'refused before any payment is requested');
    assert.equal(first.json.error, 'window_not_yet_final');
    assert.equal(first.json.max_exportable_height, 194);
    assert.equal(first.json.head_margin, 6);
    assert.equal(first.json.retry_after_blocks, 3);
    assert.equal(first.headers.get('payment-required'), null);
    assert.equal(t.fac.calls.verify.length, 0);
    assert.equal(t.fac.settled(), 0);

    // The boundary itself is still sellable and returns real rows.
    const ok = await paidRequest(t.baseUrl, '/x402/chain/blocks?from=194&count=1&format=json');
    assert.equal(ok.paid.status, 200);
    assert.equal(ok.paid.json.blocks.length, 1);
    assert.equal(ok.paid.json.summary.complete, true);
  } finally {
    await t.close();
  }
});

test('bulk: next_cursor never points backward, before the window the buyer paid for', async () => {
  const t = await buildTestGateway({ handlers: chainHandlers({ head: 200 }) });
  try {
    // from inside the exportable range but `to` above it: the cursor must be
    // the first unconsumed height, and never below `from`.
    const { paid } = await paidRequest(t.baseUrl, '/x402/chain/blocks?from=194&to=199&format=json');
    assert.equal(paid.status, 200);
    assert.ok(paid.json.summary.next_cursor >= 194,
      `cursor ${paid.json.summary.next_cursor} must not send a paging agent backward`);
    assert.equal(paid.json.summary.next_cursor, 195);
  } finally {
    await t.close();
  }
});

test('bulk: tx export respects the record cap at block granularity with a resume cursor', async () => {
  const txValues = {};
  for (let h = 10; h <= 20; h++) txValues[h] = ['100', '200', '300']; // 3 txs per block
  const t = await buildTestGateway({
    overrides: { bulkMaxTxRecords: 4 },
    handlers: chainHandlers({ head: 100, txValues }),
  });
  try {
    const { paid } = await paidRequest(t.baseUrl, '/x402/chain/transactions?from=10&to=20&format=json');
    assert.equal(paid.status, 200);
    // block 10 (3 txs) below cap, block 11 pushes to 6 (never sliced), stop before block 12
    assert.equal(paid.json.transactions.length, 6);
    assert.equal(paid.json.summary.truncated_reason, 'record_cap');
    assert.equal(paid.json.summary.next_cursor, 12);
    assert.equal(paid.json.summary.complete, false);
    // rows carry block context
    assert.equal(paid.json.transactions[0].height, 10);
    assert.equal(paid.json.transactions[0].tx_index, 0);
    assert.ok(paid.json.transactions[0].block_timestamp);
  } finally {
    await t.close();
  }
});

test('bulk: byte budget truncates at a block boundary', async () => {
  const txValues = {};
  for (let h = 0; h <= 30; h++) txValues[h] = ['100', '200', '300', '400'];
  const t = await buildTestGateway({
    overrides: { bulkMaxResponseBytes: 10_000, bulkChunkBlocks: 5 },
    handlers: chainHandlers({ head: 100, txValues }),
  });
  try {
    const { paid } = await paidRequest(t.baseUrl, '/x402/chain/blocks?from=0&to=30&format=json');
    assert.equal(paid.status, 200);
    assert.equal(paid.json.summary.truncated_reason, 'byte_budget');
    assert.ok(paid.json.summary.next_cursor > 0 && paid.json.summary.next_cursor <= 30);
    assert.equal(paid.json.blocks.length, paid.json.summary.next_cursor - 0);
  } finally {
    await t.close();
  }
});

test('bulk: address filter joins on the account digest (anim1 bech32m accepted)', async () => {
  // txs in the fixture use from=0x11*32, to=0x22*32
  const digest = Buffer.from('11'.repeat(32), 'hex');
  const payload = Buffer.concat([Buffer.from([0x10, 0x03]), digest]); // alg id + digest
  const anim1 = bech32m.encode('anim', payload);
  assert.equal(bech32m.toAccountDigestHex(anim1), '11'.repeat(32));
  const t = await buildTestGateway({
    handlers: chainHandlers({ head: 100, txValues: { 5: ['100'], 6: ['200'] } }),
  });
  try {
    const { paid } = await paidRequest(t.baseUrl, `/x402/chain/transactions?from=1&to=10&address=${anim1}&format=json`);
    assert.equal(paid.status, 200);
    assert.equal(paid.json.transactions.length, 2);
    assert.equal(paid.json.meta.address, anim1);
    assert.equal(paid.json.meta.address_digest, '11'.repeat(32));
    // an address that matches nothing
    const other = bech32m.encode('anim', Buffer.concat([Buffer.from([0x10, 0x03]), Buffer.from('99'.repeat(32), 'hex')]));
    const none = await paidRequest(t.baseUrl, `/x402/chain/transactions?from=1&to=10&address=${other}&format=json`);
    assert.equal(none.paid.json.transactions.length, 0);
  } finally {
    await t.close();
  }
});

test('bulk: gzip via Accept-Encoding, byte budgets meter uncompressed bytes', async () => {
  const t = await buildTestGateway({ handlers: chainHandlers({ head: 100 }) });
  try {
    const first = await request(t.baseUrl, '/x402/chain/blocks?from=1&to=40');
    const { paymentHeaderFrom } = require('./gateway-helpers');
    const paid = await request(t.baseUrl, '/x402/chain/blocks?from=1&to=40', {
      headers: { 'payment-signature': paymentHeaderFrom(first), 'accept-encoding': 'gzip' },
    });
    assert.equal(paid.status, 200);
    // undici auto-decompresses; prove the payload was gzip on the wire by
    // checking that the raw body is valid NDJSON after transparent decode
    const lines = paid.text.trim().split('\n');
    assert.equal(JSON.parse(lines[0]).type, 'meta');
    assert.equal(JSON.parse(lines[lines.length - 1]).type, 'summary');
  } finally {
    await t.close();
  }
});

test('bech32m round-trips and rejects plain-bech32 checksums', () => {
  const payload = Buffer.concat([Buffer.from([0x10, 0x03]), Buffer.from('ab'.repeat(32), 'hex')]);
  const addr = bech32m.encode('anim', payload);
  assert.ok(addr.startsWith('anim1'));
  const dec = bech32m.decode(addr);
  assert.equal(dec.hrp, 'anim');
  assert.deepEqual(dec.payload, payload);
  // flip a char => checksum failure
  const broken = addr.slice(0, -1) + (addr.endsWith('q') ? 'p' : 'q');
  assert.throws(() => bech32m.decode(broken), /checksum/);
  assert.throws(() => bech32m.toAccountDigestHex('anim1qqqq'), /./);
  assert.equal(bech32m.toAccountDigestHex('0x' + 'cd'.repeat(32)), 'cd'.repeat(32));
});

// ---------------------------------------------------- priority inference

function workerStatusHandler(map) {
  // map: address -> {registered, last_seen (s or ms), tiers}
  return (params) => {
    const w = map[params.address];
    if (!w) return { registered: false, address: params.address };
    return Object.assign({ address: params.address }, w);
  };
}

const WALLET_A = 'anim1' + 'q'.repeat(58);
const WALLET_B = 'anim1' + 'p'.repeat(58);

test('inference: disabled by default => clear 503, catalog false, NO 402, facilitator untouched', async () => {
  const t = await buildTestGateway();
  try {
    const res = await request(t.baseUrl, '/x402/v1/chat/completions', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ model: 'kimi-k3', messages: [{ role: 'user', content: 'hi' }] }),
    });
    assert.equal(res.status, 503);
    assert.equal(res.json.error, 'priority_inference_unavailable');
    assert.equal(res.json.enabled, false);
    assert.equal(res.headers.get('payment-required'), null);
    assert.equal(t.fac.calls.verify.length, 0);
    assert.equal(t.fac.calls.settle.length, 0);
  } finally {
    await t.close();
  }
});

test('inference: enabled but below the worker floor still refuses payment', async () => {
  const nowSec = Math.floor(Date.now() / 1000);
  const t = await buildTestGateway({
    overrides: {
      priorityInferenceEnabled: true,
      priorityInferenceMinServingWorkers: 2,
      inferenceWorkerWallets: [WALLET_A, WALLET_B],
    },
    handlers: Object.assign(chainHandlers(), {
      'aicf.workerStatus': workerStatusHandler({
        [WALLET_A]: { registered: true, last_seen: nowSec - 10, tiers: ['standard', 'standard', 'pipeline'] },
        [WALLET_B]: { registered: true, last_seen: nowSec - 4000, tiers: ['standard'] }, // stale
      }),
    }),
  });
  try {
    await t.gw.capacity.probeOnce();
    assert.equal(t.gw.capacity.count, 1); // only the fresh wallet
    const res = await request(t.baseUrl, '/x402/v1/chat/completions', {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ messages: [{ role: 'user', content: 'hi' }] }),
    });
    assert.equal(res.status, 503);
    assert.equal(res.json.serving_workers, 1);
    assert.equal(res.json.required, 2);
    assert.equal(res.headers.get('payment-required'), null);
    const cat = await request(t.baseUrl, '/x402');
    assert.equal(cat.json.products.find((p) => p.id === 'priority_inference').available, false);
    assert.equal(t.fac.calls.settle.length, 0);
  } finally {
    await t.close();
  }
});

test('inference: activates at the threshold and proxies with priority headers', async () => {
  const nowSec = Math.floor(Date.now() / 1000);
  const upstreamCalls = [];
  const upstream = async (url, opts) => {
    upstreamCalls.push({ url, headers: opts.headers, body: opts.body });
    return textResponse(JSON.stringify({ id: 'chatcmpl-1', object: 'chat.completion', choices: [{ message: { role: 'assistant', content: 'pong' } }] }),
      { headers: { 'content-type': 'application/json' } });
  };
  const t = await buildTestGateway({
    overrides: {
      priorityInferenceEnabled: true,
      priorityInferenceMinServingWorkers: 2,
      inferenceWorkerWallets: [WALLET_A, WALLET_B],
    },
    handlers: Object.assign(chainHandlers(), {
      'aicf.workerStatus': workerStatusHandler({
        [WALLET_A]: { registered: true, last_seen: nowSec - 10, tiers: ['standard'] },
        [WALLET_B]: { registered: true, last_seen: (nowSec - 20) * 1000, tiers: ['standard', 'free'] }, // ms form
      }),
    }),
    inferenceFetch: upstream,
  });
  try {
    await t.gw.capacity.probeOnce();
    assert.equal(t.gw.capacity.count, 2);
    const cat = await request(t.baseUrl, '/x402');
    assert.equal(cat.json.products.find((p) => p.id === 'priority_inference').available, true);

    const body = JSON.stringify({ model: 'kimi-k3', messages: [{ role: 'user', content: 'ping' }] });
    const { first, paid } = await paidRequest(t.baseUrl, '/x402/v1/chat/completions', {
      method: 'POST', headers: { 'content-type': 'application/json' }, body,
    });
    assert.equal(first.status, 402);
    const required = protocol.decodeHeader(first.headers.get('payment-required'));
    assert.equal(required.accepts[0].amount, '20000'); // $0.02
    assert.equal(paid.status, 200);
    assert.equal(paid.json.choices[0].message.content, 'pong');
    // OpenAI shape stays pure (no injected payment field); proof in header
    assert.equal(paid.json.payment, undefined);
    assert.ok(paid.headers.get('payment-response'));
    assert.equal(upstreamCalls.length, 1);
    assert.equal(upstreamCalls[0].headers['x-animica-priority'], 'x402-paid');
    assert.equal(t.fac.calls.settle.length, 1);
  } finally {
    await t.close();
  }
});

test('inference: capacity drop between 402 and paid retry refuses BEFORE settlement', async () => {
  const nowSec = Math.floor(Date.now() / 1000);
  const workers = {
    [WALLET_A]: { registered: true, last_seen: nowSec - 10, tiers: ['standard'] },
  };
  const t = await buildTestGateway({
    overrides: {
      priorityInferenceEnabled: true,
      priorityInferenceMinServingWorkers: 1,
      inferenceWorkerWallets: [WALLET_A],
    },
    handlers: Object.assign(chainHandlers(), { 'aicf.workerStatus': workerStatusHandler(workers) }),
  });
  try {
    await t.gw.capacity.probeOnce();
    const first = await request(t.baseUrl, '/x402/v1/chat/completions', {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ messages: [{ role: 'user', content: 'hi' }] }),
    });
    assert.equal(first.status, 402);
    // the only worker goes stale before the paid retry
    workers[WALLET_A].last_seen = nowSec - 4000;
    await t.gw.capacity.probeOnce();
    const { paymentHeaderFrom } = require('./gateway-helpers');
    const paid = await request(t.baseUrl, '/x402/v1/chat/completions', {
      method: 'POST',
      headers: { 'content-type': 'application/json', 'payment-signature': paymentHeaderFrom(first) },
      body: JSON.stringify({ messages: [{ role: 'user', content: 'hi' }] }),
    });
    assert.equal(paid.status, 503);
    assert.equal(t.fac.calls.settle.length, 0, 'must not settle when capacity dropped');
  } finally {
    await t.close();
  }
});

test('inference: stream:true is rejected before payment', async () => {
  const nowSec = Math.floor(Date.now() / 1000);
  const t = await buildTestGateway({
    overrides: { priorityInferenceEnabled: true, priorityInferenceMinServingWorkers: 1, inferenceWorkerWallets: [WALLET_A] },
    handlers: Object.assign(chainHandlers(), {
      'aicf.workerStatus': workerStatusHandler({ [WALLET_A]: { registered: true, last_seen: nowSec, tiers: ['standard'] } }),
    }),
  });
  try {
    await t.gw.capacity.probeOnce();
    const res = await request(t.baseUrl, '/x402/v1/chat/completions', {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ stream: true, messages: [{ role: 'user', content: 'hi' }] }),
    });
    assert.equal(res.status, 400);
    assert.equal(res.json.error, 'stream_unsupported');
    assert.equal(res.headers.get('payment-required'), null);
  } finally {
    await t.close();
  }
});

test('capacity: probe staleness fails closed; ms timestamps normalize; pipeline tier ignored', async () => {
  assert.equal(normalizeLastSeenSec(1784433490), 1784433490);
  assert.equal(normalizeLastSeenSec(1784433490422), 1784433490.422);
  let clock = 1_000_000_000;
  const nowFn = () => clock;
  const responses = { registered: true, last_seen: clock / 1000 - 5, tiers: ['pipeline'] };
  const gate = createCapacityGate({
    rpcUrl: 'http://node/rpc',
    wallets: [WALLET_A],
    tier: 'standard',
    minWorkers: 1,
    enabled: true,
    now: nowFn,
    logger: { debug() {}, warn() {} },
    fetchImpl: async () => textResponse(JSON.stringify({ jsonrpc: '2.0', id: 1, result: responses })),
  });
  await gate.probeOnce();
  assert.equal(gate.count, 0, 'pipeline-only tiers never count');
  responses.tiers = ['standard', 'pipeline'];
  await gate.probeOnce();
  assert.equal(gate.count, 1);
  assert.equal(gate.available(), true);
  clock += 61_000; // probe older than maxProbeAgeMs (60s default)
  assert.equal(gate.available(), false, 'stale probe must fail closed');
});
