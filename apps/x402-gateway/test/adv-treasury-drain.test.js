'use strict';
/**
 * ADVERSARIAL REGRESSION — forced-sip drain and the slippage bound.
 *
 * FINDING D1 (fixed) — the daily swap budget was a read-then-act check with a
 * very long window: attemptSip() read `sipSpendSince()` near the top and only
 * inserted its own row AFTER the approve had been signed, broadcast and
 * confirmed (up to X402_RECEIPT_TIMEOUT_MS later). A second caller starting
 * inside that window saw a stale total and got its own full allocation, and
 * the shipped CLI (`animica-x402 treasury sip --confirm`) is exactly such a
 * second caller — a different process on the same DB, with `force` skipping
 * the cooldown that might otherwise have caught it. FIX (two layers): the
 * budget check and the intent row are now ONE `BEGIN IMMEDIATE` transaction
 * written before a wei of gas is spent, and a cross-process lease stops the
 * CLI running against a live service at all.
 *
 * FINDING D2 (fixed) — the slippage bound is anchored to the same pool it
 * protects: `amountOutMinimum = QuoterV2(quote) * (1 - 1%)`, so a manipulated,
 * thin or stale quote moved the bound with it and the fill was reported as a
 * clean success. FIX: two optional INDEPENDENT references — X402_ETH_USD_PRICE
 * (the knob the settlement path already has) and the realised rate of our own
 * last confirmed sip — with X402_TREASURY_MAX_QUOTE_DEVIATION_BPS as the band.
 */

const test = require('node:test');
const assert = require('node:assert/strict');

const evm = require('../src/facilitator-evm/evm');
const uniswap = require('../src/treasury/uniswap');
const { createTreasury } = require('../src/treasury/treasury');
const { createTreasuryStore } = require('../src/treasury/store');
const { createMetrics } = require('../src/metrics');
const { treasuryScene, decodeSipCalldata, decodeEip1559, C } = require('./treasury-helpers');
const { quietLogger } = require('./evm-helpers');

const USDC = (d) => BigInt(Math.round(d * 1e6));

/** A second process on the same DB file (what the CLI is). */
function cliTreasuryFor(s) {
  return createTreasury({
    cfg: s.cfg,
    rpc: s.rpc,
    tstore: createTreasuryStore(s.store.db, { now: s.clock.now }),
    signer: s.signer,
    metrics: createMetrics(),
    logger: quietLogger,
    now: s.clock.now,
    sleep: s.clock.sleep,
  });
}

test('D1: a second caller inside the approve window can no longer double the daily swap budget', async () => {
  const s = treasuryScene({
    eth: 1n,
    usdc: USDC(30),
    env: { X402_TREASURY_DAILY_SWAP_BUDGET_USDC: '5.00' }, // one $5 sip per day, by policy
  });
  const cli = cliTreasuryFor(s);

  // The operator runs `treasury sip --confirm` while the service is already
  // mid-sip (its approve is in flight). The intent row is written BEFORE the
  // approve now, so the second caller sees the commitment immediately.
  let fired = false;
  let cliResult = null;
  const inner = s.rpc.call.bind(s.rpc);
  s.rpc.call = async (method, params = []) => {
    const out = await inner(method, params);
    if (!fired && method === 'eth_estimateGas'
        && String(params[0].data).startsWith(uniswap.SELECTORS.approve)) {
      fired = true;
      cliResult = await cli.sipNow();
    }
    return out;
  };

  const service = await s.treasury.attemptSip({ trigger: 'interval' });
  assert.equal(service.action, 'sipped');
  assert.ok(fired, 'the second caller really did run inside the window');
  assert.equal(cliResult.action, 'skipped');
  // Two independent guards close this window, and whichever fires first is a
  // correct answer: the in-flight approve is an unresolved action (nothing
  // new may be signed onto that nonce lane), and the budget row is already
  // committed (proved on its own in D1a).
  assert.ok(['unresolved_action', 'daily_budget_exhausted'].includes(cliResult.reason),
    `expected the second caller to be stopped, got ${cliResult.reason}`);

  const spent = s.tstore.sipSpendSince(0);
  assert.equal(spent, USDC(5), 'exactly the $5/day the operator configured');
  assert.ok(spent <= s.cfg.treasury.dailySwapBudgetAtomic);
  const rows = s.tstore.list({ kind: 'sip', limit: 10 });
  assert.equal(rows.length, 1);
});

test('D1a: the budget check and the intent row are one atomic write (the CLI cannot even start a second one)', async () => {
  const s = treasuryScene({ eth: 1n, usdc: USDC(30), env: { X402_TREASURY_DAILY_SWAP_BUDGET_USDC: '5.00' } });
  const store = require('node:fs').readFileSync(require('node:path').join(__dirname, '..', 'src', 'treasury', 'store.js'), 'utf8');
  assert.match(store, /beginSipTx\.immediate/, 'BEGIN IMMEDIATE takes SQLite\'s write lock across processes');

  const claim = s.tstore.beginSip({
    amount: USDC(5), minAmount: USDC(0.5), budget: USDC(5), windowSec: 86_400, destination: C.swapRouter02,
  });
  assert.equal(claim.ok, true);
  const second = s.tstore.beginSip({
    amount: USDC(5), minAmount: USDC(0.5), budget: USDC(5), windowSec: 86_400, destination: C.swapRouter02,
  });
  assert.equal(second.ok, false);
  assert.equal(second.reason, 'daily_budget_exhausted');
});

test('D1b: force still bypasses the cooldown — and still cannot bypass the budget', async () => {
  const s = treasuryScene({ eth: 1n, usdc: USDC(30) }); // default budget $10/day
  const first = await s.treasury.attemptSip({ trigger: 'interval' });
  assert.equal(first.action, 'sipped');
  s.rpc.setEth(s.facilitator, 1n);
  assert.equal((await s.treasury.attemptSip({ trigger: 'settlement' })).reason, 'cooldown', 'the automatic path is held');

  const forced = await s.treasury.sipNow();
  assert.equal(forced.action, 'sipped', 'the operator override still works');
  assert.equal(s.tstore.sipSpendSince(0), USDC(10));

  s.rpc.setEth(s.facilitator, 1n);
  const third = await s.treasury.sipNow();
  assert.equal(third.reason, 'daily_budget_exhausted', 'and the budget is the wall force cannot walk through');
});

test('D1c: the CLI is locked out of a live service entirely (the lease, not just the budget)', async () => {
  const s = treasuryScene({ eth: 1n, usdc: USDC(30) });
  const cli = cliTreasuryFor(s);
  assert.equal(s.treasury.acquireLease({ label: 'facilitator' }).ok, true);
  const denied = cli.acquireLease({ label: 'cli:999', ttlS: 300 });
  assert.equal(denied.ok, false);
  assert.equal(denied.holder.label, 'facilitator');
});

test('D2: a quote far below an independent price reference is refused, not "settled"', async () => {
  // The quoter reports 10% of the fair rate (a manipulated/thin pool, or a
  // stale price). amountOutMinimum derives from that number alone, so before
  // the fix the module reported a clean, successful sip having accepted >10x
  // less ETH than fair value.
  const s = treasuryScene({
    eth: 1n,
    usdc: USDC(30),
    chain: { quoteInflateBps: -9000 },             // quote = 10% of fair
    env: { X402_ETH_USD_PRICE: '1880' },           // ~the rate the mock pool quotes
  });
  s.rpc.state.allowance.set(`${s.facilitator.toLowerCase()}:${C.swapRouter02.toLowerCase()}`, 10n ** 18n);

  const r = await s.treasury.attemptSip({ trigger: 'interval' });
  assert.equal(r.action, 'skipped');
  assert.equal(r.reason, 'quote_off_reference');
  assert.equal(s.rpc.sent.length, 0, 'not a cent of revenue was converted at that rate');
  assert.equal(s.treasury.status().sip_consecutive_failures, 0, 'a bad market is a skip, never a strike');

  // The band is wide enough that ordinary price movement still sips.
  const ok = treasuryScene({
    eth: 1n, usdc: USDC(30),
    chain: { quoteInflateBps: -2000 },             // 20% below the reference
    env: { X402_ETH_USD_PRICE: '1880' },
  });
  ok.rpc.state.allowance.set(`${ok.facilitator.toLowerCase()}:${C.swapRouter02.toLowerCase()}`, 10n ** 18n);
  assert.equal((await ok.treasury.attemptSip({ trigger: 'interval' })).action, 'sipped');
});

test('D2b: with no price knob, our own last realised sip rate catches the same manipulation', async () => {
  const s = treasuryScene({
    eth: 1n, usdc: USDC(30),
    env: { X402_TREASURY_SIP_COOLDOWN_S: '0', X402_TREASURY_DAILY_SWAP_BUDGET_USDC: '100.00' },
  });
  s.rpc.state.allowance.set(`${s.facilitator.toLowerCase()}:${C.swapRouter02.toLowerCase()}`, 10n ** 18n);
  assert.equal(s.cfg.ethUsdPrice, 0n, 'no operator price configured — the default posture');

  const first = await s.treasury.attemptSip({ trigger: 'interval' });
  assert.equal(first.action, 'sipped', 'a normal sip establishes the realised rate');

  // Now the pool quotes 10% of what we actually filled at last time.
  s.rpc.state.quoteInflateBps = -9000;
  s.rpc.setEth(s.facilitator, 1n);
  const r = await s.treasury.attemptSip({ trigger: 'interval' });
  assert.equal(r.reason, 'quote_off_last_realized');

  // A stale reference must not become a refuel deadlock: past
  // X402_TREASURY_RATE_REFERENCE_MAX_AGE_S it stops being a reference.
  s.clock.advance((s.cfg.treasury.rateReferenceMaxAgeS + 60) * 1000);
  const later = await s.treasury.attemptSip({ trigger: 'interval' });
  assert.equal(later.action, 'sipped', 'an ancient rate is not evidence about today\'s price');
});

test('D2c: the enforced daily exposure is the swap budget, and the module says so', async () => {
  const s = treasuryScene({ eth: 1n, usdc: USDC(1_000) });
  assert.equal(s.cfg.treasury.dailySwapBudgetAtomic, USDC(10));
  assert.equal(s.treasury.status().policy.daily_swap_budget_usdc, '10');
  assert.equal(s.treasury.status().policy.max_quote_deviation_bps, 5000);

  // And docs/x402.md states the enforced bound rather than an assumption
  // about pool depth.
  const docs = require('node:fs').readFileSync(
    require('node:path').join(__dirname, '..', '..', '..', 'docs', 'x402.md'), 'utf8');
  assert.match(docs, /X402_TREASURY_DAILY_SWAP_BUDGET_USDC/);
  assert.match(docs, /X402_ETH_USD_PRICE/);
});

test('D2d: the amountOutMinimum bound itself is unchanged (quote minus exactly the configured slippage)', async () => {
  const s = treasuryScene({ eth: 1n, usdc: USDC(5), env: { X402_TREASURY_MAX_SLIPPAGE_BPS: '250' } });
  await s.treasury.attemptSip({ trigger: 'interval' });
  const swapTx = s.rpc.sent.find((x) => evm.addressEquals(x.tx.to, C.swapRouter02));
  const call = decodeSipCalldata(swapTx.tx.data);
  const quote = s.rpc.quoteOut(USDC(5), call.swap.fee);
  assert.equal(call.swap.amountOutMinimum, (quote * 9750n) / 10_000n);
  assert.equal(call.unwrap.amountMinimum, call.swap.amountOutMinimum);
  const tx = decodeEip1559(swapTx.raw);
  assert.equal(tx.value, 0n);
});
