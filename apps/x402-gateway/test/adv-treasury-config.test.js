'use strict';
/**
 * ADVERSARIAL REGRESSION — config downgrade, sweep redirection, and the
 * refuel/readiness overlap.
 *
 * FINDING C1 (fixed) — the cold-address "typo guard" was an EIP-55 checksum
 * comparison, and the addresses most likely to appear by accident have no
 * case to get wrong: `0x0000…0000` (an unset/templated variable, a truncated
 * paste) and the precompiles `0x…01`-`0x…09` are all-digit, so
 * toChecksumAddress returned them unchanged and the loader accepted them.
 * Real USDC reverts on a transfer to 0x0, so the deployment started, reported
 * ready, took payments, and two ticks later disabled its own sweep; a
 * burn-style address that does NOT revert would have destroyed every swept
 * dollar silently. FIX: the reserved range and the known burn addresses are
 * refused at startup, and a cold address WITH CODE is refused at runtime by
 * one eth_getCode unless X402_TREASURY_COLD_ALLOW_CONTRACT=1.
 *
 * FINDING C2 (fixed) — attemptSip/attemptSweep took a `balances` object from
 * the caller and sized real transfers from it. The destination was always
 * immutable; the AMOUNT was not. FIX: both entry points read their own
 * balance; a caller-supplied one is ignored.
 *
 * FINDING C3 (fixed) — X402_TREASURY_ETH_FLOOR_WEI and X402_MIN_GAS_BALANCE_WEI
 * shipped with the same default, so the treasury could only ever act at a
 * balance where /readyz already reported 503; and with the value the docs
 * quoted for the readiness floor there was a 20x dead band where the
 * facilitator refused traffic while the treasury reported "above_eth_floor".
 * FIX: the floor defaults to 5x the readiness floor and the two are
 * cross-validated at startup.
 *
 * Everything else here is a NEGATIVE control: the config-downgrade attempts
 * that fail closed, recorded so a future change that loosens them fails.
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

test('C1a: the zero address is REFUSED as a cold address (the checksum cannot catch it)', () => {
  const { source } = envFor({ X402_TREASURY_COLD_ADDRESS: ZERO });
  // It still passes the checksum — that is exactly why the checksum is not
  // the guard here.
  assert.equal(evm.validateAddress(ZERO, 'x'), ZERO);
  assert.throws(() => cfgMod.loadEvmFacilitatorConfig(source), /reserved low-address range/);
});

test('C1b: every precompile and the known burn addresses are refused too', () => {
  for (let i = 1; i <= 9; i++) {
    const addr = '0x' + '0'.repeat(39) + String(i);
    const { source } = envFor({ X402_TREASURY_COLD_ADDRESS: addr });
    assert.throws(() => cfgMod.loadEvmFacilitatorConfig(source), /reserved low-address range/, `precompile ${addr}`);
  }
  // The router's own MSG_SENDER/ADDRESS_THIS sentinels live in that range.
  assert.equal(uniswap.RECIPIENT_SENTINELS.MSG_SENDER, '0x0000000000000000000000000000000000000001');
  for (const burn of ['0x000000000000000000000000000000000000dEaD', '0x00000000000000000000000000000000DeaDBeef']) {
    const { source } = envFor({ X402_TREASURY_COLD_ADDRESS: burn });
    // 0x…dEaD falls inside the reserved range; 0x…DeaDBeef is caught by the
    // burn list. Either way a sweep to it can never be recovered.
    assert.throws(() => cfgMod.loadEvmFacilitatorConfig(source), /burn address|reserved low-address range/, burn);
  }
});

test('C1c: a cold address with CONTRACT CODE is refused at runtime unless explicitly allowed', async () => {
  const s = treasuryScene({ eth: 10n ** 15n, usdc: USDC(50) });
  // The operator pasted a contract address (or a proxy that swallows ERC-20s).
  s.rpc.state.code.set(s.cold.toLowerCase(), '0x60806040');

  const r = await s.treasury.attemptSweep({ trigger: 'interval' });
  assert.equal(r.action, 'skipped');
  assert.equal(r.reason, 'cold_address_is_contract');
  assert.equal(s.rpc.sent.length, 0, 'not one dollar moved toward an unverified destination');
  assert.match(s.treasury.warning(), /contract code/);
  assert.equal(s.balances().usdc, USDC(50));

  // Declared deliberately (a safe/multisig), it works.
  const ok = treasuryScene({
    eth: 10n ** 15n, usdc: USDC(50),
    env: { X402_TREASURY_COLD_ALLOW_CONTRACT: '1' },
  });
  ok.rpc.state.code.set(ok.cold.toLowerCase(), '0x60806040');
  const r2 = await ok.treasury.attemptSweep({ trigger: 'interval' });
  assert.equal(r2.action, 'swept');
  assert.equal(ok.rpc.getUsdc(ok.cold), USDC(30));
});

test('C1d: an unreadable cold address is a skip, not a sweep into the unknown', async () => {
  const s = treasuryScene({ eth: 10n ** 15n, usdc: USDC(50) });
  const inner = s.rpc.call.bind(s.rpc);
  s.rpc.call = async (m, p = []) => {
    if (m === 'eth_getCode') throw new Error('rpc timeout');
    return inner(m, p);
  };
  const r = await s.treasury.attemptSweep({ trigger: 'interval' });
  assert.equal(r.reason, 'cold_address_unverified');
  assert.equal(s.treasury.status().sweep_consecutive_failures, 0, 'the RPC failing is not the sweep failing');
});

/* ------------------------------------- C2: caller-supplied balance oracle -- */

test('C2: a caller-supplied balance cannot size a transfer — the chain does', async () => {
  const s = treasuryScene({ eth: 10n ** 15n, usdc: USDC(25) });

  const forged = { ethWei: 10n ** 18n, usdcAtomic: USDC(1_000_000), at: Date.now() };
  const r = await s.treasury.sweepNow({ balances: forged });

  assert.equal(r.action, 'swept');
  assert.equal(r.amount_usdc_atomic, USDC(5), 'sized from the real $25 balance minus the $20 ceiling, not the forged $1M');
  assert.equal(s.treasury.status().sweep_consecutive_failures, 0, 'and no reverted transfer walked the breaker');

  const tx = decodeEip1559(s.rpc.sent[s.rpc.sent.length - 1].raw);
  assert.equal(evm.addressEquals(tx.to, C.usdc), true);
  const dest = '0x' + evm.strip0x(tx.data).slice(32, 72);
  assert.equal(evm.addressEquals(dest, s.cold), true, 'destination is still and only the configured cold address');
  assert.equal(BigInt('0x' + evm.strip0x(tx.data).slice(72, 136)), USDC(5));

  // Same for the sip: a forged balance cannot conjure a swap.
  const dry = treasuryScene({ eth: 1n, usdc: 0n });
  const sip = await dry.treasury.sipNow({ balances: { ethWei: 0n, usdcAtomic: USDC(1_000), at: 0 } });
  assert.equal(sip.reason, 'insufficient_usdc');
  assert.equal(dry.rpc.sent.length, 0);
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

test('C3: the sip trigger sits well above the readiness floor — refuelling starts while still healthy', async () => {
  const defaults = cfgMod.loadEvmFacilitatorConfig(envFor().source);
  assert.ok(defaults.treasury.ethFloorWei >= 3n * defaults.minGasBalanceWei,
    `X402_TREASURY_ETH_FLOOR_WEI ${defaults.treasury.ethFloorWei} vs X402_MIN_GAS_BALANCE_WEI ${defaults.minGasBalanceWei}`);

  // One wei under the floor: the treasury sips...
  const s = treasuryScene({ eth: defaults.treasury.ethFloorWei - 1n, usdc: USDC(30) });
  const sip = await s.treasury.attemptSip({ trigger: 'interval' });
  assert.equal(sip.action, 'sipped');

  // ...and at that same balance the facilitator is still READY, so it keeps
  // taking the payments that pay for the gas.
  const s2 = treasuryScene({ eth: defaults.treasury.ethFloorWei - 1n, usdc: USDC(30) });
  const r = await s2.createFacilitator().readiness();
  assert.equal(r.ready, true);
  assert.equal(r.checks.gas_balance, true);
});

test('C3b: a readiness floor at or above the sip trigger is refused at startup (no dead band can be configured)', () => {
  // .env.example and the README used to document X402_MIN_GAS_BALANCE_WEI as
  // 2000000000000000 (~0.002 ETH), 20x the code default. With that value and
  // the old 1e14 sip trigger there was a band where the facilitator refused
  // traffic while the treasury reported "nothing to do".
  const { source } = envFor({ X402_MIN_GAS_BALANCE_WEI: '2000000000000000' });
  assert.throws(() => cfgMod.loadEvmFacilitatorConfig(source), /at least 3x X402_MIN_GAS_BALANCE_WEI/);

  // The spec's literal floor default (1e14) is likewise refused when it
  // equals the readiness floor — which is precisely the shipped-defaults bug.
  const equal = envFor({ X402_TREASURY_ETH_FLOOR_WEI: '100000000000000' });
  assert.throws(() => cfgMod.loadEvmFacilitatorConfig(equal.source), /at least 3x X402_MIN_GAS_BALANCE_WEI/);

  // Lowering the readiness floor to match is a legitimate operator choice.
  const ok = envFor({ X402_TREASURY_ETH_FLOOR_WEI: '100000000000000', X402_MIN_GAS_BALANCE_WEI: '30000000000000' });
  assert.equal(cfgMod.loadEvmFacilitatorConfig(ok.source).treasury.ethFloorWei, 100000000000000n);
});

test('C3c: with a price configured, a minimum sip that could not restore readiness refuses startup', () => {
  // $0.50 at $10,000/ETH is 5e13 wei — under the 1e14 readiness floor, so the
  // smallest permitted sip could never bring the facilitator back to ready.
  const bad = envFor({ X402_ETH_USD_PRICE: '10000' });
  assert.throws(() => cfgMod.loadEvmFacilitatorConfig(bad.source), /could not restore readiness/);
  // At a realistic price it is fine.
  const ok = envFor({ X402_ETH_USD_PRICE: '3000' });
  assert.equal(cfgMod.loadEvmFacilitatorConfig(ok.source).treasury.sipMinUsdcAtomic, 500_000n);
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
});

test('NEG: the signing lease TTL must outlast two check intervals', () => {
  const { source } = envFor({ X402_TREASURY_CHECK_INTERVAL_S: '600', X402_TREASURY_LEASE_TTL_S: '900' });
  assert.throws(() => cfgMod.loadEvmFacilitatorConfig(source), /LEASE_TTL_S .* at least 2x/);
});
