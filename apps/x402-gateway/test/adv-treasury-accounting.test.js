'use strict';
/**
 * ADVERSARIAL REVIEW — money accounting and the sweep's balance oracle.
 *
 * FINDING A1 — an unknown-outcome sip is money that moved on-chain and never
 * moved in the ledger. sendTx() classifies "broadcast, no receipt inside
 * X402_RECEIPT_TIMEOUT_MS" as `unknown`, leaves the row 'submitting' and
 * returns. Nothing ever revisits that row: the settlement engine's
 * recoverInFlight() only walks the payments table, and the treasury exposes
 * no recovery entry point. So after a routine RPC hiccup:
 *   - the chain has the USDC spent and the ETH received,
 *   - `treasury status`, the treasury_actions ledger and every
 *     x402_treasury_* total say nothing happened,
 *   - the gas the transaction actually burned is never accounted at all,
 *   - and the row counts against the daily swap budget for 24 h and then
 *     silently stops counting, still unresolved.
 * The spec's acceptance criterion "swept totals + sip costs reconcile with
 * mocked balances" holds only on the happy path.
 *
 * FINDING A2 — after such a sip, tick() sweeps against a STALE balance. It
 * only re-reads balances when the sip returned 'sipped'; an 'unknown' sip
 * (whose USDC may well be gone) leaves the pre-sip number in hand, and the
 * sweep sizes `surplus = staleBalance - ceiling` from it. With the shipped
 * defaults that over-sweeps the wallet below its own operating float; with a
 * legal low ceiling it over-sweeps past the balance, the transfer reverts,
 * and two such ticks DISABLE SWEEPING — the module knocking out the very
 * compensating control that justifies single-wallet mode.
 *
 * FINDING A3 — treasury gas is invisible to the facilitator's gas circuit
 * breaker. X402_DAILY_GAS_BUDGET_WEI is computed from the payments table
 * (store.gasSpentSince); sips, approves and sweeps write their gas to
 * treasury_actions instead, so the "daily ETH ceiling" is not a ceiling and
 * the breaker cannot be reasoned about as one.
 */

const test = require('node:test');
const assert = require('node:assert/strict');

const gasMod = require('../src/facilitator-evm/gas');
const { treasuryScene, C } = require('./treasury-helpers');

const USDC = (d) => BigInt(Math.round(d * 1e6));

/**
 * Hide the receipt of the SIP transaction only (an RPC hiccup while polling
 * the swap). Everything else on the chain behaves normally.
 */
function hideSipReceipt(s) {
  const inner = s.rpc.call.bind(s.rpc);
  const hidden = new Set();
  s.rpc.call = async (method, params = []) => {
    if (method === 'eth_sendRawTransaction') {
      const hash = await inner(method, params);
      const to = s.rpc.decodeEip1559(params[0]).to;
      if (String(to).toLowerCase() === C.swapRouter02.toLowerCase()) hidden.add(hash);
      return hash;
    }
    if (method === 'eth_getTransactionReceipt' && hidden.has(params[0])) return null;
    return inner(method, params);
  };
  return hidden;
}

test('A1: an unknown-outcome sip moves real money and leaves the ledger claiming nothing happened', async () => {
  const s = treasuryScene({ eth: 1n, usdc: USDC(30) });
  s.rpc.state.allowance.set(`${s.facilitator.toLowerCase()}:${C.swapRouter02.toLowerCase()}`, 10n ** 18n);
  const before = s.balances();
  hideSipReceipt(s);

  const sip = await s.treasury.attemptSip({ trigger: 'interval' });
  assert.equal(sip.action, 'unknown');

  // The chain executed it: $5 of USDC gone, ETH received.
  const after = s.balances();
  assert.equal(before.usdc - after.usdc, USDC(5), 'the swap really spent $5 of revenue');
  assert.ok(after.eth > before.eth, 'and really delivered ETH');

  // The ledger disagrees with the chain, permanently.
  const totals = s.tstore.totals();
  assert.equal(totals.sips, 0);
  assert.equal(totals.sippedUsdcAtomic, 0n, 'ledger: no USDC sipped');
  assert.equal(totals.ethReceivedWei, 0n, 'ledger: no ETH received');
  assert.equal(totals.gasSpentWei, 0n, 'ledger: no gas spent — the tx burned gas anyway');

  const st = s.treasury.status();
  assert.equal(st.totals.sipped_usdc, '0', '`animica-x402 treasury status` reports $0 sipped');
  assert.equal(st.warning, null, 'and /readyz is clean about it');

  // The row is 'submitting' forever; nothing in the process reconciles it.
  const row = s.tstore.list({ kind: 'sip', limit: 5 })[0];
  assert.equal(row.status, 'submitting');
  assert.ok(row.tx_hash, 'the hash is stored, so a reconcile COULD exist — none does');

  // Reconciliation, as the spec words it, fails: chain delta vs ledger.
  const chainSpent = before.usdc - after.usdc;
  assert.notEqual(chainSpent, totals.sippedUsdcAtomic + totals.sweptUsdcAtomic,
    'swept + sipped does not reconcile with the balances the chain actually shows');
});

test('A2a: after an unknown sip the sweep uses a stale balance and drains below the operating float', async () => {
  // Defaults: ceiling $20. Wallet holds $24, ETH under the floor.
  const s = treasuryScene({ eth: 1n, usdc: USDC(24) });
  s.rpc.state.allowance.set(`${s.facilitator.toLowerCase()}:${C.swapRouter02.toLowerCase()}`, 10n ** 18n);
  hideSipReceipt(s);

  const r = await s.treasury.tick();
  assert.equal(r.sip.action, 'unknown');
  assert.equal(r.sweep.action, 'swept');

  // The sweep sized itself from the PRE-SIP balance ($24 - $20 = $4) even
  // though the sip had already spent $5.
  assert.equal(r.sweep.amount_usdc_atomic, USDC(4));
  assert.equal(s.balances().usdc, USDC(15), 'the wallet is left $5 BELOW the $20 ceiling it is supposed to hold');
  assert.equal(s.rpc.getUsdc(s.cold), USDC(4), 'the money did go to the cold address — sizing, not destination, is wrong');
});

test('A2b: with a legal low ceiling the same stale read reverts the sweep and DISABLES SWEEPING', async () => {
  // X402_TREASURY_USDC_CEILING=1.00 passes every startup validation (it only
  // has to be >= X402_TREASURY_SIP_MIN_USDC=0.50) and is a sane choice for an
  // operator who wants the hot wallet nearly empty.
  const s = treasuryScene({
    eth: 1n,
    usdc: USDC(10),
    env: { X402_TREASURY_USDC_CEILING: '1.00', X402_TREASURY_SIP_COOLDOWN_S: '0' },
  });
  s.rpc.state.allowance.set(`${s.facilitator.toLowerCase()}:${C.swapRouter02.toLowerCase()}`, 10n ** 18n);
  hideSipReceipt(s);

  const r1 = await s.treasury.tick();
  assert.equal(r1.sip.action, 'unknown');
  // stale surplus = $10 - $1 = $9, but only $5 is left after the sip.
  assert.equal(r1.sweep.action, 'failed');
  assert.match(String(r1.sweep.detail), /exceeds balance/);
  assert.equal(s.treasury.status().sweep_consecutive_failures, 1);

  // Fresh revenue arrives, the wallet is drained by gas again, and the same
  // receipt hiccup happens on the next tick.
  s.rpc.setUsdc(s.facilitator, USDC(10));
  s.rpc.setEth(s.facilitator, 1n);
  const r2 = await s.treasury.tick();
  assert.equal(r2.sip.action, 'unknown');
  assert.equal(r2.sweep.action, 'failed');

  const st = s.treasury.status();
  assert.equal(st.sweeping_disabled, true,
    'the module disabled its own drain because of its own stale read; revenue now piles up on the hot key');
  assert.match(s.treasury.warning(), /sweep failures/);
});

test('A3: sips, approves and sweeps burn gas that the daily gas circuit breaker cannot see', async () => {
  const s = treasuryScene({ eth: 1n, usdc: USDC(60) });
  const r = await s.treasury.tick();
  assert.equal(r.sip.action, 'sipped');
  assert.equal(r.sweep.action, 'swept');

  const treasuryGas = s.tstore.totals().gasSpentWei;
  assert.ok(treasuryGas > 0n, 'the treasury demonstrably burned ETH');

  // The breaker's basis is the payments table only.
  assert.equal(s.store.gasSpentSince(0), 0n, 'store.gasSpentSince (the breaker basis) sees none of it');

  // A budget one wei above the treasury's own spend is still reported open.
  const err = gasMod.checkDailyBudget(s.store, treasuryGas + 1n);
  assert.equal(err, null, 'X402_DAILY_GAS_BUDGET_WEI does not bound the treasury at all');
});

test('A4: an approve that lands and a swap that then fails leaves a standing router allowance', async () => {
  // The commit message claims "the approve is exact-amount so a hot wallet
  // leaves no standing allowance". Exact-amount, yes; no standing allowance,
  // only when the swap in the same tick succeeds.
  const s = treasuryScene({
    eth: 1n, usdc: USDC(30),
    chain: { revertSwapWith: 'execution reverted: Too little received' },
  });
  const r = await s.treasury.attemptSip({ trigger: 'interval' });
  assert.equal(r.action, 'failed');

  const allowance = s.rpc.state.allowance.get(`${s.facilitator.toLowerCase()}:${C.swapRouter02.toLowerCase()}`);
  assert.equal(allowance, USDC(5), 'a $5 SwapRouter02 allowance survives indefinitely on the hot wallet');
});
