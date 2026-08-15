'use strict';
/**
 * ADVERSARIAL REVIEW — treasury economics / bootstrap stall.
 *
 * These are PROOFS OF A DEFECT, not regression guards for intended
 * behaviour. Each test asserts what the module ACTUALLY does today so the
 * suite stays green; the header of each says what is wrong with that.
 *
 * FINDING E1 — the `uneconomic` guard silently vetoes the adaptive sip at
 * ordinary Base fees, and vetoes EVERY sip at moderately elevated ones.
 *
 *   attemptSip() computes
 *
 *     worstGasWei = (maxApproveGas + maxSwapGas) * fees.maxFeePerGas
 *                 = (100_000 + 300_000) * (2*baseFee + priority)
 *     skip unless  minOut >= worstGasWei * minEthOutGasRatio   (ratio 20)
 *
 *   Both factors are worst case: 400k gas is the sum of the two CAPS (the
 *   measured cost is 56k + 165k, and 0 + 165k once an allowance exists), and
 *   maxFeePerGas is 2*baseFee + tip, roughly double what the tx actually pays.
 *   The guard therefore demands the sip buy ~90x the ETH it really costs.
 *
 *   Because the required minimum scales with baseFee while the sip is capped
 *   at X402_TREASURY_SIP_USDC ($5), there is a base fee above which NO sip of
 *   any size is permitted — with the shipped defaults that is ~0.17 gwei.
 *   docs/x402.md quotes Base's typical fee as 0.006 gwei, i.e. the veto arrives
 *   ~28x above "typical" and ~4x above typical for the $0.75 sip that the
 *   spec's own bootstrap-stall scenario depends on.
 *
 * FINDING E2 — the veto is invisible. `uneconomic` is a skip: no strike, no
 * breaker, no /readyz warning, `x402_treasury_sipping_enabled` stays 1 and
 * `treasury status` still reports sipping_disabled=false. The only symptom is
 * an ETH balance that keeps falling until the facilitator's own gas-balance
 * readiness check fails and paid traffic stops — which is precisely the
 * outcome the module exists to prevent.
 */

const test = require('node:test');
const assert = require('node:assert/strict');

const { treasuryScene, QUOTE_NUMER, QUOTE_DENOM } = require('./treasury-helpers');

const USDC = (d) => BigInt(Math.round(d * 1e6));

/** The module's own admission rule, restated (treasury.js attemptSip). */
function minViableSipAtomic(cfg, baseFeeWei, priorityWei = 1_000_000n) {
  const t = cfg.treasury;
  const maxFee = 2n * baseFeeWei + priorityWei;
  const worstGasWei = (t.maxApproveGas + t.maxSwapGas) * maxFee;
  const required = worstGasWei * BigInt(t.minEthOutGasRatio);
  // best allowlisted tier's wei-per-USDC-atomic, after the slippage haircut
  const bestNumer = t.poolFees.reduce((a, f) => (QUOTE_NUMER[f] > a ? QUOTE_NUMER[f] : a), 0n);
  const weiPerAtomic = (n) => ((n * bestNumer) / QUOTE_DENOM) * (10_000n - BigInt(t.maxSlippageBps)) / 10_000n;
  // smallest A with weiPerAtomic(A) >= required (linear, so just divide + fix up)
  let a = (required * QUOTE_DENOM) / (bestNumer) + 1n;
  while (weiPerAtomic(a) < required) a += 1n;
  return a;
}

test('PoC E1a: at 4x Base\'s documented typical fee the $0.75 bootstrap sip is vetoed as "uneconomic"', async () => {
  // The spec's bootstrap-stall scenario verbatim: "ETH gone at ~75
  // settlements with only ~$0.75 accrued". A gas spike is what emptied the
  // wallet, so the sip that is supposed to rescue it happens AT the spiked
  // fee — 0.025 gwei here, still 40x below Base's busiest observed levels.
  const s = treasuryScene({
    eth: 40_000_000_000_000n,          // 0.00004 ETH — under the 0.0001 floor
    usdc: USDC(0.75),
    chain: { baseFee: 25_000_000n },   // 0.025 gwei
  });

  const threshold = minViableSipAtomic(s.cfg, 25_000_000n);
  assert.ok(threshold > USDC(0.75), `at 0.025 gwei the guard needs >= ${threshold} atomic, i.e. $${Number(threshold) / 1e6}`);

  const r = await s.treasury.tick();
  assert.equal(r.sip.action, 'skipped');
  assert.equal(r.sip.reason, 'uneconomic', 'the adaptive sip the spec designed for this exact case is refused');
  assert.equal(s.rpc.sent.length, 0, 'nothing was broadcast: the wallet stays out of gas');

  // FINDING E2: totally silent.
  assert.equal(s.treasury.warning(), null, 'no /readyz warning');
  assert.equal(s.treasury.status().sipping_disabled, false, 'the breaker never fires — this is a skip, not a failure');
  assert.match(s.metrics.render(), /x402_treasury_sipping_enabled(\{\})? 1/, 'the metric still says sipping is enabled');
  assert.equal(s.logs.filter((l) => l.level === 'error' || l.level === 'warn').length, 0, 'not even a warn-level log');
});

test('PoC E1b: the stall is permanent — ticking for a simulated week never sips', async () => {
  const s = treasuryScene({
    eth: 40_000_000_000_000n,
    usdc: USDC(0.75),
    chain: { baseFee: 25_000_000n },
  });
  for (let i = 0; i < 7 * 288; i++) {           // a week of 300 s intervals
    const r = await s.treasury.tick();
    assert.equal(r.sip.reason, 'uneconomic');
    s.clock.advance(300_000);
  }
  assert.equal(s.rpc.sent.length, 0);
  assert.equal(s.tstore.totals().sips, 0);
  assert.equal(s.treasury.warning(), null, 'a week of not refuelling produces no operator-visible signal');
});

test('PoC E1c: above ~0.17 gwei NO sip is possible at any size — even with $20 of revenue', async () => {
  const baseFee = 170_000_000n; // 0.17 gwei
  const s = treasuryScene({
    eth: 1n,                                   // effectively empty: emergency
    usdc: USDC(20),                            // twenty dollars of accrued revenue
    chain: { baseFee },
  });
  const threshold = minViableSipAtomic(s.cfg, baseFee);
  assert.ok(
    threshold > s.cfg.treasury.sipUsdcAtomic,
    `guard needs $${Number(threshold) / 1e6} but X402_TREASURY_SIP_USDC caps the sip at $${Number(s.cfg.treasury.sipUsdcAtomic) / 1e6}`
  );

  const r = await s.treasury.tick();
  assert.equal(r.sip.reason, 'uneconomic');
  // Manual override does not help either: `force` deliberately does not
  // bypass the economic check, so the documented operator remedy
  // (`animica-x402 treasury sip --confirm`) is refused too.
  const forced = await s.treasury.sipNow();
  assert.equal(forced.action, 'skipped');
  assert.equal(forced.reason, 'uneconomic', 'the CLI escape hatch is vetoed by the same rule');
});

test('PoC E1d: the veto threshold, tabulated — it tracks baseFee linearly while the sip is capped', () => {
  const s = treasuryScene({ eth: 10n ** 15n, usdc: 0n });
  const rows = [6_000_000n, 12_000_000n, 25_000_000n, 50_000_000n, 100_000_000n, 170_000_000n]
    .map((bf) => ({ gwei: Number(bf) / 1e9, minSipUsd: Number(minViableSipAtomic(s.cfg, bf)) / 1e6 }));

  // documented "typical" Base fee — the only row where the $0.50 adaptive
  // minimum is actually usable
  assert.ok(rows[0].minSipUsd < 0.5, `at 0.006 gwei the floor is $${rows[0].minSipUsd}`);
  assert.ok(rows[2].minSipUsd > 0.75, `at 0.025 gwei the floor is $${rows[2].minSipUsd}`);
  assert.ok(rows[3].minSipUsd > 1.5, `at 0.05 gwei the floor is $${rows[3].minSipUsd}`);
  assert.ok(rows[5].minSipUsd > 5.0, `at 0.17 gwei the floor is $${rows[5].minSipUsd} — above the $5 sip cap`);
});

test('control: the SAME scenario at the documented typical fee does sip — so the guard, not the design, is the blocker', async () => {
  const s = treasuryScene({
    eth: 40_000_000_000_000n,
    usdc: USDC(0.75),
    chain: { baseFee: 6_000_000n },  // 0.006 gwei, docs/x402.md's "typical"
  });
  const r = await s.treasury.tick();
  assert.equal(r.sip.action, 'sipped');
  assert.equal(r.sip.amount_usdc_atomic, USDC(0.75));
});
