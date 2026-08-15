'use strict';
/**
 * ADVERSARIAL REVIEW 3/3 — vector (6) gas-drain: daily-gas-budget bypass.
 *
 * The circuit breaker (gas.js checkDailyBudget) is consulted in settle()
 * BEFORE the process-wide submit lock, and gas spend is only recorded AFTER
 * a settlement confirms. So the budget check is a classic TOCTOU: N distinct
 * valid authorizations submitted concurrently all read spent==0, all pass the
 * breaker, and all broadcast — spending many times the configured daily cap.
 *
 * FIXED (2026-08-15): the authoritative check now runs INSIDE the submit lock
 * and counts in-flight reservations (payments.gas_reserved_wei) plus the cost
 * of the settlement about to be signed, so a concurrent burst is capped
 * exactly like the sequential path. These are the regression tests.
 */

const test = require('node:test');
const assert = require('node:assert');

const { scene } = require('./evm-helpers');

// One settlement in the mock burns gasUsed*price + l1Fee:
//   0x15122*0x5f5e64 + 0x6f7ceb00 = 541,284,226,120 wei
const PER_SETTLE_WEI = 86306n * 6250020n + 1870000000n;

// The breaker reserves the WORST case at signing time — gasLimit (estimate
// x1.25 buffer) * maxFeePerGas (2*baseFee + priority) — which is larger than
// the receipt-derived spend above. Budgets in these tests are expressed in
// reservations, because that is what the cap actually meters.
const RESERVE_WEI = ((82_000n * 125n) / 100n) * (2n * 5_000_000n + 1_000_000n);

test('CONTROL (sequential): the daily gas budget stops settlements at the cap', async () => {
  // Sized so exactly two settle sequentially: each check is
  // recorded-spend + this reservation >= budget. A reservation is released
  // once the receipt reports the (smaller) real cost, which is why the budget
  // is expressed as two real settlements plus one in-flight reservation.
  const sc = scene({
    envOverrides: { X402_DAILY_GAS_BUDGET_WEI: String(PER_SETTLE_WEI * 2n + RESERVE_WEI) },
  });

  const r1 = await sc.facilitator.settle(sc.payment());
  const r2 = await sc.facilitator.settle(sc.payment());
  const r3 = await sc.facilitator.settle(sc.payment());

  assert.equal(r1.success, true, 'first settles');
  assert.equal(r2.success, true, 'second settles');
  assert.equal(r3.success, false, 'breaker refuses the third');
  assert.equal(sc.rpc.sent.length, 2, 'exactly two broadcasts when serialized');
  assert.ok(sc.store.gasSpentSince(0) <= PER_SETTLE_WEI * 2n + RESERVE_WEI,
    'recorded spend never exceeds the configured cap');
});

test('a budget smaller than one settlement refuses everything (fail closed, no overshoot)', async () => {
  const sc = scene({ envOverrides: { X402_DAILY_GAS_BUDGET_WEI: String(RESERVE_WEI - 1n) } });
  const r = await sc.facilitator.settle(sc.payment());
  assert.equal(r.success, false, 'cannot afford even one settlement -> refuse rather than overspend');
  assert.equal(sc.rpc.sent.length, 0, 'nothing broadcast');
});

test('FIXED (concurrent): distinct valid authorizations cannot race past the daily gas budget', async () => {
  const N = 6;
  // Budget for exactly one settlement; six fire in the same tick.
  const sc = scene({ envOverrides: { X402_DAILY_GAS_BUDGET_WEI: String(RESERVE_WEI + 1n) } });

  // N distinct authorizations (each sc.payment() picks a fresh random nonce
  // => distinct authorization_hash => each claims its own row).
  const bodies = Array.from({ length: N }, () => sc.payment());

  // Fire them all in the same tick — exactly what a hostile client (or an
  // agent swarm) does.
  const results = await Promise.all(bodies.map((b) => sc.facilitator.settle(b)));

  const broadcasts = sc.rpc.sent.length;
  const spent = sc.store.gasSpentSince(0);
  const budget = RESERVE_WEI + 1n;

  // FIXED: the authoritative budget check runs INSIDE the submit lock and
  // counts in-flight reservations, so the burst is capped exactly like the
  // sequential path — one settlement broadcasts, the rest are refused by the
  // breaker (previously all six broadcast, ~6x over budget).
  assert.equal(broadcasts, 1, `exactly one broadcast under a 1-settlement budget (got ${broadcasts})`);
  assert.ok(spent <= budget, `trailing spend ${spent} wei stays within the ${budget} wei cap`);

  const refusedForBudget = results.filter(
    (r) => !r.success && /X402_DAILY_GAS_BUDGET_WEI/.test(r.errorDetail || ''));
  assert.equal(refusedForBudget.length, N - 1, 'every other request was refused BY THE BREAKER specifically');
  for (const r of refusedForBudget) {
    assert.equal(r.transaction, '', 'a budget refusal never names a broadcast tx — no money moved');
  }
  // NOTE: the winner's own success is not asserted here. This mock tracks a
  // single global "last authorization" for its consume-on-send bookkeeping, so
  // six in-flight payments confuse the MOCK's post-settlement check (not the
  // gateway's). Sequential settlement success is covered by the CONTROL test
  // above and by facilitator-evm.test.js.
});

test('the daily budget is ENABLED by default, so an unconfigured deployment still has an aggregate cap', async () => {
  // Default posture matters: a fresh install sponsors gas for anyone who can
  // produce a valid $0.01 authorization, so shipping the breaker off would
  // leave the float exposed until an operator noticed. It now defaults to
  // 0.0004 ETH/day (~600 settlements at typical Base gas).
  const sc = scene(); // no X402_DAILY_GAS_BUDGET_WEI override
  assert.equal(sc.cfg.dailyGasBudgetWei, 400000000000000n, 'breaker on by default');
  assert.ok(sc.cfg.dailyGasBudgetWei > PER_SETTLE_WEI * 10n,
    'and generous enough not to throttle normal traffic');
  const N = 5;
  const bodies = Array.from({ length: N }, () => sc.payment());
  await Promise.all(bodies.map((b) => sc.facilitator.settle(b)));
  assert.equal(sc.rpc.sent.length, N, 'normal traffic is unaffected by the default cap');
});
