'use strict';
/**
 * ADVERSARIAL REGRESSION — money accounting and the sweep's balance oracle.
 *
 * FINDING A1 (fixed) — an unknown-outcome sip was money that moved on-chain
 * and never moved in the ledger: the row stayed 'submitting' forever (the
 * settlement engine's recoverInFlight() only walks the payments table, and
 * the treasury had no recovery entry point and no stored raw tx). `treasury
 * status` reported $0 sipped while the chain had spent $5, and the gas the
 * transaction burned was never accounted at all. FIX: raw tx + params
 * persisted, treasury.recover() runs at startup and at the top of every tick
 * and resolves each row from chain truth (receipt -> Withdrawal/Transfer logs
 * -> confirm, with gas).
 *
 * FINDING A2 (fixed) — tick() only re-read balances when the sip returned
 * exactly 'sipped', so an 'unknown' sip left the sweep sizing itself from a
 * pre-sip number: it over-drained past the operating float, or (with a
 * perfectly legal low ceiling) reverted twice and DISABLED SWEEPING. FIX:
 * attemptSweep reads its own balance, and it refuses to act at all while a
 * treasury action is unresolved.
 *
 * FINDING A3 (fixed) — treasury gas was invisible to X402_DAILY_GAS_BUDGET_WEI
 * (computed from the payments table only), so the documented daily ETH
 * ceiling bounded half the outflow and the treasury kept spending while the
 * breaker was open. FIX: the breaker sums both ledgers, and the treasury
 * checks it before signing.
 *
 * FINDING A4 (fixed) — an approve that landed followed by a swap that failed
 * left a standing SwapRouter02 allowance on a hot wallet holding live
 * revenue. FIX: the allowance is reset to zero in the same tick.
 */

const test = require('node:test');
const assert = require('node:assert/strict');

const gasMod = require('../src/facilitator-evm/gas');
const uniswap = require('../src/treasury/uniswap');
const evm = require('../src/facilitator-evm/evm');
const { treasuryScene, C } = require('./treasury-helpers');

const USDC = (d) => BigInt(Math.round(d * 1e6));

/**
 * Hide the receipt of the SIP transaction (an RPC hiccup while polling the
 * swap). `reveal()` ends the outage, exactly as a real RPC recovering would.
 */
function hideSipReceipt(s) {
  const inner = s.rpc.call.bind(s.rpc);
  const hidden = new Set();
  let hiding = true;
  s.rpc.call = async (method, params = []) => {
    if (method === 'eth_sendRawTransaction') {
      const hash = await inner(method, params);
      const to = s.rpc.decodeEip1559(params[0]).to;
      if (String(to).toLowerCase() === C.swapRouter02.toLowerCase()) hidden.add(hash);
      return hash;
    }
    if (hiding && method === 'eth_getTransactionReceipt' && hidden.has(params[0])) return null;
    return inner(method, params);
  };
  return { reveal: () => { hiding = false; } };
}

test('A1: an unknown-outcome sip is reconciled from chain truth on the next pass', async () => {
  const s = treasuryScene({ eth: 1n, usdc: USDC(30) });
  s.rpc.state.allowance.set(`${s.facilitator.toLowerCase()}:${C.swapRouter02.toLowerCase()}`, 10n ** 18n);
  const before = s.balances();
  const rpcOutage = hideSipReceipt(s);

  const sip = await s.treasury.attemptSip({ trigger: 'interval' });
  assert.equal(sip.action, 'unknown');

  // The chain executed it: $5 of USDC gone, ETH received.
  const after = s.balances();
  assert.equal(before.usdc - after.usdc, USDC(5), 'the swap really spent $5 of revenue');
  assert.ok(after.eth > before.eth, 'and really delivered ETH');

  // Until it is resolved the module says so, loudly, and signs nothing new.
  assert.equal(s.tstore.unresolvedCount(), 1);
  assert.match(s.treasury.warning(), /unresolved on-chain/);
  assert.equal((await s.treasury.attemptSip({ trigger: 'interval' })).reason, 'unresolved_action');

  // The RPC comes back and the ledger catches up with the chain.
  rpcOutage.reveal();
  const rec = await s.treasury.recover({ trigger: 'test' });
  assert.equal(rec.confirmed, 1);

  const totals = s.tstore.totals();
  assert.equal(totals.sips, 1);
  assert.equal(totals.sippedUsdcAtomic, USDC(5), 'ledger: $5 sipped, exactly what the chain shows');
  assert.equal(totals.ethReceivedWei, after.eth - before.eth, 'ledger ETH == the WETH9 Withdrawal log');
  assert.ok(totals.gasSpentWei > 0n, 'and the gas the transaction burned is accounted');

  const st = s.treasury.status();
  assert.equal(st.totals.sipped_usdc, '5');
  assert.equal(st.warning, null);
  assert.equal(s.tstore.list({ kind: 'sip', limit: 5 })[0].status, 'confirmed');

  // Reconciliation, as the spec words it: chain delta == ledger.
  assert.equal(before.usdc - after.usdc, totals.sippedUsdcAtomic + totals.sweptUsdcAtomic);
});

test('A1b: recovery is wired into startup and into every tick, not only into a CLI command', async () => {
  const server = require('node:fs').readFileSync(require('node:path').join(__dirname, '..', 'src', 'facilitator-evm', 'server.js'), 'utf8');
  assert.match(server, /facilitator\.treasury\.recover\(\{ trigger: 'startup' \}\)/);
  const treasury = require('node:fs').readFileSync(require('node:path').join(__dirname, '..', 'src', 'treasury', 'treasury.js'), 'utf8');
  assert.match(treasury, /if \(tstore\.unresolvedCount\(\) > 0\) report\.recovery = await recover\(\{ trigger \}\)/);

  // ...and the tick really does it.
  const s = treasuryScene({ eth: 1n, usdc: USDC(30) });
  s.rpc.state.allowance.set(`${s.facilitator.toLowerCase()}:${C.swapRouter02.toLowerCase()}`, 10n ** 18n);
  const rpcOutage = hideSipReceipt(s);
  assert.equal((await s.treasury.attemptSip({ trigger: 'interval' })).action, 'unknown');
  rpcOutage.reveal();
  const t2 = await s.treasury.tick();
  assert.equal(t2.recovery.confirmed, 1, 'the next tick reconciled it with no operator involvement');
  assert.equal(s.tstore.totals().sips, 1);
});

test('A2a: after an unknown sip the sweep never sizes itself from the pre-sip balance', async () => {
  // Defaults: ceiling $20. Wallet holds $24, ETH under the floor. Pre-fix the
  // sweep moved $24-$20 = $4 after the sip had already spent $5, leaving the
  // wallet $5 BELOW the float it is supposed to keep.
  const s = treasuryScene({ eth: 1n, usdc: USDC(24) });
  s.rpc.state.allowance.set(`${s.facilitator.toLowerCase()}:${C.swapRouter02.toLowerCase()}`, 10n ** 18n);
  const rpcOutage = hideSipReceipt(s);

  const r = await s.treasury.tick();
  assert.equal(r.sip.action, 'unknown');
  assert.equal(r.sweep.reason, 'unresolved_action', 'nothing is signed while an outcome is unknown');
  assert.equal(s.rpc.getUsdc(s.cold), 0n);

  rpcOutage.reveal();
  const r2 = await s.treasury.tick();
  assert.equal(r2.recovery.confirmed, 1);
  // $24 - $5 sipped = $19, which is UNDER the ceiling: nothing to sweep.
  assert.equal(r2.sweep.reason, 'below_ceiling');
  assert.equal(s.balances().usdc, USDC(19), 'the operating float is intact');
});

test('A2b: with a legal low ceiling the sweep sizes from the fresh balance and never walks the breaker', async () => {
  // X402_TREASURY_USDC_CEILING=1.00 passes every startup validation and is a
  // sane choice for an operator who wants the hot wallet nearly empty.
  const s = treasuryScene({
    eth: 1n,
    usdc: USDC(10),
    env: { X402_TREASURY_USDC_CEILING: '1.00', X402_TREASURY_SIP_COOLDOWN_S: '0' },
  });
  s.rpc.state.allowance.set(`${s.facilitator.toLowerCase()}:${C.swapRouter02.toLowerCase()}`, 10n ** 18n);
  const rpcOutage = hideSipReceipt(s);

  const r1 = await s.treasury.tick();
  assert.equal(r1.sip.action, 'unknown');
  assert.equal(r1.sweep.reason, 'unresolved_action');

  rpcOutage.reveal();
  const r2 = await s.treasury.tick();
  assert.equal(r2.recovery.confirmed, 1);
  assert.equal(r2.sweep.action, 'swept');
  assert.equal(r2.sweep.amount_usdc_atomic, USDC(4), 'balance ($5 after the sip) minus the $1 ceiling');
  assert.equal(s.rpc.getUsdc(s.cold), USDC(4));

  const st = s.treasury.status();
  assert.equal(st.sweep_consecutive_failures, 0);
  assert.equal(st.sweeping_disabled, false, 'the drain is intact — it is the compensating control for single-wallet mode');
});

test('A3: treasury gas counts against the daily gas budget, and an open breaker stops the treasury', async () => {
  const s = treasuryScene({ eth: 1n, usdc: USDC(60) });
  const r = await s.treasury.tick();
  assert.equal(r.sip.action, 'sipped');
  assert.equal(r.sweep.action, 'swept');

  const treasuryGas = s.tstore.totals().gasSpentWei;
  assert.ok(treasuryGas > 0n, 'the treasury demonstrably burned ETH');
  assert.equal(s.store.gasSpentSince(0), 0n, 'settlement gas is still counted separately (readable economics)');

  // The breaker's basis now includes it.
  assert.equal(s.store.treasuryGasSpentSince(0), treasuryGas);
  const now = s.clock.now;
  const err = gasMod.checkDailyBudget(s.store, treasuryGas + 1n, { now });
  assert.equal(err, null, 'one wei of headroom left');
  const open = gasMod.checkDailyBudget(s.store, treasuryGas, { now });
  assert.ok(open, 'at the budget the breaker opens on treasury spend alone');
  assert.match(open.message, /treasury /);

  // ...and a treasury with the breaker already open refuses to spend more.
  const tight = treasuryScene({
    eth: 1n, usdc: USDC(60),
    env: { X402_DAILY_GAS_BUDGET_WEI: '1000' }, // 1000 wei: anything is over
  });
  const t = await tight.treasury.tick();
  assert.equal(t.sip.reason, 'gas_budget_exhausted');
  assert.equal(t.sweep.reason, 'gas_budget_exhausted');
  assert.equal(tight.rpc.sent.length, 0, 'not one transaction while the daily ETH ceiling is exhausted');
  assert.equal(tight.treasury.status().sip_consecutive_failures, 0, 'and it is a skip, not a strike');
});

test('A3b: sweeps are capped per day (they have no cooldown, so their gas needs its own bound)', async () => {
  const s = treasuryScene({
    eth: 10n ** 15n, usdc: USDC(100),
    env: { X402_TREASURY_MAX_SWEEPS_PER_DAY: '2', X402_TREASURY_USDC_CEILING: '20.00' },
  });
  assert.equal((await s.treasury.attemptSweep({ trigger: 'interval' })).action, 'swept');
  s.rpc.setUsdc(s.facilitator, USDC(100));
  assert.equal((await s.treasury.attemptSweep({ trigger: 'interval' })).action, 'swept');
  s.rpc.setUsdc(s.facilitator, USDC(100));
  const third = await s.treasury.attemptSweep({ trigger: 'interval' });
  assert.equal(third.reason, 'sweep_budget_exhausted');
  assert.equal(third.max_per_day, 2);

  // A day later the cap refills.
  s.clock.advance(86_401 * 1000);
  assert.equal((await s.treasury.attemptSweep({ trigger: 'interval' })).action, 'swept');
});

test('A4: an approve that lands and a swap that then fails leaves NO standing router allowance', async () => {
  const s = treasuryScene({
    eth: 1n, usdc: USDC(30),
    chain: { revertSwapWith: 'execution reverted: Too little received' },
  });
  const r = await s.treasury.attemptSip({ trigger: 'interval' });
  assert.equal(r.action, 'failed');

  const allowance = s.rpc.state.allowance.get(`${s.facilitator.toLowerCase()}:${C.swapRouter02.toLowerCase()}`);
  assert.equal(allowance, 0n, 'the allowance was reset in the same tick — a hot wallet leaves nothing standing');

  // The revoke is visible in the ledger and is not a strike (the sip failure
  // is the event; cleaning up after it is bookkeeping).
  const approves = s.tstore.list({ kind: 'approve', limit: 10 });
  assert.equal(approves.length, 2, 'the exact-amount approve and its revoke');
  assert.equal(approves[0].usdc_amount, '0');
  assert.equal(approves[0].status, 'confirmed');
  const revokeTx = s.rpc.sent[s.rpc.sent.length - 1];
  assert.ok(evm.addressEquals(revokeTx.tx.to, C.usdc));
  assert.ok(revokeTx.tx.data.startsWith(uniswap.SELECTORS.approve));
  assert.equal(BigInt('0x' + evm.strip0x(revokeTx.tx.data).slice(72, 136)), 0n);
});

test('A4b: an UNKNOWN swap outcome does not revoke (the swap may still land and would then STF)', async () => {
  const s = treasuryScene({ eth: 1n, usdc: USDC(30) });
  hideSipReceipt(s); // the approve confirms; the swap broadcasts and vanishes
  const r = await s.treasury.attemptSip({ trigger: 'interval' });
  assert.equal(r.action, 'unknown');

  // Exactly two transactions: the approve and the swap. No revoke — an
  // approve(0) racing a swap that may still be in a mempool would turn a
  // recoverable unknown into a guaranteed STF.
  assert.equal(s.rpc.sent.length, 2);
  const zeroApprovals = s.rpc.sent.filter((x) =>
    evm.addressEquals(x.tx.to, C.usdc)
    && x.tx.data.startsWith(uniswap.SELECTORS.approve)
    && BigInt('0x' + evm.strip0x(x.tx.data).slice(72, 136)) === 0n);
  assert.equal(zeroApprovals.length, 0, 'no allowance reset while the swap outcome is unknown');
  assert.equal(s.tstore.unresolvedCount(), 1, 'the swap is left for recover() to resolve from chain truth');
});
