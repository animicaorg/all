'use strict';
/**
 * Persistent replay/settlement store tests: uniqueness as the replay
 * arbiter, state-machine transitions, reclaim rules, gas/revenue
 * accounting, and durability across close/reopen.
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const { createStore } = require('../src/store');

function mem() {
  return createStore(':memory:');
}

const CLAIM = (over = {}) => ({
  paymentId: over.paymentId || `pay_${Math.random().toString(36).slice(2)}`,
  authorizationHash: over.authorizationHash || '0x' + 'ab'.repeat(32),
  payer: '0x' + '11'.repeat(20),
  asset: '0x' + '22'.repeat(20),
  network: 'eip155:8453',
  amount: 10000n,
  resource: '/x402/qrng',
  expiresAt: 2_000_000_000,
  authNonce: '0x' + 'cd'.repeat(32),
  ...over,
});

test('authorization_hash uniqueness: second claim is rejected', () => {
  const s = mem();
  assert.equal(s.claim(CLAIM({ paymentId: 'pay_1' })).claimed, true);
  const second = s.claim(CLAIM({ paymentId: 'pay_2' }));
  assert.equal(second.claimed, false);
  assert.equal(second.existing.payment_id, 'pay_1');
  assert.equal(second.existing.status, 'pending');
  s.close();
});

test('full happy transition: pending -> submitting -> settled + gas', () => {
  const s = mem();
  s.claim(CLAIM({ paymentId: 'p1' }));
  s.markSubmitting('p1', { txHash: '0xtx', txNonce: 7, rawTx: '0x02aa' });
  assert.equal(s.listSubmitting().length, 1);
  assert.equal(s.listSubmitting()[0].raw_tx, '0x02aa');
  s.markSettled('p1', { txHash: '0xtx', gasSpentWei: 540_000_000_000n });
  const row = s.getByPaymentId('p1');
  assert.equal(row.status, 'settled');
  assert.equal(row.settlement_tx_hash, '0xtx');
  assert.ok(row.settled_at > 0);
  assert.equal(row.gas_spent_wei, '540000000000');
  assert.equal(s.listSubmitting().length, 0);
  assert.equal(s.gasSpentSince(0), 540_000_000_000n);
  assert.equal(s.settledRevenueAtomic(), 10000n);
  s.close();
});

test('settled/failed rows cannot transition again', () => {
  const s = mem();
  s.claim(CLAIM({ paymentId: 'p1' }));
  s.markSubmitting('p1', { txHash: '0xtx', txNonce: 1, rawTx: '0x02' });
  s.markSettled('p1', { txHash: '0xtx', gasSpentWei: 1n });
  assert.throws(() => s.markSettled('p1', { txHash: '0xother' }), /not in a settleable state/);
  assert.throws(() => s.markSubmitting('p1', { txHash: '0xother', txNonce: 2, rawTx: '0x02' }), /not in a claimable state/);
  // markFailed on a settled row is a no-op, never a downgrade
  s.markFailed('p1', 'nope');
  assert.equal(s.getByPaymentId('p1').status, 'settled');
  s.close();
});

test('reclaim: failed-without-broadcast rows are retryable, broadcast ones never', () => {
  const s = mem();
  const auth = '0x' + '99'.repeat(32);
  s.claim(CLAIM({ paymentId: 'p1', authorizationHash: auth }));
  s.markFailed('p1', 'gas estimate blew up'); // raw_tx IS NULL
  const retry = s.claim(CLAIM({ paymentId: 'p_new', authorizationHash: auth }));
  assert.equal(retry.claimed, true);
  assert.equal(retry.paymentId, 'p1'); // same row resumed
  assert.equal(retry.reclaimed, true);
  assert.equal(s.getByPaymentId('p1').status, 'pending');

  // now it signs + broadcasts + fails => permanently burned
  s.markSubmitting('p1', { txHash: '0xtx', txNonce: 1, rawTx: '0x02aa' });
  s.markFailed('p1', 'reverted', { gasSpentWei: 5n, txHash: '0xtx' });
  const retry2 = s.claim(CLAIM({ paymentId: 'p_newer', authorizationHash: auth }));
  assert.equal(retry2.claimed, false);
  assert.equal(s.gasSpentSince(0), 5n); // reverted gas still accounted
  assert.equal(s.settledRevenueAtomic(), 0n); // failed never counts as revenue
  s.close();
});

test('durability: replay mark survives close/reopen', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'x402-store-'));
  const dbPath = path.join(dir, 'state', 'x402.db'); // exercises mkdir -p
  const auth = '0x' + '77'.repeat(32);
  let s = createStore(dbPath);
  s.claim(CLAIM({ paymentId: 'p1', authorizationHash: auth }));
  s.markSubmitting('p1', { txHash: '0xtx', txNonce: 3, rawTx: '0x02bb' });
  s.close();

  s = createStore(dbPath);
  const again = s.claim(CLAIM({ paymentId: 'p2', authorizationHash: auth }));
  assert.equal(again.claimed, false, 'restart must not allow replay');
  const inflight = s.listSubmitting();
  assert.equal(inflight.length, 1);
  assert.equal(inflight[0].raw_tx, '0x02bb'); // recovery has the exact bytes
  s.close();
  fs.rmSync(dir, { recursive: true, force: true });
});

test('gasSpentSince windows correctly and sums as BigInt', () => {
  const s = mem();
  s.claim(CLAIM({ paymentId: 'p1' }));
  s.markSubmitting('p1', { txHash: '0xt1', txNonce: 1, rawTx: '0x02' });
  s.markSettled('p1', { txHash: '0xt1', gasSpentWei: 2n ** 62n }); // beyond float53
  assert.equal(s.gasSpentSince(0), 2n ** 62n);
  assert.equal(s.gasSpentSince(Math.floor(Date.now() / 1000) + 3600), 0n);
  s.close();
});
