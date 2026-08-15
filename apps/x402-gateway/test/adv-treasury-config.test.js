'use strict';
/**
 * ADVERSARIAL REVIEW — config downgrade, sweep redirection, and the
 * refuel/readiness overlap.
 *
 * FINDING C1 — the cold-address "typo guard" is an EIP-55 checksum comparison,
 * and the addresses most likely to appear by accident have no checksum to
 * fail: `0x0000…0000` (an unset/templated variable, a truncated paste) and the
 * precompiles `0x0000…0001`-`0x…0009` are all-digit, so `toChecksumAddress`
 * returns them unchanged and loadTreasuryConfig accepts them. createTreasury
 * only rejects a cold address equal to the facilitator, USDC, WETH9, the
 * router or the quoter — the zero address passes every gate. Real USDC
 * (FiatTokenV2) reverts on `transfer(0x0, …)`, so the deployment starts,
 * reports ready, takes payments, and two ticks later disables its own sweep;
 * every dollar then accumulates on the hot key, which is the exact risk
 * single-wallet mode was allowed to take only because the sweep exists.
 *
 * FINDING C2 — `sweepNow()`/`attemptSip()` accept a caller-supplied `balances`
 * object and use it as the balance oracle without any cross-check against the
 * chain. The destination is genuinely immutable (re-verified below against the
 * signed bytes), but the AMOUNT is not: a wrong/forged balance produces a
 * transfer for money the wallet does not hold, which reverts and walks the
 * two-strike breaker.
 *
 * FINDING C3 — X402_TREASURY_ETH_FLOOR_WEI and X402_MIN_GAS_BALANCE_WEI ship
 * with the same default (1e14 wei). The treasury may therefore only ever act
 * while the facilitator's own /readyz is already failing on gas_balance.
 *
 * Everything else here is a NEGATIVE control: the config-downgrade attempts
 * that DO fail closed, recorded so a future change that loosens them fails.
 */

const test = require('node:test');
const assert = require('node:assert/strict');

const cfgMod = require('../src/config');
const evm = require('../src/facilitator-evm/evm');
const uniswap = require('../src/treasury/uniswap');
const { assertTreasuryPolicy } = require('../src/treasury');
const { treasuryScene, C, decodeEip1559 } = require('./treasury-helpers');
const { kp } = require('./evm-helpers');

const USDC = (d) => BigInt(Math.round(d * 1e6));
const ZERO = '0x0000000000000000000000000000000000000000';

function envFor(overrides = {}) {
  const k = kp();
  return {
    key: k,
    source: {
      X402_NETWORK: 'base',
      X402_RPC_URL: 'http://127.0.0.1:1/mock',
      X402_SETTLEMENT_ADDRESS: k.address,     // single-wallet mode
      X402_FACILITATOR_PRIVATE_KEY: k.privHex,
      X402_TREASURY_ENABLED: '1',
      X402_TREASURY_COLD_ADDRESS: kp().address,
      ...overrides,
    },
  };
}

/* ---------------------------------------------------- C1: zero address ---- */

test('C1a: the zero address is an ACCEPTED cold address — checksum, policy gate and construction all pass', () => {
  const { key, source } = envFor({ X402_TREASURY_COLD_ADDRESS: ZERO });

  // 1. It survives the checksum "typo guard" (there is no case to get wrong).
  assert.equal(evm.validateAddress(ZERO, 'x'), ZERO);
  const cfg = cfgMod.loadEvmFacilitatorConfig(source);
  assert.equal(cfg.treasury.coldAddress, ZERO, 'config accepted 0x0 as the destination of all revenue');

  // 2. It survives the single-wallet policy gate.
  const policy = assertTreasuryPolicy(cfg, key.address);
  assert.equal(policy.singleWallet, true);
  assert.equal(policy.treasuryRequired, true);

  // 3. And the module constructs happily around it.
  const s = treasuryScene({ env: { X402_TREASURY_COLD_ADDRESS: ZERO }, eth: 10n ** 15n, usdc: USDC(50) });
  assert.equal(s.treasury.coldAddress, ZERO);
});

test('C1b: every precompile address is accepted too (0x…01 … 0x…09)', () => {
  for (let i = 1; i <= 9; i++) {
    const addr = '0x' + '0'.repeat(39) + String(i);
    const { source } = envFor({ X402_TREASURY_COLD_ADDRESS: addr });
    const cfg = cfgMod.loadEvmFacilitatorConfig(source);
    assert.equal(cfg.treasury.coldAddress, addr, `precompile ${addr} accepted as the sweep destination`);
  }
});

test('C1c: a zero-address sweep is signed and broadcast; on real USDC it reverts and disables the drain', async () => {
  const s = treasuryScene({
    env: { X402_TREASURY_COLD_ADDRESS: ZERO },
    eth: 10n ** 15n,
    usdc: USDC(50),
    // FiatTokenV2._transfer: require(to != address(0), "ERC20: transfer to the zero address")
    chain: { revertSweepWith: 'execution reverted: ERC20: transfer to the zero address' },
  });

  const r1 = await s.treasury.attemptSweep({ trigger: 'interval' });
  assert.equal(r1.action, 'failed');
  const r2 = await s.treasury.attemptSweep({ trigger: 'interval' });
  assert.equal(r2.action, 'failed');

  const st = s.treasury.status();
  assert.equal(st.sweeping_disabled, true);
  assert.equal(st.sipping_disabled, false);
  // The facilitator keeps taking payments; readiness is not failed by this.
  assert.match(s.treasury.warning(), /sweep failures/);
  assert.equal(s.balances().usdc, USDC(50), 'every dollar of revenue stays on the hot key');
});

/* ------------------------------------- C2: caller-supplied balance oracle -- */

test('C2: sweepNow() trusts a caller-supplied balance object for SIZING (destination still immutable)', async () => {
  const s = treasuryScene({ eth: 10n ** 15n, usdc: USDC(25) });

  const forged = { ethWei: 10n ** 18n, usdcAtomic: USDC(1_000_000), at: Date.now() };
  const r = await s.treasury.sweepNow({ balances: forged });

  // The transfer that got built is for money the wallet does not have.
  assert.equal(r.action, 'failed', 'it reverts, but only because the chain says so — nothing here cross-checked');
  assert.match(String(r.detail), /exceeds balance/);
  assert.equal(s.treasury.status().sweep_consecutive_failures, 1, 'and it walked the breaker one strike closer');

  // Destination immutability holds even on the forged path: assert against
  // the bytes the module would have broadcast.
  const s2 = treasuryScene({ eth: 10n ** 15n, usdc: USDC(1_000_000) });
  const r2 = await s2.treasury.sweepNow({ balances: { ethWei: 1n, usdcAtomic: USDC(999_000), at: 0 } });
  assert.equal(r2.action, 'swept');
  const tx = decodeEip1559(s2.rpc.sent[s2.rpc.sent.length - 1].raw);
  assert.equal(evm.addressEquals(tx.to, C.usdc), true);
  const dest = '0x' + evm.strip0x(tx.data).slice(32, 72);
  assert.equal(evm.addressEquals(dest, s2.cold), true, 'destination is still and only the configured cold address');
  // ...but the AMOUNT came from the forged oracle, not from the chain.
  assert.equal(BigInt('0x' + evm.strip0x(tx.data).slice(72, 136)), USDC(999_000) - s2.cfg.treasury.usdcCeilingAtomic);
});

test('C2b: no destination can be injected through any argument shape, including the prototype chain', async () => {
  const attacker = kp().address;
  const s = treasuryScene({ eth: 10n ** 15n, usdc: USDC(50) });
  Object.defineProperty(Object.prototype, 'coldAddress', { value: attacker, configurable: true });
  Object.defineProperty(Object.prototype, 'destination', { value: attacker, configurable: true });
  try {
    const r = await s.treasury.sweepNow({ to: attacker });
    assert.equal(r.action, 'swept');
    assert.equal(r.destination, s.cold);
    assert.equal(s.rpc.getUsdc(attacker), 0n);
  } finally {
    delete Object.prototype.coldAddress;
    delete Object.prototype.destination;
  }
});

/* -------------------------------------------- C3: refuel vs readiness ------ */

test('C3: the sip trigger and the readiness gas floor are the same number — refuelling implies not-ready', async () => {
  const defaults = cfgMod.loadEvmFacilitatorConfig(envFor().source);
  assert.equal(defaults.minGasBalanceWei, defaults.treasury.ethFloorWei,
    'X402_MIN_GAS_BALANCE_WEI == X402_TREASURY_ETH_FLOOR_WEI (both 1e14 by default)');

  // One wei under the floor: the treasury wants to sip...
  const s = treasuryScene({ eth: defaults.treasury.ethFloorWei - 1n, usdc: USDC(30) });
  const sip = await s.treasury.attemptSip({ trigger: 'interval' });
  assert.equal(sip.action, 'sipped');

  // ...and at that same balance the facilitator reports NOT READY.
  const s2 = treasuryScene({ eth: defaults.treasury.ethFloorWei - 1n, usdc: USDC(30) });
  const fac = s2.createFacilitator();
  const r = await fac.readiness();
  assert.equal(r.ready, false);
  assert.match(String(r.checks.gas_balance), /^low:/,
    'every refuel cycle begins with the facilitator advertising itself unhealthy');
});

test('C3b: with the DOCUMENTED readiness floor (0.002 ETH) there is a 20x dead band where the facilitator is unready and the treasury refuses to act', async () => {
  // .env.example line 176 and README line 294 both document
  // X402_MIN_GAS_BALANCE_WEI = 2000000000000000 (~0.002 ETH). The code default
  // is 1e14; either way the two knobs are never cross-validated.
  const documented = { X402_MIN_GAS_BALANCE_WEI: '2000000000000000' };
  const eth = 500_000_000_000_000n; // 0.0005 ETH: under the readyz floor, over the sip floor

  const s = treasuryScene({ env: documented, eth, usdc: USDC(30) });
  const sip = await s.treasury.attemptSip({ trigger: 'interval' });
  assert.equal(sip.reason, 'above_eth_floor', 'the treasury sees nothing to do');

  const fac = s.createFacilitator();
  const r = await fac.readiness();
  assert.equal(r.ready, false, 'while the facilitator refuses traffic for want of gas');

  // And the adaptive minimum sip cannot clear that floor even when it fires:
  // $0.50 buys ~0.000266 ETH, an order of magnitude under 0.002 ETH.
  const halfDollarWei = s.rpc.quoteOut(USDC(0.5), 100);
  assert.ok(halfDollarWei < 2_000_000_000_000_000n / 7n,
    `a $0.50 sip delivers ${halfDollarWei} wei — it can never restore readiness at the documented floor`);
});

/* ------------------------------------------ negative controls (no finding) - */

test('NEG: partial/whitespace/case env values all fail closed', () => {
  const cases = [
    ['', /requires X402_TREASURY_COLD_ADDRESS/],
    ['   ', /not a 0x-prefixed 20-byte hex address/],
    [' 0x20fEee2dC0d4b36f69ddca69d0cE32d7E80b27a6', /not a 0x-prefixed 20-byte hex address/],
    ['0x20fEee2dC0d4b36f69ddca69d0cE32d7E80b27a6 ', /not a 0x-prefixed 20-byte hex address/],
    ['0x20feee2dc0d4b36f69ddca69d0ce32d7e80b27a6', /must be EIP-55 checksummed exactly/],
    ['0x20fEee2dC0d4b36f69ddca69d0cE32d7E80b27a7', /bad EIP-55 checksum/],
    ['0x20fEee2dC0d4b36f69ddca69d0cE32d7E80b27a', /not a 0x-prefixed 20-byte hex address/],
    ['20fEee2dC0d4b36f69ddca69d0cE32d7E80b27a6', /not a 0x-prefixed 20-byte hex address/],
  ];
  for (const [value, re] of cases) {
    const { source } = envFor({ X402_TREASURY_COLD_ADDRESS: value });
    assert.throws(() => cfgMod.loadEvmFacilitatorConfig(source), re, `cold address ${JSON.stringify(value)}`);
  }
});

test('NEG: only exactly "1" enables the treasury, and anything else refuses single-wallet startup', () => {
  for (const v of ['true', 'yes', ' 1', '1 ', '01', 'TRUE', 'on']) {
    const { key, source } = envFor({ X402_TREASURY_ENABLED: v });
    const cfg = cfgMod.loadEvmFacilitatorConfig(source);
    assert.equal(cfg.treasury.enabled, false, `X402_TREASURY_ENABLED=${JSON.stringify(v)} must not enable`);
    assert.throws(() => assertTreasuryPolicy(cfg, key.address), /single-wallet mode refused/);
  }
});

test('NEG: a cold address pointing at USDC / the router / the quoter / WETH9 is refused at construction', () => {
  for (const addr of [C.usdc, C.swapRouter02, C.quoterV2, C.weth9]) {
    assert.throws(
      () => treasuryScene({ env: { X402_TREASURY_COLD_ADDRESS: evm.validateAddress(addr, 'x') }, eth: 1n }),
      /points at the .* contract/,
      `cold address ${addr}`
    );
  }
  // ...and the sentinel addresses the router itself uses are NOT covered by
  // that list — only the zero-address case above shows how thin the net is.
  assert.equal(uniswap.RECIPIENT_SENTINELS.MSG_SENDER, '0x0000000000000000000000000000000000000001');
});
