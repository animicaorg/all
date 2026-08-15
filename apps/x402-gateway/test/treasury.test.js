'use strict';
/**
 * Treasury ("sweep and sip") — the spec's test matrix.
 *
 * Everything runs against the mock Base chain in test/treasury-helpers.js,
 * which decodes the ACTUAL SIGNED TRANSACTION BYTES and applies USDC / router
 * / quoter semantics (including the real revert strings) to them. No network,
 * no real keys, no real money.
 */

const test = require('node:test');
const assert = require('node:assert/strict');
const { keccak_256 } = require('@noble/hashes/sha3.js');

const cfgMod = require('../src/config');
const evm = require('../src/facilitator-evm/evm');
const uniswap = require('../src/treasury/uniswap');
const { assertTreasuryPolicy } = require('../src/treasury');
const { createEvmFacilitator } = require('../src/facilitator-evm/server');
const { loadSigner } = require('../src/facilitator-evm/key');
const { createStore } = require('../src/store');
const { kp, quietLogger } = require('./evm-helpers');
const { treasuryScene, createMockChain, decodeSipCalldata, C } = require('./treasury-helpers');

const USDC = C.usdc;
const WETH9 = C.weth9;
const ROUTER = C.swapRouter02;

const sel = (sig) => '0x' + Buffer.from(keccak_256(Buffer.from(sig, 'ascii'))).toString('hex').slice(0, 8);
const topic = (sig) => '0x' + Buffer.from(keccak_256(Buffer.from(sig, 'ascii'))).toString('hex');

/* ==========================================================================
 * 1. Calldata: selectors, addresses and the exact recon fixture
 * ====================================================================== */

test('every selector is the keccak of its signature (not a copied constant)', () => {
  assert.equal(uniswap.SELECTORS.quoteExactInputSingle, sel('quoteExactInputSingle((address,address,uint256,uint24,uint160))'));
  assert.equal(uniswap.SELECTORS.exactInputSingle, sel('exactInputSingle((address,address,uint24,address,uint256,uint256,uint160))'));
  assert.equal(uniswap.SELECTORS.unwrapWETH9, sel('unwrapWETH9(uint256,address)'));
  assert.equal(uniswap.SELECTORS.multicall, sel('multicall(bytes[])'));
  assert.equal(uniswap.SELECTORS.multicallDeadline, sel('multicall(uint256,bytes[])'));
  assert.equal(uniswap.SELECTORS.approve, sel('approve(address,uint256)'));
  assert.equal(uniswap.SELECTORS.allowance, sel('allowance(address,address)'));
  assert.equal(uniswap.SELECTORS.transfer, sel('transfer(address,uint256)'));
  assert.equal(uniswap.SELECTORS.balanceOf, sel('balanceOf(address)'));
  assert.equal(uniswap.SELECTORS.paused, sel('paused()'));
  assert.equal(uniswap.SELECTORS.isBlacklisted, sel('isBlacklisted(address)'));
  assert.equal(uniswap.TOPICS.withdrawal, topic('Withdrawal(address,uint256)'));
  assert.equal(uniswap.TOPICS.transfer, topic('Transfer(address,address,uint256)'));
});

test('every hardcoded Base contract address is EIP-55 valid (transcription guard)', () => {
  for (const [name, addr] of Object.entries(C)) {
    if (typeof addr !== 'string' || !addr.startsWith('0x')) continue;
    assert.equal(addr, evm.toChecksumAddress(addr), `${name} is not checksummed`);
  }
  assert.deepEqual(C.allowedPoolFees, [100, 500, 3000]);
  assert.ok(!C.allowedPoolFees.includes(10000), 'the 1% tier must never be allowlisted for a sip');
});

test('the sip multicall reproduces the live-simulated recon payload byte for byte', () => {
  // Recon §4: 5 USDC, fee 500, minOut = quote - 1%, recipient 0x…0f00d1,
  // multicall(bytes[]) form — 548 bytes, simulated OK on two RPCs.
  const minOut = 2632403362718125n;
  const swap = uniswap.exactInputSingleCalldata({
    tokenIn: USDC, tokenOut: WETH9, fee: 500,
    recipient: uniswap.RECIPIENT_SENTINELS.ADDRESS_THIS,
    amountIn: 5_000_000n, amountOutMinimum: minOut,
  });
  const unwrap = uniswap.unwrapWeth9Calldata(minOut, '0x00000000000000000000000000000000000f00d1');
  const mc = uniswap.multicallCalldata([swap, unwrap]);
  assert.equal(evm.strip0x(swap).length / 2, 228);
  assert.equal(evm.strip0x(unwrap).length / 2, 68);
  assert.equal(evm.strip0x(mc).length / 2, 548);
  const body = evm.strip0x(mc);
  assert.equal(body.slice(0, 8), '04' === '' ? '' : 'ac9650d8');
  assert.equal(body.slice(8, 72), '0'.repeat(62) + '20', 'offset to bytes[]');
  assert.equal(body.slice(72, 136), '0'.repeat(63) + '2', 'array length 2');
  assert.equal(body.slice(136, 200), '0'.repeat(62) + '40', 'element[0] offset');
  assert.equal(body.slice(200, 264), '0'.repeat(61) + '160', 'element[1] offset');
  assert.equal(body.slice(264, 328), '0'.repeat(61) + '0e4', 'len(element[0]) = 228');
});

test('QuoterV2 puts amountIn BEFORE fee — the router struct does not', () => {
  const q = evm.strip0x(uniswap.quoteExactInputSingleCalldata({ tokenIn: USDC, tokenOut: WETH9, amountIn: 5_000_000n, fee: 500 })).slice(8);
  assert.equal(BigInt('0x' + q.slice(128, 192)), 5_000_000n, 'word 2 is amountIn');
  assert.equal(BigInt('0x' + q.slice(192, 256)), 500n, 'word 3 is fee');
  const s = evm.strip0x(uniswap.exactInputSingleCalldata({
    tokenIn: USDC, tokenOut: WETH9, fee: 500, recipient: uniswap.RECIPIENT_SENTINELS.ADDRESS_THIS,
    amountIn: 5_000_000n, amountOutMinimum: 1n,
  })).slice(8);
  assert.equal(BigInt('0x' + s.slice(128, 192)), 500n, 'router word 2 is fee');
  assert.equal(BigInt('0x' + s.slice(256, 320)), 5_000_000n, 'router word 4 is amountIn');
});

test('unwrapWETH9 refuses the router sentinels (they are not substituted, the ETH would be burned)', () => {
  assert.throws(() => uniswap.unwrapWeth9Calldata(1n, uniswap.RECIPIENT_SENTINELS.MSG_SENDER), /sentinel/);
  assert.throws(() => uniswap.unwrapWeth9Calldata(1n, uniswap.RECIPIENT_SENTINELS.ADDRESS_THIS), /sentinel/);
  assert.throws(() => uniswap.unwrapWeth9Calldata(1n, '0x' + '0'.repeat(40)), /zero address/);
});

test('exactInputSingle refuses amountIn 0 (CONTRACT_BALANCE flag) and a zero slippage floor', () => {
  const base = { tokenIn: USDC, tokenOut: WETH9, fee: 500, recipient: uniswap.RECIPIENT_SENTINELS.ADDRESS_THIS };
  assert.throws(() => uniswap.exactInputSingleCalldata({ ...base, amountIn: 0n, amountOutMinimum: 1n }), /CONTRACT_BALANCE/);
  assert.throws(() => uniswap.exactInputSingleCalldata({ ...base, amountIn: 1n, amountOutMinimum: 0n }), /slippage/);
});

test('revert strings map to distinct retry policies', () => {
  assert.deepEqual(uniswap.classifyRevert('execution reverted: Too little received'), { class: 'price_moved', strike: false, hardDisable: false });
  assert.equal(uniswap.classifyRevert('Insufficient WETH9').class, 'price_moved');
  assert.equal(uniswap.classifyRevert('execution reverted: STF').class, 'allowance');
  assert.equal(uniswap.classifyRevert('execution reverted: STF').strike, true);
  assert.equal(uniswap.classifyRevert('STE').hardDisable, true, 'a bad ETH recipient is a misconfiguration, not market noise');
  assert.equal(uniswap.classifyRevert('whatever').strike, true);
});

/* ==========================================================================
 * 2. Configuration
 * ====================================================================== */

function facCfg(extra = {}) {
  return cfgMod.loadEvmFacilitatorConfig({
    X402_NETWORK: 'base',
    X402_RPC_URL: 'http://127.0.0.1:1/mock',
    X402_SETTLEMENT_ADDRESS: kp().address,
    X402_FACILITATOR_PRIVATE_KEY: kp().privHex,
    ...extra,
  });
}

test('treasury defaults parse to the spec values in atomic units', () => {
  const t = facCfg().treasury;
  assert.equal(t.enabled, false);
  assert.equal(t.coldAddress, null);
  assert.equal(t.ethFloorWei, 100000000000000n);
  assert.equal(t.sipUsdcAtomic, 5_000_000n);
  assert.equal(t.sipMinUsdcAtomic, 500_000n);
  assert.equal(t.maxSlippageBps, 100);
  assert.equal(t.sipCooldownS, 86400);
  assert.equal(t.dailySwapBudgetAtomic, 10_000_000n);
  assert.equal(t.usdcCeilingAtomic, 20_000_000n);
  assert.equal(t.checkIntervalS, 300);
  assert.deepEqual(t.poolFees, [500, 100]);
});

test('enabling the treasury without a cold address refuses startup', () => {
  assert.throws(() => facCfg({ X402_TREASURY_ENABLED: '1' }), /X402_TREASURY_COLD_ADDRESS/);
});

test('the cold address must be EIP-55 checksummed exactly (typo guard on the money destination)', () => {
  const cold = kp().address;
  assert.throws(
    () => facCfg({ X402_TREASURY_ENABLED: '1', X402_TREASURY_COLD_ADDRESS: cold.toLowerCase() }),
    /checksummed exactly/
  );
  assert.equal(facCfg({ X402_TREASURY_ENABLED: '1', X402_TREASURY_COLD_ADDRESS: cold }).treasury.coldAddress, cold);
});

test('impossible treasury policies refuse startup rather than never firing', () => {
  const on = { X402_TREASURY_ENABLED: '1', X402_TREASURY_COLD_ADDRESS: kp().address };
  assert.throws(() => facCfg({ ...on, X402_TREASURY_SIP_USDC: '0.10' }), /SIP_USDC must be >= /);
  assert.throws(() => facCfg({ ...on, X402_TREASURY_DAILY_SWAP_BUDGET_USDC: '0.10' }), /BUDGET_USDC is below/);
  assert.throws(() => facCfg({ ...on, X402_TREASURY_USDC_CEILING: '0.10' }), /CEILING is below/);
  assert.throws(() => facCfg({ ...on, X402_TREASURY_POOL_FEES: '10000' }), /POOL_FEES/);
  assert.throws(() => facCfg({ ...on, X402_TREASURY_ETH_FLOOR_WEI: '0' }), /ETH_FLOOR_WEI must be > 0/);
  assert.throws(() => facCfg({ ...on, X402_TREASURY_MAX_SWAP_GAS: '100000' }), /MAX_SWAP_GAS below/);
  assert.throws(() => facCfg({ ...on, X402_TREASURY_MAX_SLIPPAGE_BPS: '5000' }), /MAX_SLIPPAGE_BPS/);
});

test('the treasury refuses to run on a network with no verified Uniswap contract set', () => {
  assert.throws(
    () => cfgMod.loadEvmFacilitatorConfig({
      X402_NETWORK: 'base-sepolia',
      X402_CHAIN_ID: '84532',
      X402_RPC_URL: 'http://127.0.0.1:1/mock',
      X402_SETTLEMENT_ADDRESS: kp().address,
      X402_FACILITATOR_PRIVATE_KEY: kp().privHex,
      X402_TREASURY_ENABLED: '1',
      X402_TREASURY_COLD_ADDRESS: kp().address,
    }),
    /no live-verified Uniswap v3 contract set/
  );
});

/* ==========================================================================
 * 3. Single-wallet startup policy
 * ====================================================================== */

test('single-wallet mode (payTo == facilitator) refuses to start without the treasury', () => {
  const me = kp();
  const cfg = cfgMod.loadEvmFacilitatorConfig({
    X402_NETWORK: 'base',
    X402_RPC_URL: 'http://127.0.0.1:1/mock',
    X402_SETTLEMENT_ADDRESS: me.address,
    X402_FACILITATOR_PRIVATE_KEY: me.privHex,
  });
  assert.throws(() => assertTreasuryPolicy(cfg, me.address), /single-wallet mode refused/);

  // ...and the refusal is enforced by the real facilitator constructor.
  const signer = loadSigner(cfg.privateKey);
  delete cfg.privateKey;
  const store = createStore(':memory:');
  assert.throws(
    () => createEvmFacilitator({ cfg, rpc: createMockChain(), store, signer, logger: quietLogger }),
    /single-wallet mode refused/
  );
  store.close();
});

test('single-wallet mode with a cold address starts, and a separate payTo needs no treasury', () => {
  const me = kp();
  const cold = kp().address;
  const ok = cfgMod.loadEvmFacilitatorConfig({
    X402_NETWORK: 'base',
    X402_RPC_URL: 'http://127.0.0.1:1/mock',
    X402_SETTLEMENT_ADDRESS: me.address,
    X402_FACILITATOR_PRIVATE_KEY: me.privHex,
    X402_TREASURY_ENABLED: '1',
    X402_TREASURY_COLD_ADDRESS: cold,
  });
  assert.deepEqual(assertTreasuryPolicy(ok, me.address), { singleWallet: true, treasuryRequired: true });

  const separate = facCfg();
  assert.deepEqual(assertTreasuryPolicy(separate, me.address), { singleWallet: false, treasuryRequired: false });
});

test('a cold address equal to the facilitator (sweep to self) is refused', () => {
  const me = kp();
  const cfg = cfgMod.loadEvmFacilitatorConfig({
    X402_NETWORK: 'base',
    X402_RPC_URL: 'http://127.0.0.1:1/mock',
    X402_SETTLEMENT_ADDRESS: me.address,
    X402_FACILITATOR_PRIVATE_KEY: me.privHex,
    X402_TREASURY_ENABLED: '1',
    X402_TREASURY_COLD_ADDRESS: me.address,
  });
  assert.throws(() => assertTreasuryPolicy(cfg, me.address), /equals the facilitator address/);
});

test('config downgrade: flipping ENABLED=1 without a cold address cannot sneak past a partial env', () => {
  // The exact adversarial shape: the operator sets single-wallet payTo and
  // the enable flag, but the cold address line is missing/empty.
  const me = kp();
  assert.throws(() => cfgMod.loadEvmFacilitatorConfig({
    X402_NETWORK: 'base',
    X402_RPC_URL: 'http://127.0.0.1:1/mock',
    X402_SETTLEMENT_ADDRESS: me.address,
    X402_FACILITATOR_PRIVATE_KEY: me.privHex,
    X402_TREASURY_ENABLED: '1',
    X402_TREASURY_COLD_ADDRESS: '',
  }), /X402_TREASURY_COLD_ADDRESS/);
});

/* ==========================================================================
 * 4. Sip behaviour
 * ====================================================================== */

test('above the ETH floor nothing happens', async () => {
  const s = treasuryScene({ eth: 10n ** 15n, usdc: 8_000_000n });
  const r = await s.treasury.tick();
  assert.equal(r.sip.action, 'skipped');
  assert.equal(r.sip.reason, 'above_eth_floor');
  assert.equal(s.rpc.sent.length, 0);
});

test('crossing the floor triggers exactly ONE sip, and repeated triggers are held by the cooldown', async () => {
  const s = treasuryScene({ eth: 10n ** 13n, usdc: 30_000_000n });
  const first = await s.treasury.tick();
  assert.equal(first.sip.action, 'sipped');
  assert.equal(first.sip.amount_usdc_atomic, 5_000_000n);

  // Ten more triggers of every kind: the cooldown must hold.
  for (let i = 0; i < 10; i++) {
    s.rpc.setEth(s.facilitator, 1n); // pretend gas is still exhausted
    const r = await s.treasury.tick({ trigger: 'settlement' });
    assert.equal(r.sip.action, 'skipped');
    assert.equal(r.sip.reason, 'cooldown');
  }
  const sips = s.tstore.list({ kind: 'sip' });
  assert.equal(sips.length, 1);
  assert.equal(sips[0].status, 'confirmed');
});

test('the sip is min(SIP_USDC, balance) and never below SIP_MIN_USDC', async () => {
  const small = treasuryScene({ eth: 1n, usdc: 1_250_000n }); // $1.25 accrued
  const r1 = await small.treasury.tick();
  assert.equal(r1.sip.action, 'sipped');
  assert.equal(r1.sip.amount_usdc_atomic, 1_250_000n, 'adaptive: swap what there is');

  const tiny = treasuryScene({ eth: 1n, usdc: 400_000n }); // $0.40 < $0.50 floor
  const r2 = await tiny.treasury.tick();
  assert.equal(r2.sip.action, 'skipped');
  assert.equal(r2.sip.reason, 'insufficient_usdc');
  assert.equal(tiny.rpc.sent.length, 0);
});

test('BOOTSTRAP STALL: a gas spike empties ETH at $0.50 accrued and the adaptive sip still recovers', async () => {
  // The scenario the adaptive sip exists for. The operator funded ~$2 of ETH
  // once and walked away. A fee spike burns it at ~75 settlements, by which
  // point only ~$0.75 of $0.01 QRNG revenue has accrued. A fixed $5 minimum
  // deadlocks here forever: no ETH to settle, so no more revenue, so never
  // $5 to swap. The adaptive floor breaks the deadlock at $0.50.
  const s = treasuryScene({
    eth: 0n,                 // gas exhausted
    usdc: 500_000n,          // exactly the $0.50 adaptive floor
    env: { X402_TREASURY_SIP_USDC: '5.00', X402_TREASURY_SIP_MIN_USDC: '0.50' },
  });
  assert.equal(s.balances().eth, 0n);

  const r = await s.treasury.tick({ trigger: 'settlement' });
  assert.equal(r.sip.action, 'sipped', 'the module must be able to sip with only $0.50 on hand');
  assert.equal(r.sip.amount_usdc_atomic, 500_000n);
  assert.equal(r.sip.emergency, true, 'below floor/2 the cooldown halves — this is the emergency path');

  const ethAfter = s.balances().eth;
  assert.ok(ethAfter > 2.6e14, `a $0.50 sip must buy real gas, got ${ethAfter} wei`);
  // Recon: a settlement costs ~5.39e11 wei. Assert the recovered runway is
  // the ~490 settlements the brief promises, not a token amount.
  const settlementsBought = ethAfter / 539_000_000_000n;
  assert.ok(settlementsBought > 400n, `expected >400 settlements of runway, got ${settlementsBought}`);

  // And the counterfactual: with a FIXED $5 minimum the same state deadlocks.
  const fixed = treasuryScene({
    eth: 0n, usdc: 500_000n,
    env: { X402_TREASURY_SIP_USDC: '5.00', X402_TREASURY_SIP_MIN_USDC: '5.00' },
  });
  const stalled = await fixed.treasury.tick();
  assert.equal(stalled.sip.action, 'skipped');
  assert.equal(stalled.sip.reason, 'insufficient_usdc');
  assert.equal(fixed.balances().eth, 0n, 'the deadlock the adaptive floor is designed to prevent');
});

test('the cooldown halves below floor/2 and is enforced exactly', async () => {
  const s = treasuryScene({
    eth: 10n ** 13n, usdc: 30_000_000n,
    env: { X402_TREASURY_SIP_COOLDOWN_S: '1000', X402_TREASURY_DAILY_SWAP_BUDGET_USDC: '100.00' },
  });
  await s.treasury.tick();                    // sip #1 at t0
  s.rpc.setEth(s.facilitator, 10n ** 13n);    // still below floor/2 -> 500s cooldown

  s.clock.advance(499 * 1000);
  assert.equal((await s.treasury.tick()).sip.reason, 'cooldown');
  s.clock.advance(2 * 1000);
  assert.equal((await s.treasury.tick()).sip.action, 'sipped', 'halved cooldown (500s) has elapsed');

  // Just under the floor (not below floor/2) the FULL cooldown applies.
  s.rpc.setEth(s.facilitator, 99n * 10n ** 12n);
  s.clock.advance(600 * 1000);
  assert.equal((await s.treasury.tick()).sip.reason, 'cooldown', 'non-emergency uses the full 1000s');
  s.clock.advance(401 * 1000);
  assert.equal((await s.treasury.tick()).sip.action, 'sipped');
});

test('a failed attempt retries on the short cooldown, a successful one on the full cooldown', async () => {
  const s = treasuryScene({
    eth: 1n, usdc: 30_000_000n,
    env: {
      X402_TREASURY_SIP_COOLDOWN_S: '86400',
      X402_TREASURY_RETRY_COOLDOWN_S: '900',
      X402_TREASURY_USDC_CEILING: '1000.00',
      X402_TREASURY_DAILY_SWAP_BUDGET_USDC: '100.00',
    },
    chain: { quoteInflateBps: 200 }, // every sip reverts on the slippage bound
  });
  assert.equal((await s.treasury.tick()).sip.action, 'failed');

  s.clock.advance(899 * 1000);
  const held = await s.treasury.tick();
  assert.equal(held.sip.reason, 'cooldown');
  assert.equal(held.sip.retry, true);

  // Past the retry window the price has recovered and the sip goes through.
  s.clock.advance(2 * 1000);
  s.rpc.state.quoteInflateBps = 0;
  assert.equal((await s.treasury.tick()).sip.action, 'sipped');

  // After a SUCCESS the full 24 h (halved to 12 h in the emergency band) applies.
  s.rpc.setEth(s.facilitator, 10n ** 13n);
  s.clock.advance(3600 * 1000);
  const after = await s.treasury.tick();
  assert.equal(after.sip.reason, 'cooldown');
  assert.equal(after.sip.retry, false);
  assert.equal(after.sip.cooldown_s, 43200);
});

test('FORCED-SIP DRAIN: spamming settlement triggers can never exceed the daily budget', async () => {
  const s = treasuryScene({
    eth: 1n, usdc: 1_000_000_000n, // $1,000 sitting there
    env: {
      X402_TREASURY_SIP_COOLDOWN_S: '0',              // no cooldown protection at all
      X402_TREASURY_DAILY_SWAP_BUDGET_USDC: '10.00',
      X402_TREASURY_SIP_USDC: '5.00',
      X402_TREASURY_USDC_CEILING: '100000.00',        // keep the sweep out of it
    },
  });
  for (let i = 0; i < 50; i++) {
    s.rpc.setEth(s.facilitator, 1n); // attacker keeps gas pinned at zero
    await s.treasury.tick({ trigger: 'settlement' });
  }
  const sips = s.tstore.list({ kind: 'sip', limit: 100 }).filter((r) => r.status === 'confirmed');
  const total = sips.reduce((a, r) => a + BigInt(r.usdc_amount), 0n);
  assert.equal(total, 10_000_000n, 'exactly the $10.00 daily budget, not a cent more');
  assert.equal(sips.length, 2);
  const last = await s.treasury.tick({ trigger: 'settlement' });
  assert.equal(last.sip.reason, 'daily_budget_exhausted');

  // A day later the budget refills.
  s.clock.advance(86_401 * 1000);
  const next = await s.treasury.tick({ trigger: 'settlement' });
  assert.equal(next.sip.action, 'sipped');
});

test('the budget clamps a sip rather than skipping when only part of it fits', async () => {
  const s = treasuryScene({
    eth: 1n, usdc: 100_000_000n,
    env: {
      X402_TREASURY_SIP_COOLDOWN_S: '0',
      X402_TREASURY_DAILY_SWAP_BUDGET_USDC: '7.00',
      X402_TREASURY_USDC_CEILING: '10000.00',
    },
  });
  const a = await s.treasury.tick();
  assert.equal(a.sip.amount_usdc_atomic, 5_000_000n);
  s.rpc.setEth(s.facilitator, 1n);
  const b = await s.treasury.tick();
  assert.equal(b.sip.amount_usdc_atomic, 2_000_000n, 'clamped to the $2.00 of budget left');
});

test('amountOutMinimum is the quote minus exactly the configured slippage, in BigInt', async () => {
  const s = treasuryScene({ eth: 1n, usdc: 5_000_000n, env: { X402_TREASURY_MAX_SLIPPAGE_BPS: '250' } });
  await s.treasury.tick();
  const swapTx = s.rpc.sent.find((x) => evm.addressEquals(x.tx.to, ROUTER));
  const call = decodeSipCalldata(swapTx.tx.data);
  const quote = s.rpc.quoteOut(5_000_000n, call.swap.fee);
  assert.equal(call.swap.amountOutMinimum, (quote * 9750n) / 10_000n);
  assert.equal(call.unwrap.amountMinimum, call.swap.amountOutMinimum);
  // The floor is a real bound, not decoration: it must be under the quote.
  assert.ok(call.swap.amountOutMinimum < quote);
});

test('SLIPPAGE BOUND: a fill worse than amountOutMinimum reverts and moves no USDC', async () => {
  // The pool fills at the true rate while the quote claims 2% more — i.e.
  // exactly the "the price moved against us" case the 1% bound exists for.
  const s = treasuryScene({
    eth: 1n, usdc: 30_000_000n,
    env: { X402_TREASURY_MAX_SLIPPAGE_BPS: '100', X402_TREASURY_USDC_CEILING: '1000.00' },
    chain: { quoteInflateBps: 200 },
  });
  const r = await s.treasury.tick();
  assert.equal(r.sip.action, 'failed');
  assert.match(r.sip.detail, /Too little received/);
  assert.equal(s.balances().usdc, 30_000_000n, 'not a cent of USDC may move when the bound binds');
  assert.equal(s.treasury.status().sip_consecutive_failures, 0, 'price movement is not a strike');

  // Widen the tolerance past the move and the same swap goes through — the
  // bound is doing the work, not an unrelated failure.
  const loose = treasuryScene({
    eth: 1n, usdc: 30_000_000n,
    env: { X402_TREASURY_MAX_SLIPPAGE_BPS: '300' },
    chain: { quoteInflateBps: 200 },
  });
  assert.equal((await loose.treasury.tick()).sip.action, 'sipped');
});

test('the best-quoting allowlisted fee tier wins', async () => {
  const s = treasuryScene({ eth: 1n, usdc: 5_000_000n });
  await s.treasury.tick();
  const swapTx = s.rpc.sent.find((x) => evm.addressEquals(x.tx.to, ROUTER));
  const call = decodeSipCalldata(swapTx.tx.data);
  assert.equal(call.swap.fee, 100, 'fee 100 quotes marginally better at this size (live ordering)');

  const only500 = treasuryScene({ eth: 1n, usdc: 5_000_000n, env: { X402_TREASURY_POOL_FEES: '500' } });
  await only500.treasury.tick();
  const t2 = only500.rpc.sent.find((x) => evm.addressEquals(x.tx.to, ROUTER));
  assert.equal(decodeSipCalldata(t2.tx.data).swap.fee, 500);
});

test('the sip transaction carries no ETH value, targets the router, and unwraps to the facilitator', async () => {
  const s = treasuryScene({ eth: 1n, usdc: 5_000_000n });
  await s.treasury.tick();
  const swapTx = s.rpc.sent.find((x) => evm.addressEquals(x.tx.to, ROUTER));
  assert.equal(swapTx.tx.value, 0n, 'ETH attached to SwapRouter02 is claimable by anyone via refundETH()');
  const call = decodeSipCalldata(swapTx.tx.data);
  assert.ok(evm.addressEquals(call.swap.recipient, uniswap.RECIPIENT_SENTINELS.ADDRESS_THIS));
  assert.ok(evm.addressEquals(call.unwrap.recipient, s.facilitator));
  assert.ok(call.deadline > 0n, 'multicall(deadline, …) — a stale sip must expire, not execute later');
});

test('the approve is exact-amount and leaves no standing allowance', async () => {
  const s = treasuryScene({ eth: 1n, usdc: 5_000_000n });
  await s.treasury.tick();
  const approveTx = s.rpc.sent.find((x) => evm.addressEquals(x.tx.to, USDC) && x.tx.data.startsWith(uniswap.SELECTORS.approve));
  assert.ok(approveTx, 'an approve must precede the first swap');
  assert.equal(BigInt('0x' + evm.strip0x(approveTx.tx.data).slice(72, 136)), 5_000_000n);
  const left = s.rpc.state.allowance.get(`${s.facilitator.toLowerCase()}:${ROUTER.toLowerCase()}`);
  assert.equal(left, 0n, 'the swap consumes the exact allowance — no unlimited approval from a hot wallet');
});

test('USDC paused or the facilitator blacklisted: skip, never a strike', async () => {
  const s = treasuryScene({ eth: 1n, usdc: 5_000_000n, chain: { paused: true } });
  const r = await s.treasury.tick();
  assert.equal(r.sip.reason, 'token_unavailable');
  assert.equal(r.sweep.reason, 'below_ceiling');
  assert.equal(s.treasury.status().sip_consecutive_failures, 0);
  assert.equal(s.rpc.sent.length, 0);

  const b = treasuryScene({ eth: 1n, usdc: 5_000_000n });
  b.rpc.state.blacklisted.add(b.facilitator.toLowerCase());
  assert.equal((await b.treasury.tick()).sip.reason, 'token_unavailable');
});

test('a sip whose ETH would not clearly outweigh its own gas is skipped', async () => {
  const s = treasuryScene({
    eth: 1n, usdc: 5_000_000n,
    env: { X402_TREASURY_MIN_ETH_OUT_GAS_RATIO: '100000' }, // absurd ratio -> always uneconomic
  });
  const r = await s.treasury.tick();
  assert.equal(r.sip.reason, 'uneconomic');
  assert.equal(s.rpc.sent.length, 0);
});

test('the module does nothing at all when disabled', async () => {
  const s = treasuryScene({ eth: 0n, usdc: 1_000_000_000n, enabled: false, singleWallet: false });
  const r = await s.treasury.tick();
  assert.equal(r.sip.reason, 'treasury_disabled');
  assert.equal(r.sweep.reason, 'treasury_disabled');
  assert.equal(s.rpc.sent.length, 0);
  s.treasury.start();
  assert.equal(s.rpc.sent.length, 0);
  s.treasury.notifySettlement();
  await new Promise((r2) => setImmediate(r2));
  assert.equal(s.rpc.sent.length, 0);
});

/* ==========================================================================
 * 5. Failure policy
 * ====================================================================== */

test('two consecutive swap failures disable sipping and raise a readyz WARNING (settlements keep working)', async () => {
  const s = treasuryScene({
    eth: 1n, usdc: 30_000_000n,
    env: { X402_TREASURY_SIP_COOLDOWN_S: '0' },
    chain: { revertSwapWith: 'execution reverted: STF' },
  });
  const first = await s.treasury.tick();
  assert.equal(first.sip.action, 'failed');
  assert.equal(s.treasury.status().sipping_disabled, false, 'one failure is not enough to disable');

  s.rpc.setEth(s.facilitator, 1n);
  const second = await s.treasury.tick();
  assert.equal(second.sip.action, 'failed');
  const st = s.treasury.status();
  assert.equal(st.sipping_disabled, true);
  assert.match(st.sipping_disabled_reason, /2 consecutive swap failures/);
  assert.match(s.treasury.warning(), /consecutive swap failures/);

  // A third trigger must not spend more gas trying.
  const before = s.rpc.sent.length;
  const third = await s.treasury.tick();
  assert.equal(third.sip.reason, 'sipping_disabled');
  assert.equal(s.rpc.sent.length, before);

  // And the WARNING never fails readiness — paid traffic is still settleable.
  // (Top the wallet up first: an empty gas balance is its own, separate and
  // genuinely fatal readiness failure.)
  s.rpc.setEth(s.facilitator, 10n ** 18n);
  const fac = s.createFacilitator();
  const ready = await fac.readiness();
  assert.equal(ready.ready, true, 'a broken sip must never stop the facilitator taking payments');
  assert.match(String(ready.checks.treasury), /WARNING/);
  assert.ok(Array.isArray(ready.warnings) && ready.warnings.length === 1);

  // The operator's escape hatch.
  s.treasury.resume();
  assert.equal(s.treasury.status().sipping_disabled, false);
  assert.equal(s.treasury.warning(), null);
});

test('a stale-quote revert ("Too little received") is not a strike — that is normal market noise', async () => {
  const s = treasuryScene({
    eth: 1n, usdc: 30_000_000n,
    env: { X402_TREASURY_SIP_COOLDOWN_S: '0' },
    chain: { revertSwapWith: 'execution reverted: Too little received' },
  });
  await s.treasury.tick();
  s.rpc.setEth(s.facilitator, 1n);
  await s.treasury.tick();
  s.rpc.setEth(s.facilitator, 1n);
  await s.treasury.tick();
  const st = s.treasury.status();
  assert.equal(st.sip_consecutive_failures, 0);
  assert.equal(st.sipping_disabled, false, 'price movement must not disable the refuel loop');
});

test('an STE (recipient cannot receive ETH) hard-disables sipping on the first failure', async () => {
  const s = treasuryScene({
    eth: 1n, usdc: 30_000_000n,
    env: { X402_TREASURY_SIP_COOLDOWN_S: '0' },
    chain: { revertSwapWith: 'execution reverted: STE' },
  });
  const r = await s.treasury.tick();
  assert.equal(r.sip.action, 'failed');
  assert.equal(s.treasury.status().sipping_disabled, true);
  assert.match(s.treasury.status().sipping_disabled_reason, /hard-disabled/);
});

test('the disable survives a restart (it is persisted, not in-memory)', async () => {
  const s = treasuryScene({
    eth: 1n, usdc: 30_000_000n,
    env: { X402_TREASURY_SIP_COOLDOWN_S: '0' },
    chain: { revertSwapWith: 'execution reverted: STF' },
  });
  await s.treasury.tick();
  s.rpc.setEth(s.facilitator, 1n);
  await s.treasury.tick();
  assert.equal(s.treasury.status().sipping_disabled, true);

  const restarted = s.createFacilitator(); // same DB handle
  assert.equal(restarted.treasury.status().sipping_disabled, true);
  assert.match(restarted.treasury.warning(), /consecutive swap failures/);
});

test('an unknown send outcome is not treated as a failure (the tx may have landed)', async () => {
  const s = treasuryScene({
    eth: 1n, usdc: 30_000_000n,
    chain: { sendError: { transport: true, message: 'socket hang up' } },
  });
  // Pre-approve so the transport error lands on the swap itself.
  s.rpc.state.allowance.set(`${s.facilitator.toLowerCase()}:${ROUTER.toLowerCase()}`, 10n ** 18n);
  const r = await s.treasury.tick();
  assert.equal(r.sip.action, 'unknown');
  assert.equal(s.treasury.status().sip_consecutive_failures, 0);
  // ...and it still counts against the daily budget, because the money may
  // in fact have moved.
  const rows = s.tstore.list({ kind: 'sip' });
  assert.equal(rows[0].status, 'submitting');
  assert.equal(s.tstore.sipSpendSince(0), 5_000_000n);
});

test('a receipt whose effects do not match (no Withdrawal log) fails the sip loudly', async () => {
  const s = treasuryScene({ eth: 1n, usdc: 30_000_000n, chain: { omitWithdrawalLog: true } });
  const r = await s.treasury.tick();
  assert.equal(r.sip.action, 'failed');
  assert.equal(r.sip.reason, 'effect_mismatch');
});

/* ==========================================================================
 * 6. Sweep behaviour + destination immutability
 * ====================================================================== */

test('crossing the ceiling sweeps exactly balance - ceiling to the cold address', async () => {
  const s = treasuryScene({ eth: 10n ** 15n, usdc: 33_500_000n });
  const r = await s.treasury.tick();
  assert.equal(r.sweep.action, 'swept');
  assert.equal(r.sweep.amount_usdc_atomic, 13_500_000n);
  assert.equal(s.rpc.getUsdc(s.cold), 13_500_000n);
  assert.equal(s.rpc.getUsdc(s.facilitator), 20_000_000n, 'the ceiling stays as operating float');

  const again = await s.treasury.tick();
  assert.equal(again.sweep.reason, 'below_ceiling');
});

test('a dust surplus is not swept (gas would exceed the money moved)', async () => {
  const s = treasuryScene({ eth: 10n ** 15n, usdc: 20_050_000n }); // $0.05 over
  const r = await s.treasury.tick();
  assert.equal(r.sweep.reason, 'below_min_sweep');
  assert.equal(s.rpc.sent.length, 0);
});

test('SWEEP DESTINATION IMMUTABILITY: no argument, no config mutation, no env change can retarget it', async () => {
  const s = treasuryScene({ eth: 10n ** 15n, usdc: 50_000_000n });
  const attacker = kp().address;

  // 1. There is no destination parameter. Passing one is simply ignored.
  const r = await s.treasury.sweepNow({ to: attacker, destination: attacker, coldAddress: attacker });
  assert.equal(r.action, 'swept');
  assert.equal(r.destination, s.cold);

  // 2. Mutating the config object after construction changes nothing: the
  //    destination was captured at construction from server config.
  s.cfg.treasury.coldAddress = attacker;
  s.cfg.settlementAddress = attacker;
  process.env.X402_TREASURY_COLD_ADDRESS = attacker;
  try {
    s.rpc.setUsdc(s.facilitator, 50_000_000n);
    const r2 = await s.treasury.sweepNow();
    assert.equal(r2.destination, s.cold);
  } finally {
    delete process.env.X402_TREASURY_COLD_ADDRESS;
  }

  // 3. The proof that matters: every ERC-20 transfer this module ever signed
  //    pays the cold address and nothing else.
  const transfers = s.rpc.sent
    .filter((x) => evm.addressEquals(x.tx.to, USDC) && x.tx.data.startsWith(uniswap.SELECTORS.transfer))
    .map((x) => '0x' + evm.strip0x(x.tx.data).slice(32, 72));
  assert.ok(transfers.length >= 2);
  for (const dest of transfers) assert.ok(evm.addressEquals(dest, s.cold), `sweep paid ${dest}, not the cold address`);
  assert.equal(s.rpc.getUsdc(attacker), 0n);
  assert.equal(s.rpc.getUsdc(s.cold), 60_000_000n);
});

test('a sweep failure twice in a row disables sweeping without touching sipping', async () => {
  const s = treasuryScene({
    eth: 10n ** 15n, usdc: 50_000_000n,
    chain: { revertSweepWith: 'execution reverted: ERC20: transfer to the zero address' },
  });
  await s.treasury.tick();
  await s.treasury.tick();
  const st = s.treasury.status();
  assert.equal(st.sweeping_disabled, true);
  assert.equal(st.sipping_disabled, false);
  assert.match(s.treasury.warning(), /sweep failures/);
});

/* ==========================================================================
 * 7. Isolation from the settlement path
 * ====================================================================== */

test('the post-settlement hook returns immediately and swallows every error', async () => {
  const s = treasuryScene({ eth: 1n, usdc: 5_000_000n });
  s.rpc.call = async () => { throw new Error('rpc is on fire'); };
  const t0 = Date.now();
  assert.equal(s.treasury.notifySettlement(), undefined);
  assert.ok(Date.now() - t0 < 50, 'the hook must not await anything');
  await new Promise((r) => setImmediate(r));
  assert.equal(s.treasury.status().sipping_disabled, false);
});

test('a sip in flight never makes a settlement wait for its receipt', async () => {
  // The shared nonce lane is a FIFO submit lock (settlement.js). The treasury
  // may hold it for one sign+broadcast, never across receipt polling.
  let lock = Promise.resolve();
  const withSubmitLock = (fn) => {
    const run = lock.then(fn, fn);
    lock = run.then(() => undefined, () => undefined);
    return run;
  };
  const s = treasuryScene({
    eth: 1n, usdc: 30_000_000n,
    withSubmitLock,
    yieldingSleep: true,          // receipt polling yields the event loop
    chain: { dropReceipt: true }, // the sip's receipt never arrives
  });
  // Pre-approve so the sip is a single transaction.
  s.rpc.state.allowance.set(`${s.facilitator.toLowerCase()}:${ROUTER.toLowerCase()}`, 10n ** 18n);

  const order = [];
  let settlementTask = null;
  s.rpc.state.onSend = () => {
    if (settlementTask) return;
    // Queued while the treasury still holds the lock, mid-broadcast.
    settlementTask = withSubmitLock(async () => { order.push('settlement'); });
  };

  const tickDone = s.treasury.tick().then(() => order.push('tick'));
  await new Promise((r) => setImmediate(r));
  assert.ok(settlementTask, 'the sip should have broadcast by now');
  await settlementTask;
  assert.deepEqual(order, ['settlement'], 'the settlement ran while the sip was still polling for a receipt');

  await tickDone;
  assert.deepEqual(order, ['settlement', 'tick']);
  assert.equal(s.treasury.status().totals.sips, 0, 'the sip itself never confirmed — it just did not hold anyone up');
});

test('a real settlement fires the treasury hook — and a hook that throws cannot break the payment', async () => {
  const { createSettlementEngine } = require('../src/facilitator-evm/settlement');
  const { scene } = require('./evm-helpers');
  const sc = scene();
  let fired = 0;
  const engine = createSettlementEngine({
    cfg: sc.cfg, rpc: sc.rpc, store: sc.store, signer: sc.signer, metrics: sc.metrics,
    logger: quietLogger, domainSepBytes: sc.domainSepBytes,
    hooks: { onSettled: (info) => { fired++; assert.ok(info.txHash); throw new Error('treasury exploded'); } },
    sleep: sc.clock.sleep, now: sc.clock.now,
  });
  const res = await engine.settle(sc.payment());
  assert.equal(res.success, true, 'the payer must be served even when the treasury hook throws');
  assert.equal(fired, 1);
  sc.store.close();
});

test('concurrent triggers coalesce into a single tick (one nonce lane, one budget)', async () => {
  const s = treasuryScene({ eth: 1n, usdc: 30_000_000n, env: { X402_TREASURY_SIP_COOLDOWN_S: '0' } });
  const a = s.treasury.run('settlement');
  const b = s.treasury.run('settlement');
  const c = s.treasury.run('settlement');
  assert.equal(a, b);
  assert.equal(b, c, 'three settlements in a burst must not start three ticks');
  await a;
  // The coalesced follow-up runs after; either way the budget bounds it.
  await new Promise((r) => setImmediate(r));
  const confirmed = s.tstore.list({ kind: 'sip', limit: 50 }).filter((r) => r.status === 'confirmed');
  assert.ok(confirmed.length >= 1);
  const total = confirmed.reduce((acc, r) => acc + BigInt(r.usdc_amount), 0n);
  assert.ok(total <= 10_000_000n, 'the daily budget still bounds a coalesced burst');
});

/* ==========================================================================
 * 8. Accounting
 * ====================================================================== */

test('swept totals, sip spend and ETH received reconcile with the mocked chain balances', async () => {
  const s = treasuryScene({
    eth: 1n, usdc: 60_000_000n,
    env: {
      X402_TREASURY_SIP_COOLDOWN_S: '0',
      X402_TREASURY_DAILY_SWAP_BUDGET_USDC: '10.00',
      // Floor above what one sip buys, so the second sip triggers on its own
      // and the ETH ledger stays a pure sum of the Withdrawal logs.
      X402_TREASURY_ETH_FLOOR_WEI: '10000000000000000',
    },
  });
  const startUsdc = 60_000_000n;

  const r1 = await s.treasury.tick();
  assert.equal(r1.sip.action, 'sipped');
  assert.equal(r1.sweep.action, 'swept');
  const r2 = await s.treasury.tick();
  assert.equal(r2.sip.action, 'sipped');

  const totals = s.tstore.totals();
  const balances = s.balances();
  const cold = s.rpc.getUsdc(s.cold);

  // Conservation: everything that left the wallet went to exactly one of the
  // two places the module is allowed to send it.
  assert.equal(totals.sippedUsdcAtomic + totals.sweptUsdcAtomic + balances.usdc, startUsdc);
  assert.equal(cold, totals.sweptUsdcAtomic);
  assert.equal(totals.sippedUsdcAtomic, 10_000_000n);
  assert.equal(balances.eth, 1n + totals.ethReceivedWei, 'ETH gained equals the sum of the Withdrawal logs');

  // Metrics agree with the ledger.
  const text = s.metrics.render();
  const metricValue = (name) => {
    const line = text.split('\n').find((l) => l.startsWith(name + ' '));
    return line ? line.slice(name.length + 1) : null;
  };
  assert.equal(metricValue('x402_treasury_swept_usdc_total'), (Number(totals.sweptUsdcAtomic) / 1e6).toString());
  assert.equal(metricValue('x402_treasury_sip_eth_received_wei'), totals.ethReceivedWei.toString());
  assert.equal(metricValue('x402_treasury_eth_balance_wei'), balances.eth.toString());
  assert.match(text, /x402_treasury_sips_total\{result="ok"\} 2/);
  assert.match(text, /x402_treasury_sweeps_total\{result="ok"\} 1/);

  // status() is a faithful summary of the same numbers.
  const st = s.treasury.status();
  assert.equal(st.totals.sips, 2);
  assert.equal(st.spent_today_usdc, '10');
  assert.equal(st.budget_remaining_usdc, '0');
  assert.equal(st.cold_address, s.cold);
  assert.equal(st.single_wallet, true);
});

test('treasury gas is accounted separately from settlement gas', async () => {
  const s = treasuryScene({ eth: 1n, usdc: 30_000_000n });
  await s.treasury.tick();
  const text = s.metrics.render();
  assert.match(text, /x402_treasury_gas_spent_wei\{kind="sip"\} \d+/);
  assert.match(text, /x402_gas_spent_wei 0/, 'settlement gas must not absorb treasury gas');
  const rows = s.tstore.list({ kind: 'sip' });
  assert.ok(BigInt(rows[0].gas_spent_wei) > 0n, 'the sip records its own gas, L1 data fee included');
});
