'use strict';
/**
 * ADVERSARIAL REGRESSION — treasury economics / bootstrap stall.
 *
 * FINDING E1 (fixed) — the `uneconomic` guard used to veto the adaptive sip at
 * ordinary Base fees and to veto EVERY sip above ~0.17 gwei. It priced the
 * SUM OF THE GAS CAPS (100k + 300k) at the FEE CEILING (2*baseFee + tip) and
 * demanded 20x that — roughly 90x what a sip really costs. The spec's own
 * bootstrap scenario ("ETH gone at ~75 settlements with only ~$0.75 accrued")
 * happens AT the elevated fee that emptied the wallet, i.e. exactly where the
 * guard refused.
 *
 * The fix prices the attempt: MEASURED gas for the legs it will actually send
 * (the approve leg is dropped when an allowance already covers the swap) at
 * the fee the transaction actually pays (baseFee + tip), with a 4x margin.
 * The guard still exists — it is what stops a dust sip or a broken fee market
 * from converting revenue at a loss — it just no longer fires at fees the
 * network sees every day.
 *
 * FINDING E2 (fixed) — the veto was invisible: `uneconomic` is a skip, so no
 * strike, no breaker, no /readyz warning, and `x402_treasury_sipping_enabled`
 * stayed 1 while the wallet drained. There is now a refuel-blocked streak: N
 * consecutive checks under the ETH floor with the sip skipped raise the
 * /readyz WARNING, set `x402_treasury_refuel_blocked` and log at error level.
 */

const test = require('node:test');
const assert = require('node:assert/strict');

const { treasuryScene, QUOTE_NUMER, QUOTE_DENOM, C } = require('./treasury-helpers');
const { MEASURED_GAS } = require('../src/treasury/treasury');

const USDC = (d) => BigInt(Math.round(d * 1e6));

/** The module's admission rule, restated (treasury.js priceForAmount). */
function minViableSipAtomic(cfg, baseFeeWei, { priorityWei = 1_000_000n, needsApprove = true } = {}) {
  const t = cfg.treasury;
  const gasUnits = MEASURED_GAS.swap + (needsApprove ? MEASURED_GAS.approve : 0n);
  const required = gasUnits * (baseFeeWei + priorityWei) * BigInt(t.minEthOutGasRatio);
  const bestNumer = t.poolFees.reduce((a, f) => (QUOTE_NUMER[f] > a ? QUOTE_NUMER[f] : a), 0n);
  const weiPerAtomic = (n) => (((n * bestNumer) / QUOTE_DENOM) * (10_000n - BigInt(t.maxSlippageBps))) / 10_000n;
  let a = (required * QUOTE_DENOM) / bestNumer + 1n;
  while (weiPerAtomic(a) < required) a += 1n;
  return a;
}

test('E1a: the spec\'s bootstrap scenario ($0.75 accrued, 4x typical gas) now refuels', async () => {
  // Verbatim from the spec: "ETH gone at ~75 settlements with only ~$0.75
  // accrued". A gas spike is what emptied the wallet, so the sip that has to
  // rescue it happens AT the spiked fee — 0.025 gwei here.
  const s = treasuryScene({
    eth: 40_000_000_000_000n,          // 0.00004 ETH — under the floor
    usdc: USDC(0.75),
    chain: { baseFee: 25_000_000n },   // 0.025 gwei
  });

  const threshold = minViableSipAtomic(s.cfg, 25_000_000n);
  assert.ok(threshold < USDC(0.75), `the guard now needs only $${Number(threshold) / 1e6} at 0.025 gwei`);

  const r = await s.treasury.tick();
  assert.equal(r.sip.action, 'sipped', 'the adaptive sip the spec designed for this exact case runs');
  assert.equal(r.sip.amount_usdc_atomic, USDC(0.75));
  assert.equal(r.sip.emergency, true);
  assert.ok(s.balances().eth > 3e14, 'and the wallet is fuelled again');
  assert.equal(s.treasury.warning(), null);
});

test('E1b: at 0.17 gwei — where NO sip of any size used to be permitted — a $0.50 sip still runs', async () => {
  const baseFee = 170_000_000n;
  const s = treasuryScene({ eth: 1n, usdc: USDC(0.5), chain: { baseFee } });
  const threshold = minViableSipAtomic(s.cfg, baseFee);
  assert.ok(threshold <= USDC(0.5), `minimum viable sip at 0.17 gwei is $${Number(threshold) / 1e6}`);

  const r = await s.treasury.tick();
  assert.equal(r.sip.action, 'sipped');
  assert.equal(r.sip.amount_usdc_atomic, USDC(0.5));
});

test('E1c: the guard is still a guard — a dust-sized sip in an expensive fee market is refused', async () => {
  // 0.5 gwei is ~80x Base's typical fee (and the highest the shipped
  // X402_MAX_FEE_PER_GAS_WEI=1 gwei cap even allows a transaction at). A
  // $0.50 sip there would spend a large fraction of itself on gas, so the
  // guard holds — the same skip protects against a dust sip and a broken fee
  // oracle.
  const s = treasuryScene({ eth: 1n, usdc: USDC(0.5), chain: { baseFee: 500_000_000n } });
  const r = await s.treasury.tick();
  assert.equal(r.sip.action, 'skipped');
  assert.equal(r.sip.reason, 'uneconomic');
  assert.equal(s.rpc.sent.length, 0);

  // ...and a bigger balance clears it, because the cost is fixed per attempt
  // while the proceeds scale with size.
  const big = treasuryScene({ eth: 1n, usdc: USDC(50), chain: { baseFee: 500_000_000n } });
  assert.equal((await big.treasury.tick()).sip.action, 'sipped');

  // Above the fee cap it is the cap, not the economics, that stops us — and
  // that is a skip too, never a strike.
  const capped = treasuryScene({ eth: 1n, usdc: USDC(50), chain: { baseFee: 30_000_000_000n } });
  const cappedR = await capped.treasury.tick();
  assert.equal(cappedR.sip.reason, 'fee_estimate_failed');
  assert.equal(capped.treasury.status().sip_consecutive_failures, 0);
});

test('E1d: the veto threshold, tabulated — it stays under the $0.50 adaptive floor across Base\'s real fee range', () => {
  const s = treasuryScene({ eth: 10n ** 15n, usdc: 0n });
  const rows = [6_000_000n, 12_000_000n, 25_000_000n, 50_000_000n, 100_000_000n, 170_000_000n, 500_000_000n]
    .map((bf) => ({ gwei: Number(bf) / 1e9, minSipUsd: Number(minViableSipAtomic(s.cfg, bf)) / 1e6 }));

  // Up to ~28x Base's documented typical fee (0.006 gwei) the ADAPTIVE $0.50
  // minimum is viable — this is the band the bootstrap-stall cure lives in,
  // and the pre-fix guard vetoed it from 0.012 gwei upwards.
  for (const row of rows.slice(0, 6)) {
    assert.ok(row.minSipUsd < 0.5, `at ${row.gwei} gwei the minimum viable sip is $${row.minSipUsd} (must stay under the $0.50 floor)`);
  }
  // At 0.5 gwei — the highest fee X402_MAX_FEE_PER_GAS_WEI even permits a
  // transaction at — a sip is still possible well inside the $5 cap, so the
  // mechanism no longer dies before the network does.
  assert.ok(rows[6].minSipUsd < Number(s.cfg.treasury.sipUsdcAtomic) / 1e6,
    `at 0.5 gwei the minimum viable sip is $${rows[6].minSipUsd}, under the $5 sip cap`);
});

test('E1e: an existing allowance drops the approve leg from the cost, so the check is not paying for gas twice', async () => {
  const s = treasuryScene({ eth: 1n, usdc: USDC(0.5), chain: { baseFee: 25_000_000n } });
  s.rpc.state.allowance.set(`${s.facilitator.toLowerCase()}:${C.swapRouter02.toLowerCase()}`, 10n ** 18n);
  const withApprove = minViableSipAtomic(s.cfg, 25_000_000n, { needsApprove: true });
  const without = minViableSipAtomic(s.cfg, 25_000_000n, { needsApprove: false });
  assert.ok(without < withApprove, 'a pre-existing allowance is cheaper, and the guard knows it');
  const r = await s.treasury.attemptSip({ trigger: 'interval' });
  assert.equal(r.action, 'sipped');
  assert.equal(s.rpc.sent.length, 1, 'no approve transaction was needed');
});

test('E2: a wallet that cannot refuel raises a /readyz WARNING, a metric and an error log', async () => {
  // A refuel that is blocked for ANY skip reason (here: no liquidity at all —
  // the quoter is down) used to be perfectly silent while the wallet drained.
  const s = treasuryScene({
    eth: 40_000_000_000_000n,
    usdc: USDC(5),
    chain: { quoteError: 'execution reverted' },
    env: { X402_TREASURY_REFUEL_ALERT_TICKS: '3', X402_TREASURY_SIP_COOLDOWN_S: '0' },
  });

  for (let i = 0; i < 2; i++) {
    const r = await s.treasury.tick();
    assert.equal(r.sip.reason, 'no_quote');
    assert.equal(s.treasury.warning(), null, 'one or two blocked checks are noise, not an alert');
  }
  const third = await s.treasury.tick();
  assert.equal(third.sip.reason, 'no_quote');
  assert.match(s.treasury.warning(), /cannot refuel/);
  assert.match(s.metrics.render(), /x402_treasury_refuel_blocked(\{\})? 1/);
  assert.ok(s.logs.some((l) => l.level === 'error' && l.event === 'treasury_refuel_blocked'));
  assert.equal(s.treasury.status().refuel_blocked_ticks, 3);
  assert.equal(s.treasury.status().sipping_disabled, false, 'a skip is still not a failure — no breaker, no strike');

  // The facilitator keeps taking payments: this is a warning, not readiness.
  s.rpc.setEth(s.facilitator, 10n ** 18n);
  const ready = await s.createFacilitator().readiness();
  assert.equal(ready.ready, true);

  // Recovery clears it.
  s.rpc.state.quoteError = null;
  s.rpc.setEth(s.facilitator, 1n);
  const ok = await s.treasury.tick();
  assert.equal(ok.sip.action, 'sipped');
  assert.equal(s.treasury.status().refuel_blocked_ticks, 0);
  assert.equal(s.treasury.warning(), null);
});

test('E2b: being ABOVE the floor is never a refuel alert (a healthy wallet skips every tick)', async () => {
  const s = treasuryScene({ eth: 10n ** 16n, usdc: USDC(1), env: { X402_TREASURY_REFUEL_ALERT_TICKS: '2' } });
  for (let i = 0; i < 10; i++) {
    const r = await s.treasury.tick();
    assert.equal(r.sip.reason, 'above_eth_floor');
  }
  assert.equal(s.treasury.warning(), null);
  assert.equal(s.treasury.status().refuel_blocked_ticks, 0);
});
