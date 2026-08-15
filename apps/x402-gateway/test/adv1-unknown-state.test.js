'use strict';
/**
 * ADVERSARIAL REVIEW 1/3 — money-loss PoCs (verify/disprove, no fixes here).
 * Vectors: (1) replay/double-spend, (2) concurrent duplicates,
 * (3) settlement unknown-state handling, (10) refund/reconciliation edges.
 *
 * Every mock is in-test; no real chain or key is touched. These tests are
 * PROOFS about the ACTUAL code paths in src/paywall.js, src/store/*,
 * src/facilitator-evm/settlement.js — they intentionally assert current
 * (possibly buggy) behavior and say so in comments.
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const cfgMod = require('../src/config');
const { createGateway } = require('../src/server');
const { createGatewayStore } = require('../src/store/gateway');
const { createReceiptSigner } = require('../src/receipts');
const { chainHandlers, fakeNodeFetch, paymentHeaderFrom, request, BASE } = require('./gateway-helpers');
const { scene } = require('./evm-helpers');

/**
 * A gateway wired to a PROGRAMMABLE facilitator whose /settle result we
 * control per-call. Exposes the real gatewayStore so we can inspect the
 * incident / idempotency ledgers after a request.
 */
function programmableFacilitator() {
  const state = {
    verify: () => ({ isValid: true, payer: '0x' + 'ab'.repeat(20) }),
    settle: null, // set per test
    settleCalls: 0,
  };
  const factory = () => ({
    async verify(paymentPayload) { return state.verify(paymentPayload); },
    async settle(paymentPayload, requirements) {
      state.settleCalls += 1;
      return state.settle(paymentPayload, requirements);
    },
    async supported() { return { kinds: [], extensions: [], signers: {} }; },
  });
  return { factory, state };
}

async function buildGateway({ handlers } = {}) {
  const fac = programmableFacilitator();
  const nodeFetch = fakeNodeFetch(handlers || chainHandlers());
  const cfg = cfgMod.loadGatewayConfig({}, {
    enabled: true,
    networkEvm: BASE,
    usdcAsset: cfgMod.USDC_DEFAULTS[BASE],
    basePayTo: '0x' + '77'.repeat(20),
    resourceBaseUrl: 'http://127.0.0.1:0',
    wanmMint: '', wanmTreasury: '', wanmFeePayerPubkey: '', wanmUsdPrice: '',
  });
  const quiet = { debug() {}, info() {}, warn() {}, error() {}, child() { return quiet; } };
  const gatewayStore = createGatewayStore(':memory:');
  const gw = createGateway({
    cfg, logger: quiet, fetchImpl: nodeFetch, inferenceFetch: nodeFetch,
    facilitatorClientFactory: fac.factory, gatewayStore,
    chainIndex: null, chainIndexer: null, // no sqlite index file, no walker
    receiptSigner: createReceiptSigner({ secret: 'adv1-secret' }),
    sleep: async () => {}, availabilityTtlMs: 0,
  });
  await new Promise((r) => gw.server.listen(0, '127.0.0.1', r));
  const baseUrl = `http://127.0.0.1:${gw.server.address().port}`;
  return {
    baseUrl, fac, gatewayStore,
    close: () => new Promise((r) => { gw.capacity.stop(); gw.server.close(r); }),
  };
}

async function paidQrng(baseUrl, { idemKey } = {}) {
  const first = await request(baseUrl, '/x402/qrng/draw');
  assert.equal(first.status, 402, 'first request must 402');
  const headers = { 'payment-signature': paymentHeaderFrom(first) };
  if (idemKey) headers['idempotency-key'] = idemKey;
  return request(baseUrl, '/x402/qrng/draw', { headers });
}

/* ============================================================ VECTOR 3 === */
/*  Settlement UNKNOWN-STATE handling at the gateway.                        */

test('ADV1-A [vector 3/10]: facilitator returns UNKNOWN settle (tx broadcast, receipt timed out) -> gateway 402 with NO incident, NO reconciliation trail', async () => {
  // This is exactly what src/facilitator-evm/settlement.js confirmSettlement
  // returns on a receipt timeout: success:false, a REAL tx hash, and a
  // detail that says the state is "recoverable, not final". The tx may still
  // mine; the facilitator row stays 'submitting' and crash recovery will
  // later mark it settled and COUNT REVENUE.
  const h = await buildGateway();
  try {
    const REAL_TX = '0x' + 'ab'.repeat(32);
    h.fac.state.settle = () => ({
      success: false,
      errorReason: 'unexpected_settle_error',
      transaction: REAL_TX, // <-- a real in-flight tx hash, not ''
      network: BASE,
      errorDetail: 'no receipt within deadline; settlement state is recoverable, not final',
    });

    const res = await paidQrng(h.baseUrl);
    // FIXED: an unknown outcome is never a bare 402 (which a client reads as
    // "you were not charged" and retries, risking a second payment). It is a
    // 502 carrying a signed receipt, and it leaves a reconciliation trail.
    assert.equal(res.status, 502, 'unknown settlement is reported as unknown, not as "not charged"');

    const incidents = h.gatewayStore.listIncidents();
    assert.equal(incidents.length, 1, 'unknown-state settlement opens an incident');
    assert.equal(incidents[0].kind, 'settle_unknown');
    assert.equal(incidents[0].settlement_tx, REAL_TX,
      'incident carries the broadcast tx hash so it can be joined to the chain and the facilitator ledger');
    assert.ok(incidents[0].auth_nonce, 'incident carries the EIP-3009 nonce as a cross-ledger join key');
  } finally {
    await h.close();
  }
});

test('ADV1-B [vector 3]: transport-throw unknown outcome is handled identically to a returned-unknown one', async () => {
  const h = await buildGateway();
  try {
    // Transport throw (fc.settle rejects): paywall.settleNow's catch opens a
    // 'settle_unknown' incident. Same real-world ambiguity (tx may have
    // landed) — handled here, dropped in ADV1-A.
    h.fac.state.settle = () => { throw new Error('ETIMEDOUT talking to facilitator'); };
    const res = await paidQrng(h.baseUrl);
    assert.equal(res.status, 502, 'transport ambiguity is an unknown outcome, not a clean failure');
    const incidents = h.gatewayStore.listIncidents();
    assert.equal(incidents.length, 1, 'throw path opens an incident');
    assert.equal(incidents[0].kind, 'settle_unknown');
    // A throw yields no tx hash, but the authorization nonce still ties this
    // incident to the facilitator's payments row and to any on-chain
    // AuthorizationUsed event, so reconcile can resolve it either way.
    assert.ok(incidents[0].auth_nonce, 'incident is joinable by authorization nonce even with no tx hash');
  } finally {
    await h.close();
  }
});

test('ADV1-C [vector 3]: execute-then-settle withholds the resource on an unknown settle BUT records the obligation', async () => {
  const h = await buildGateway();
  try {
    h.fac.state.settle = () => ({
      success: false, errorReason: 'unexpected_settle_error',
      transaction: '0x' + 'cd'.repeat(32), network: BASE,
    });
    const res = await paidQrng(h.baseUrl);
    assert.equal(res.status, 502);
    assert.equal(res.json && res.json.randomness, undefined, 'resource is still withheld — never delivered unsettled');
    // The draw is discarded, but the potential obligation is now on the books:
    // an operator can see that this payer may have paid for nothing and
    // reconcile (refund or re-deliver) against the named tx.
    const incidents = h.gatewayStore.listIncidents();
    assert.equal(incidents.length, 1, 'obligation recorded');
    assert.equal(incidents[0].kind, 'settle_unknown');
    assert.equal(incidents[0].settlement_tx, '0x' + 'cd'.repeat(32));
  } finally {
    await h.close();
  }
});

/* ====================================================== VECTOR 1 / 2 ====== */
/*  Replay + concurrency at the facilitator (the real EVM engine).          */

test('ADV1-D [vector 1/2]: DISPROVE double-spend — 8 concurrent settles of one authorization settle EXACTLY once, one broadcast, one settled row', async () => {
  const sc = scene();
  const body = sc.payment();
  const results = await Promise.all(Array.from({ length: 8 }, () => sc.facilitator.settle(body)));
  const ok = results.filter((r) => r.success);
  assert.equal(ok.length, 1, 'exactly one concurrent settle wins the atomic claim');
  assert.equal(sc.rpc.sent.length, 1, 'exactly one tx broadcast');
  const rows = sc.store.db.prepare('SELECT status FROM payments').all();
  assert.equal(rows.length, 1, 'losers created no rows');
  assert.equal(rows[0].status, 'settled');
});

test('ADV1-E [vector 1/3]: process-kill BETWEEN broadcast and DB settle -> reopened store + recovery settles once, NEVER a second transfer', async () => {
  // Simulate the crash-safety claim directly against the persistent store:
  // a row is left 'submitting' (broadcast happened, DB never saw 'settled'),
  // the process dies, a fresh store instance over the same file runs
  // recovery, and the on-chain authorization is already consumed.
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'adv1-crash-'));
  const dbPath = path.join(dir, 'x402.db');
  try {
    const scA = scene({ storePath: dbPath, rpcOpts: { consumeOnSend: false } });
    const body = scA.payment();
    const a = scA.lastAuth;

    // Drive a real settle but stop the world right after broadcast: force the
    // receipt to never appear so the row stays 'submitting'.
    scA.rpc.opts.receiptForSend = false;
    const out = await scA.facilitator.settle(body);
    assert.equal(out.success, false, 'receipt-timeout is not reported as success');
    const row = scA.store.db.prepare('SELECT * FROM payments').get();
    assert.equal(row.status, 'submitting', 'row is left recoverable, not settled');
    assert.ok(row.raw_tx, 'signed bytes persisted BEFORE broadcast');
    assert.equal(scA.rpc.sent.length, 1, 'exactly one broadcast happened pre-crash');
    scA.store.close(); // "kill -9"

    // Fresh process: new store + facilitator over the same file. Chain now
    // shows the tx mined and the authorization consumed.
    const scB = scene({ storePath: dbPath, rpcOpts: { consumeOnSend: false }, envOverrides: { X402_SETTLEMENT_ADDRESS: scA.payTo } });
    scB.rpc.state.authConsumed.add(`${a.from.toLowerCase()}:${a.nonce.toLowerCase()}`);
    scB.rpc.state.receipts[row.settlement_tx_hash] = {
      transactionHash: row.settlement_tx_hash, status: '0x1', blockNumber: '0x64',
      gasUsed: '0x15122', effectiveGasPrice: '0x5f5e64',
      logs: [{ address: scB.cfg.asset, topics: [require('../src/facilitator-evm/usdc').TOPICS.authorizationUsed, require('./evm-helpers').word(a.from), a.nonce], data: '0x' }],
    };
    const report = await scB.facilitator.recoverInFlight();
    assert.equal(report.settled, 1, 'recovery settles the in-flight row from chain truth');
    assert.equal(report.rebroadcast, 0, 'never rebroadcast a known/consumed authorization');
    assert.equal(scB.rpc.sent.length, 0, 'no second transfer');
    const after = scB.store.getByPaymentId(row.payment_id);
    assert.equal(after.status, 'settled');
    scB.store.close();
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test('ADV1-F [vector 10]: reconciliation KEY gap — the gateway incident and the facilitator settled row share no join key (fingerprint vs authorization_hash)', async () => {
  // Prove the two ledgers cannot be joined deterministically. The gateway
  // keys incidents by sha256(canonicalJson(paymentPayload)); the facilitator
  // keys payments by the EIP-712 authorization digest. They are different
  // hashes over different bytes.
  const protocol = require('../src/protocol');
  const crypto = require('node:crypto');
  const sc = scene();
  const body = sc.payment();

  // facilitator's key for this payment
  const settle = await sc.facilitator.settle(body);
  assert.equal(settle.success, true);
  const facRow = sc.store.db.prepare('SELECT authorization_hash FROM payments').get();

  // gateway's key for the very same payment payload
  const gwFingerprint = crypto.createHash('sha256')
    .update(protocol.canonicalJson(body.paymentPayload)).digest('hex');

  assert.notEqual(facRow.authorization_hash.toLowerCase(), '0x' + gwFingerprint,
    'PROOF: authorization_hash != gateway fingerprint — no shared key');
  assert.notEqual(facRow.authorization_hash.replace(/^0x/, ''), gwFingerprint,
    'the two ledgers key the same payment by different digests');
});
