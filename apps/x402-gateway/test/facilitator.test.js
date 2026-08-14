'use strict';
/**
 * Self-facilitator tests, all against a MOCKED Solana RPC — no network.
 */

const test = require('node:test');
const assert = require('node:assert/strict');

const sol = require('../src/solana');
const { createFacilitator, ReplayStore } = require('../src/facilitator');
const { scene, addr, kp } = require('./helpers');

function facFor(s, extra = {}) {
  return createFacilitator(Object.assign({
    network: s.network,
    signer: s.feePayerKp,
    rpc: s.rpc,
    sleep: async () => {},
    logger: { error() {} },
  }, extra));
}

const body = (s, txB64, reqOverrides = {}) => ({
  x402Version: 2,
  paymentPayload: s.payload(txB64),
  paymentRequirements: Object.assign({}, s.requirements, reqOverrides),
});

test('verify: happy path — valid partially-signed TransferChecked', async () => {
  const s = scene();
  const fac = facFor(s);
  const out = await fac.verify(body(s, s.tx()));
  assert.deepEqual({ isValid: out.isValid, payer: out.payer }, { isValid: true, payer: s.payerKp.publicKey });
  // verify is read-only: nothing sent, nothing marked
  assert.equal(s.rpc.sent.length, 0);
  assert.equal(fac.replay.seen.size, 0);
});

test('settle: happy path — fee payer signature added, tx broadcast, confirmed', async () => {
  const s = scene();
  const fac = facFor(s);
  const out = await fac.settle(body(s, s.tx()));

  assert.equal(out.success, true);
  assert.equal(out.network, s.network);
  assert.equal(out.payer, s.payerKp.publicKey);
  assert.equal(out.amount, s.requirements.amount);
  assert.ok(out.transaction.length > 0, 'transaction hash required on success');

  // The broadcast wire tx must now carry a REAL fee-payer signature over the
  // exact message bytes, in slot 0.
  assert.equal(s.rpc.sent.length, 1);
  const sent = sol.parseTransaction(s.rpc.sent[0]);
  assert.equal(sol.isPlaceholderSignature(sent.signatures[0]), false);
  assert.ok(sol.verifySignature(sent.messageBytes, sent.signatures[0], s.feePayerKp.publicKey));
  // and the authority's original signature is untouched
  assert.ok(sol.verifySignature(sent.messageBytes, sent.signatures[1], s.payerKp.publicKey));
  assert.equal(out.transaction, sol.base58Encode(sent.signatures[0]));
});

test('reject: wrong amount (tx pays less than required)', async () => {
  const s = scene();
  const fac = facFor(s);
  const short = s.tx({ amount: BigInt(s.requirements.amount) - 1n });
  const v = await fac.verify(body(s, short));
  assert.equal(v.isValid, false);
  assert.equal(v.invalidReason, 'invalid_exact_svm_payload_amount');
  const st = await fac.settle(body(s, short));
  assert.deepEqual({ success: st.success, errorReason: st.errorReason, transaction: st.transaction },
    { success: false, errorReason: 'invalid_exact_svm_payload_amount', transaction: '' });
});

test('reject: wrong mint (tx transfers a different SPL token)', async () => {
  const s = scene();
  const fac = facFor(s);
  const otherMint = addr();
  const v = await fac.verify(body(s, s.tx({ mint: otherMint })));
  assert.equal(v.isValid, false);
  assert.equal(v.invalidReason, 'invalid_exact_svm_payload_mint');
});

test('reject: replayed transaction after settlement', async () => {
  const s = scene();
  const fac = facFor(s);
  const txB64 = s.tx();

  const first = await fac.settle(body(s, txB64));
  assert.equal(first.success, true);

  const again = await fac.verify(body(s, txB64));
  assert.equal(again.isValid, false);
  assert.equal(again.invalidReason, 'invalid_transaction_state');

  const settleAgain = await fac.settle(body(s, txB64));
  assert.equal(settleAgain.success, false);
  assert.equal(settleAgain.errorReason, 'invalid_transaction_state');
  assert.equal(s.rpc.sent.length, 1, 'replay must not broadcast a second time');
});

test('reject: authority signature missing', async () => {
  const s = scene();
  const fac = facFor(s);
  const v = await fac.verify(body(s, s.tx({ signAuthority: false })));
  assert.equal(v.isValid, false);
  assert.equal(v.invalidReason, 'invalid_exact_svm_payload_signature');
});

test('reject: fee payer is not the advertised sponsor', async () => {
  const s = scene();
  const fac = facFor(s);
  const stranger = kp();
  const v = await fac.verify(body(s, s.tx({ feePayer: stranger.publicKey })));
  assert.equal(v.isValid, false);
  assert.equal(v.invalidReason, 'invalid_exact_svm_payload_fee_payer');
});

test('reject: unknown program instruction (open-cheque prevention)', async () => {
  const s = scene();
  const fac = facFor(s);
  const v = await fac.verify(body(s, s.tx({ extraProgram: addr() })));
  assert.equal(v.isValid, false);
  assert.equal(v.invalidReason, 'invalid_exact_svm_payload_instructions');
});

test('reject: token instruction that is not TransferChecked', async () => {
  const s = scene();
  const fac = facFor(s);
  const v = await fac.verify(body(s, s.tx({ transferTag: 3 }))); // 3 = plain Transfer
  assert.equal(v.isValid, false);
  assert.equal(v.invalidReason, 'invalid_exact_svm_payload_instructions');
});

test('reject: destination not owned by payTo', async () => {
  const s = scene();
  // Point requirements at a different treasury; the on-chain dest account
  // (mock) still belongs to the original.
  const fac = facFor(s);
  const v = await fac.verify(body(s, s.tx(), { payTo: addr() }));
  assert.equal(v.isValid, false);
  assert.equal(v.invalidReason, 'invalid_exact_svm_payload_recipient');
});

test('reject: insufficient funds in source', async () => {
  const s = scene({ priceAtomic: (10n ** 13n).toString() }); // more than the funded 1e12
  const fac = facFor(s);
  const v = await fac.verify(body(s, s.tx({ amount: 10n ** 13n })));
  assert.equal(v.isValid, false);
  assert.equal(v.invalidReason, 'insufficient_funds');
});

test('reject: wrong network / wrong scheme / wrong feePayer in requirements', async () => {
  const s = scene();
  const fac = facFor(s);
  const txB64 = s.tx();
  assert.equal((await fac.verify(body(s, txB64, { network: 'solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp' }))).invalidReason, 'invalid_network');
  assert.equal((await fac.verify(body(s, txB64, { scheme: 'upto' }))).invalidReason, 'invalid_scheme');
  assert.equal((await fac.verify(body(s, txB64, { extra: { feePayer: addr() } }))).invalidReason, 'invalid_payload');
});

test('settle: rpc send failure returns unexpected_settle_error and keeps replay mark', async () => {
  const s = scene();
  s.rpc.call = (function (orig) {
    return async (method, params) => {
      if (method === 'sendTransaction') throw new Error('rpc down');
      return orig(method, params);
    };
  })(s.rpc.call.bind(s.rpc));
  const fac = facFor(s);
  const txB64 = s.tx();
  const out = await fac.settle(body(s, txB64));
  assert.equal(out.success, false);
  assert.equal(out.errorReason, 'unexpected_settle_error');
  // Mark kept: the send may have landed server-side. A retry must re-build
  // with a fresh blockhash, not re-fire the same bytes.
  const again = await fac.verify(body(s, txB64));
  assert.equal(again.invalidReason, 'invalid_transaction_state');
});

test('supported() advertises the exact/SVM kind and the sponsor signer', () => {
  const s = scene();
  const fac = facFor(s);
  const sup = fac.supported();
  assert.deepEqual(sup.kinds, [{ x402Version: 2, scheme: 'exact', network: s.network, extra: { feePayer: s.feePayerKp.publicKey } }]);
  assert.deepEqual(sup.signers, { [s.network]: [s.feePayerKp.publicKey] });
});

test('replay store expires marks after ttl', () => {
  let t = 0;
  const store = new ReplayStore({ ttlMs: 100, now: () => t });
  store.mark('k');
  assert.equal(store.has('k'), true);
  t = 101;
  assert.equal(store.has('k'), false);
});
