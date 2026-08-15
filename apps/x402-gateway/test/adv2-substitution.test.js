'use strict';
/**
 * ADVERSARIAL REVIEW 2/3 — vector (4): amount / token / network / recipient
 * substitution. Two defense layers are probed independently:
 *
 *   Layer A (gateway match): the client MUST echo the exact PaymentRequirements
 *   the server offered. The gateway rebuilds `accepts` from its own config and
 *   compares the client's `accepted` verbatim (protocol.requirementsEqual over
 *   canonicalJson). The mock facilitator here accepts ANY well-formed payment,
 *   so a 402 (instead of 200) proves the GATEWAY blocked the tamper before any
 *   facilitator round-trip.
 *
 *   Layer B (facilitator verify): defense-in-depth. Even if a compromised or
 *   buggy gateway forwarded manipulated requirements, the self-hosted exact-EVM
 *   facilitator re-validates every payment-critical field against its OWN
 *   allowlisted config and the signed EIP-712 digest.
 *
 * These are PoCs: where the defense holds the test asserts it holds. Findings
 * (if any) are reported in the review summary, not fixed here.
 */

const { test } = require('node:test');
const assert = require('node:assert');

const protocol = require('../src/protocol');
const cfgMod = require('../src/config');
const {
  buildTestGateway, request, BASE,
} = require('./gateway-helpers');
const { scene } = require('./evm-helpers');

const QRNG = '/x402/qrng/draw';
const OTHER_REAL_TOKEN = cfgMod.USDC_DEFAULTS[cfgMod.NETWORKS.BASE_SEPOLIA]; // 0x036C...CF7e

/** Fetch the 402 and return the server's offered accepts[0] + resource url. */
async function offer(baseUrl, path) {
  const res = await request(baseUrl, path);
  assert.equal(res.status, 402, 'unpaid request must 402');
  const required = protocol.decodeHeader(res.headers.get('payment-required'));
  return { accepted: required.accepts[0], resource: required.resource.url };
}

/** Build a PAYMENT-SIGNATURE header from a (possibly mutated) accepted object. */
function payHeader(accepted, resource, authOverrides = {}) {
  const payload = {
    x402Version: 2,
    resource,
    accepted,
    payload: {
      signature: '0x' + '11'.repeat(65),
      authorization: Object.assign({
        from: '0x' + 'ab'.repeat(20),
        to: accepted.payTo,
        value: accepted.amount,
        validAfter: '0',
        validBefore: '9999999999',
        nonce: '0x' + 'cd'.repeat(32),
      }, authOverrides),
    },
  };
  return protocol.encodeHeader(payload);
}

async function sendPay(baseUrl, path, accepted, resource, authOverrides) {
  return request(baseUrl, path, {
    headers: { 'payment-signature': payHeader(accepted, resource, authOverrides) },
  });
}

/* ============================ Layer A — gateway match ==================== */

test('adv2/4 gateway: honest echo unlocks (positive control)', async () => {
  const t = await buildTestGateway();
  try {
    const { accepted, resource } = await offer(t.baseUrl, QRNG);
    const res = await sendPay(t.baseUrl, QRNG, accepted, resource);
    assert.equal(res.status, 200, 'untampered payment must unlock — proves the harness reaches settlement');
    assert.equal(t.fac.settled(), 1);
  } finally {
    await t.close();
  }
});

test('adv2/4 gateway: lowercased payTo/asset is rejected (checksummed vs lowercase)', async () => {
  const evm = require('../src/facilitator-evm/evm');
  // A payTo that actually carries mixed case, so lowercasing changes the string.
  const mixedPayTo = evm.toChecksumAddress('0x' + 'ab'.repeat(20));
  assert.notEqual(mixedPayTo, mixedPayTo.toLowerCase(), 'sanity: payTo must be mixed-case');
  const t = await buildTestGateway({ overrides: { basePayTo: mixedPayTo } });
  try {
    const { accepted, resource } = await offer(t.baseUrl, QRNG);
    const lcPayTo = Object.assign({}, accepted, { payTo: accepted.payTo.toLowerCase() });
    // asset default 0x833589fCD6...2913 is already checksummed/mixed-case.
    const lcAsset = Object.assign({}, accepted, { asset: accepted.asset.toLowerCase() });
    const r1 = await sendPay(t.baseUrl, QRNG, lcPayTo, resource);
    const r2 = await sendPay(t.baseUrl, QRNG, lcAsset, resource);
    assert.equal(r1.status, 402, 'lowercased payTo must not match the server offer');
    assert.equal(r2.status, 402, 'lowercased (non-checksummed) asset must not match the server offer');
    assert.equal(t.fac.settled(), 0, 'no settlement on a rejected match');
  } finally {
    await t.close();
  }
});

test('adv2/4 gateway: amount off-by-one (±1) is rejected', async () => {
  const t = await buildTestGateway();
  try {
    const { accepted, resource } = await offer(t.baseUrl, QRNG);
    const minus = Object.assign({}, accepted, { amount: (BigInt(accepted.amount) - 1n).toString() });
    const plus = Object.assign({}, accepted, { amount: (BigInt(accepted.amount) + 1n).toString() });
    assert.equal((await sendPay(t.baseUrl, QRNG, minus, resource)).status, 402, 'amount-1 must not match');
    assert.equal((await sendPay(t.baseUrl, QRNG, plus, resource)).status, 402, 'amount+1 must not match');
    assert.equal(t.fac.settled(), 0);
  } finally {
    await t.close();
  }
});

test('adv2/4 gateway: different-but-real token (Sepolia USDC) is rejected', async () => {
  const t = await buildTestGateway();
  try {
    const { accepted, resource } = await offer(t.baseUrl, QRNG);
    const swapped = Object.assign({}, accepted, { asset: OTHER_REAL_TOKEN });
    assert.equal((await sendPay(t.baseUrl, QRNG, swapped, resource)).status, 402);
    assert.equal(t.fac.settled(), 0);
  } finally {
    await t.close();
  }
});

test('adv2/4 gateway: network substitution (chainId in requirements) is rejected', async () => {
  const t = await buildTestGateway();
  try {
    const { accepted, resource } = await offer(t.baseUrl, QRNG);
    const swapped = Object.assign({}, accepted, { network: cfgMod.NETWORKS.BASE_SEPOLIA });
    assert.equal((await sendPay(t.baseUrl, QRNG, swapped, resource)).status, 402);
    assert.equal(t.fac.settled(), 0);
  } finally {
    await t.close();
  }
});

test('adv2/4 gateway: adding an extra field or dropping one breaks the match', async () => {
  const t = await buildTestGateway();
  try {
    const { accepted, resource } = await offer(t.baseUrl, QRNG);
    const withExtra = Object.assign({}, accepted, { extra: { name: 'USD Coin', version: '2' } });
    const dropped = Object.assign({}, accepted); delete dropped.maxTimeoutSeconds;
    assert.equal((await sendPay(t.baseUrl, QRNG, withExtra, resource)).status, 402, 'extra field must break verbatim match');
    assert.equal((await sendPay(t.baseUrl, QRNG, dropped, resource)).status, 402, 'missing field must break verbatim match');
    assert.equal(t.fac.settled(), 0);
  } finally {
    await t.close();
  }
});

/* ==================== Layer B — facilitator defense-in-depth ============= */

test('adv2/4 facilitator: authorization.value != required amount => value_mismatch', async () => {
  const sc = scene();
  for (const [value, amount] of [[9_999n, '10000'], [10_001n, '10000']]) {
    const body = sc.payment({ value, amount });
    const v = await sc.facilitator.verify(body);
    assert.equal(v.isValid, false);
    assert.equal(v.invalidReason, 'invalid_exact_evm_payload_authorization_value_mismatch',
      `value ${value} vs amount ${amount}`);
  }
});

test('adv2/4 facilitator: signed recipient != settlement address => recipient_mismatch', async () => {
  const sc = scene();
  const other = '0x' + '99'.repeat(20);
  // auth.to tampered to another address while req.payTo stays the real one.
  const body = sc.payment({ to: other, reqPayTo: sc.payTo });
  const v = await sc.facilitator.verify(body);
  assert.equal(v.isValid, false);
  assert.equal(v.invalidReason, 'invalid_exact_evm_payload_recipient_mismatch');
});

test('adv2/4 facilitator: required payTo != facilitator config => invalid_payment_requirements', async () => {
  const sc = scene();
  const other = '0x' + '99'.repeat(20);
  const body = sc.payment({ to: other, reqPayTo: other });
  const v = await sc.facilitator.verify(body);
  assert.equal(v.isValid, false);
  assert.equal(v.invalidReason, 'invalid_payment_requirements');
});

test('adv2/4 facilitator: different token => invalid_payment_requirements', async () => {
  const sc = scene();
  const body = sc.payment({ asset: OTHER_REAL_TOKEN });
  const v = await sc.facilitator.verify(body);
  assert.equal(v.isValid, false);
  assert.equal(v.invalidReason, 'invalid_payment_requirements');
});

test('adv2/4 facilitator: wrong network => invalid_network', async () => {
  const sc = scene();
  const body = sc.payment({ network: cfgMod.NETWORKS.BASE_SEPOLIA });
  const v = await sc.facilitator.verify(body);
  assert.equal(v.isValid, false);
  assert.equal(v.invalidReason, 'invalid_network');
});

test('adv2/4 facilitator: signature over the WRONG domain (chain) => invalid signature', async () => {
  const sc = scene();
  // Sign the very same authorization under a Base-SEPOLIA domain separator,
  // then present it against the mainnet requirements. The facilitator rebuilds
  // the digest with the MAINNET domain; recovery yields a different address.
  const evm = require('../src/facilitator-evm/evm');
  const sepoliaDomain = evm.domainSeparator({
    name: 'USDC', version: '2', chainId: 84532,
    verifyingContract: OTHER_REAL_TOKEN,
  });
  const body = sc.payment({ signWithDomain: sepoliaDomain });
  const v = await sc.facilitator.verify(body);
  assert.equal(v.isValid, false);
  assert.equal(v.invalidReason, 'invalid_exact_evm_payload_signature');
});

test('adv2/4 facilitator: lowercased-but-correct payTo/asset still verifies (case is normalized, not exploitable)', async () => {
  const sc = scene();
  const body = sc.payment({
    to: sc.payTo.toLowerCase(),
    reqPayTo: sc.payTo.toLowerCase(),
    asset: sc.cfg.asset.toLowerCase(),
  });
  const v = await sc.facilitator.verify(body);
  assert.equal(v.isValid, true, 'the same address in lowercase is the same recipient — addressEquals is case-insensitive by design');
});
