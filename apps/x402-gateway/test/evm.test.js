'use strict';
/**
 * Unit tests: EVM primitives (keccak/EIP-712/RLP/signatures — verified
 * against LIVE-read chain vectors), BigInt money parsing edge cases, key
 * handling, and the strict fail-closed facilitator config loader.
 */

const test = require('node:test');
const assert = require('node:assert');

const evm = require('../src/facilitator-evm/evm');
const usdc = require('../src/facilitator-evm/usdc');
const { loadSigner, redact } = require('../src/facilitator-evm/key');
const cfgMod = require('../src/config');
const { atomicToDecimalString } = require('../src/metrics');
const { kp, ethSign } = require('./evm-helpers');

/* ----------------------------------------------------------- EIP-712 -- */

test('domain separator matches LIVE Base mainnet USDC DOMAIN_SEPARATOR()', () => {
  // Live eth_call 2026-08-15: 0x02fa7265...834f
  const ds = evm.bytesToHex(evm.domainSeparator({
    name: 'USD Coin', version: '2', chainId: 8453,
    verifyingContract: '0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913',
  }));
  assert.equal(ds, '0x02fa7265e7c5d81118673727957699e4d68f74cd74b7db77da710fe8a2c7834f');
});

test('domain separator matches LIVE Base Sepolia USDC DOMAIN_SEPARATOR()', () => {
  // Live eth_call 2026-08-15: 0x71f17a3b...9818 — note the DIFFERENT domain
  // name ("USDC" vs mainnet's "USD Coin"): the check that catches the trap.
  const ds = evm.bytesToHex(evm.domainSeparator({
    name: 'USDC', version: '2', chainId: 84532,
    verifyingContract: '0x036CbD53842c5426634e7929541eC2318f3dCF7e',
  }));
  assert.equal(ds, '0x71f17a3b2ff373b803d70a5a07c046c1a2bc8e89c09ef722fcb047abe94c9818');
});

test('transferWithAuthorization typehash and calldata selector', () => {
  // keccak256 of the canonical signature string must equal the pinned hash.
  const sig = 'TransferWithAuthorization(address from,address to,uint256 value,uint256 validAfter,uint256 validBefore,bytes32 nonce)';
  assert.equal(evm.bytesToHex(evm.keccak(Buffer.from(sig, 'ascii'))),
    evm.bytesToHex(usdc.TRANSFER_WITH_AUTHORIZATION_TYPEHASH));
  const auth = {
    from: '0x' + '11'.repeat(20), to: '0x' + '22'.repeat(20),
    value: 10000n, validAfter: 0n, validBefore: 2000000000n, nonce: '0x' + 'ab'.repeat(32),
  };
  const data = usdc.transferAuthCalldata(auth, '0x' + '11'.repeat(32) + '22'.repeat(32) + '1b');
  assert.ok(data.startsWith('0xe3ee160e'));
  assert.equal(data.length, 2 + 8 + 9 * 64); // selector + 9 words
});

/* -------------------------------------------------------- signatures -- */

test('sign/recover roundtrip and wrong-key mismatch', () => {
  const a = kp();
  const b = kp();
  const digest = evm.keccak(Buffer.from('x402'));
  const sig = ethSign(digest, a.priv);
  assert.equal(evm.recoverAddress(digest, sig), a.address);
  assert.notEqual(evm.recoverAddress(digest, sig), b.address);
  // Tampered digest recovers a different signer.
  assert.notEqual(evm.recoverAddress(evm.keccak(Buffer.from('tampered')), sig), a.address);
});

test('signature validation: bad v, high-s, wrong length all rejected', () => {
  const a = kp();
  const digest = evm.keccak(Buffer.from('m'));
  const sig = ethSign(digest, a.priv);
  // v = 29
  assert.throws(() => evm.parseEthSignature(sig.slice(0, -2) + '1d'), /v must be 27 or 28/);
  // high-s: s' = n - s is the malleable twin
  const r = sig.slice(2, 66);
  const s = BigInt('0x' + sig.slice(66, 130));
  const highS = (evm.SECP256K1_N - s).toString(16).padStart(64, '0');
  assert.throws(() => evm.parseEthSignature('0x' + r + highS + sig.slice(130)), /low-s/);
  // 64 bytes
  assert.throws(() => evm.parseEthSignature('0x' + 'aa'.repeat(64)), /65 bytes/);
});

test('EIP-55 checksummed addresses enforced', () => {
  assert.equal(evm.toChecksumAddress('0x833589fcd6edb6e08f4c7c32d4f71b54bda02913'),
    '0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913');
  // all-lowercase accepted, normalized
  assert.equal(evm.validateAddress('0x833589fcd6edb6e08f4c7c32d4f71b54bda02913'),
    '0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913');
  // wrong mixed-case checksum rejected
  assert.throws(() => evm.validateAddress('0x833589FCD6eDb6E08f4c7C32D4f71b54bdA02913'), /checksum/);
  assert.throws(() => evm.validateAddress('0x1234'), /address/);
});

/* --------------------------------------------------------------- RLP -- */

test('RLP encoding vectors (ethereum wiki canonical)', () => {
  const hex = (b) => Buffer.from(b).toString('hex');
  assert.equal(hex(evm.rlpEncode(Buffer.from('dog'))), '83646f67');
  assert.equal(hex(evm.rlpEncode([Buffer.from('cat'), Buffer.from('dog')])), 'c88363617483646f67');
  assert.equal(hex(evm.rlpEncode(new Uint8Array(0))), '80'); // empty string / zero int
  assert.equal(hex(evm.rlpEncode([])), 'c0');
  assert.equal(hex(evm.rlpEncode(evm.bigintToMinimalBytes(1024n))), '820400');
  assert.equal(hex(evm.rlpEncode(evm.bigintToMinimalBytes(15n))), '0f'); // single byte < 0x80
  const lorem = Buffer.from('Lorem ipsum dolor sit amet, consectetur adipisicing elit');
  assert.equal(hex(evm.rlpEncode(lorem)).slice(0, 4), 'b838'); // 56 bytes => long-string prefix
});

test('EIP-1559 tx: type-2 envelope, decodable fee math', () => {
  // Structural pin. The full encoding was ALSO proven against a live node
  // (sepolia.base.org decoded a signed tx from this code and computed
  // 21000 * maxFeePerGas for the insufficient-funds error — meaning RLP,
  // signature and sender recovery all parsed on a real client).
  const a = kp();
  const tx = evm.signEip1559Tx({
    chainId: 8453, nonce: 0, maxPriorityFeePerGas: 1_000_000n, maxFeePerGas: 11_000_000n,
    gasLimit: 100_000n, to: '0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913', value: 0n, data: '0x',
  }, a.priv);
  assert.ok(tx.rawTx.startsWith('0x02'));
  assert.equal(tx.hash.length, 66);
  // Deterministic given same inputs? No — RFC6979 is deterministic per key+digest.
  const tx2 = evm.signEip1559Tx({
    chainId: 8453, nonce: 0, maxPriorityFeePerGas: 1_000_000n, maxFeePerGas: 11_000_000n,
    gasLimit: 100_000n, to: '0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913', value: 0n, data: '0x',
  }, a.priv);
  assert.equal(tx.hash, tx2.hash);
});

/* ------------------------------------------------------- money BigInt -- */

test('BigInt price parsing edge cases (USD -> USDC atomic)', () => {
  assert.equal(cfgMod.usdToUsdcAtomic('0.01'), '10000');
  assert.equal(cfgMod.usdToUsdcAtomic('0.005'), '5000');
  assert.equal(cfgMod.usdToUsdcAtomic('1'), '1000000');
  assert.equal(cfgMod.usdToUsdcAtomic('0.000001'), '1');
  assert.equal(cfgMod.usdToUsdcAtomic('0.010'), '10000'); // trailing zeros fine
  assert.equal(cfgMod.usdToUsdcAtomic('123456789.123456'), '123456789123456');
  // sub-atomic precision refused, not truncated
  assert.throws(() => cfgMod.usdToUsdcAtomic('0.0000001'), /fractional digits/);
  // garbage refused
  for (const bad of ['1e3', '-1', '.5', '1.', '0x10', '', 'NaN', '1,000']) {
    assert.throws(() => cfgMod.usdToUsdcAtomic(bad), Error, `should reject ${JSON.stringify(bad)}`);
  }
});

test('atomic -> decimal string rendering is digit-wise exact', () => {
  assert.equal(atomicToDecimalString(10000n, 6), '0.01');
  assert.equal(atomicToDecimalString(0n, 6), '0');
  assert.equal(atomicToDecimalString(1n, 6), '0.000001');
  assert.equal(atomicToDecimalString(1_000_000n, 6), '1');
  // beyond float precision: 2^60 + 1 atomic units survive exactly
  assert.equal(atomicToDecimalString(1152921504606846977n, 6), '1152921504606.846977');
});

/* ---------------------------------------------------------------- key -- */

test('facilitator key: malformed refused, valid derives address only', () => {
  assert.throws(() => loadSigner('not-a-key'), /malformed/);
  assert.throws(() => loadSigner(''), /malformed/);
  assert.throws(() => loadSigner('0x' + '0'.repeat(64)), /out of the secp256k1 range/);
  assert.throws(() => loadSigner('0x' + 'f'.repeat(64)), /out of the secp256k1 range/);
  const a = kp();
  const signer = loadSigner(a.privHex);
  assert.equal(signer.address, a.address);
  // the signer never serializes its key
  assert.equal(JSON.stringify(signer), JSON.stringify({ address: a.address }));
});

test('redaction strips key-shaped fields by name', () => {
  const out = redact({
    payer: '0xabc', privateKey: 'SECRET', nested: { signature: '0xsig', amount: '5' },
    raw_tx: '0x02...', list: [{ apiKey: 'k' }],
  });
  assert.equal(out.privateKey, '[REDACTED]');
  assert.equal(out.nested.signature, '[REDACTED]');
  assert.equal(out.raw_tx, '[REDACTED]');
  assert.equal(out.payer, '0xabc');
  assert.equal(out.nested.amount, '5');
});

/* ------------------------------------------------- config fail-closed -- */

const GOOD_ENV = {
  X402_NETWORK: 'base',
  X402_RPC_URL: 'https://mainnet.base.org',
  X402_SETTLEMENT_ADDRESS: '0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913',
  X402_FACILITATOR_PRIVATE_KEY: '0x' + '11'.repeat(32),
};

test('facilitator config: valid env loads with allowlisted values', () => {
  const cfg = cfgMod.loadEvmFacilitatorConfig({ ...GOOD_ENV });
  assert.equal(cfg.chainId, 8453);
  assert.equal(cfg.caip2, 'eip155:8453');
  assert.equal(cfg.asset, '0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913');
  assert.equal(cfg.eip712.name, 'USD Coin'); // the mainnet trap, pinned
  assert.equal(cfg.port, 8743);
  assert.equal(typeof cfg.maxFeePerGasWei, 'bigint');
});

test('facilitator config: every contradiction fails closed', () => {
  const cases = [
    [{ X402_NETWORK: 'ethereum' }, /not allowlisted/],
    [{ X402_CHAIN_ID: '1' }, /contradicts/],
    [{ X402_ASSET: '0x' + 'aa'.repeat(20) }, /not allowlisted/],
    [{ X402_RPC_URL: '' }, /X402_RPC_URL/],
    [{ X402_RPC_URL: 'ftp://x' }, /X402_RPC_URL/],
    [{ X402_SETTLEMENT_ADDRESS: '' }, /X402_SETTLEMENT_ADDRESS/],
    [{ X402_SETTLEMENT_ADDRESS: '0x123' }, /X402_SETTLEMENT_ADDRESS/],
    [{ X402_FACILITATOR_PRIVATE_KEY: '' }, /X402_FACILITATOR_PRIVATE_KEY/],
    [{ X402_MAX_FEE_PER_GAS_WEI: '1.5' }, /decimal integer/],
    [{ X402_MAX_GAS_PER_SETTLEMENT: '100' }, /never settle/],
    [{ X402_CONFIRMATIONS: 'many' }, /integer/],
  ];
  for (const [override, re] of cases) {
    assert.throws(() => cfgMod.loadEvmFacilitatorConfig({ ...GOOD_ENV, ...override }), re,
      `expected failure for ${JSON.stringify(override)}`);
  }
});

test('gateway load(): facilitator mode selection', () => {
  // The seven live env names keep working with no facilitator-mode vars set.
  const cfg = cfgMod.load();
  assert.equal(cfg.facilitatorMode, 'remote');
  assert.ok(cfg.evmFacilitatorUrl.length > 0);
  // self mode points at the loopback facilitator
  process.env.X402_FACILITATOR_MODE = 'self';
  try {
    assert.equal(cfgMod.load().evmFacilitatorUrl, 'http://127.0.0.1:8743');
  } finally {
    delete process.env.X402_FACILITATOR_MODE;
  }
  process.env.X402_FACILITATOR_MODE = 'nonsense';
  try {
    assert.throws(() => cfgMod.load(), /X402_FACILITATOR_MODE/);
  } finally {
    delete process.env.X402_FACILITATOR_MODE;
  }
});
