'use strict';
/**
 * Wire-shape tests: the 402 response must match x402 spec v2
 * (PaymentRequired via PAYMENT-REQUIRED header) and the legacy v1 body.
 */

const test = require('node:test');
const assert = require('node:assert/strict');

const protocol = require('../src/protocol');
const { usdToUsdcAtomic, usdToTokenAtomic, decimalToScaled, NETWORKS } = require('../src/config');
const { buildPaymentRequiredForRoute } = require('../src/middleware');
const { addr } = require('./helpers');

const CFG = {
  resourceBaseUrl: 'https://api.example.test',
  serviceName: 'Animica',
  networkEvm: NETWORKS.BASE_MAINNET,
  usdcAsset: '0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913',
  basePayTo: '0x1111111111111111111111111111111111111111',
  networkSvm: NETWORKS.SOLANA_DEVNET,
  wanmMint: addr(),
  wanmTreasury: addr(),
  wanmDecimals: 9,
  wanmUsdPrice: '0.05',
  wanmFeePayerPubkey: addr(),
  maxTimeoutSeconds: 60,
};
const ROUTE = { path: '/paid/echo', priceUsd: '0.005', description: 'Echo', mimeType: 'application/json' };

test('v2 PaymentRequired carries every spec-required field', () => {
  const pr = buildPaymentRequiredForRoute(ROUTE, CFG);

  assert.equal(pr.x402Version, 2);
  assert.equal(pr.resource.url, 'https://api.example.test/paid/echo');
  assert.equal(pr.resource.serviceName, 'Animica');
  assert.ok(pr.resource.serviceName.length <= 32);
  assert.equal(pr.accepts.length, 2);

  for (const a of pr.accepts) {
    assert.equal(a.scheme, 'exact');
    assert.match(a.network, /^[a-z0-9-]+:[A-Za-z0-9_.-]+$/, 'network must be CAIP-2');
    assert.match(a.amount, /^\d+$/, 'amount must be an atomic-unit integer string');
    assert.equal(typeof a.asset, 'string');
    assert.equal(typeof a.payTo, 'string');
    assert.equal(typeof a.maxTimeoutSeconds, 'number');
    // v1 field names must NOT leak into v2
    assert.equal(a.maxAmountRequired, undefined);
    assert.equal(a.resource, undefined);
    assert.equal(a.outputSchema, undefined);
  }

  const [usdc, wanm] = pr.accepts;
  assert.equal(usdc.network, 'eip155:8453');
  assert.equal(usdc.amount, '5000'); // $0.005 in 6-decimal USDC atomic units
  assert.equal(wanm.network, 'solana:EtWTRABZaYq6iMfeYKouRu166VU2xqa1');
  assert.equal(wanm.asset, CFG.wanmMint);
  assert.equal(wanm.payTo, CFG.wanmTreasury);
  assert.equal(wanm.extra.feePayer, CFG.wanmFeePayerPubkey);
  // $0.005 at $0.05/wANM = 0.1 wANM = 1e8 atomic (9 decimals)
  assert.equal(wanm.amount, '100000000');
});

test('v2 header round-trips through base64', () => {
  const pr = buildPaymentRequiredForRoute(ROUTE, CFG);
  const decoded = protocol.decodeHeader(protocol.encodeHeader(pr));
  assert.deepEqual(decoded, JSON.parse(JSON.stringify(pr)));
});

test('v1 body rendering uses legacy names and network slugs', () => {
  const pr = buildPaymentRequiredForRoute(ROUTE, CFG);
  const v1 = protocol.toV1Body(pr);

  assert.equal(v1.x402Version, 1);
  assert.ok(Array.isArray(v1.accepts));
  const [usdc, wanm] = v1.accepts;
  assert.equal(usdc.network, 'base');
  assert.equal(wanm.network, 'solana-devnet');
  for (const a of v1.accepts) {
    assert.match(a.maxAmountRequired, /^\d+$/);
    assert.equal(a.resource, 'https://api.example.test/paid/echo');
    assert.equal(typeof a.description, 'string');
    assert.equal(typeof a.mimeType, 'string');
    assert.equal(a.amount, undefined, 'v2 field name must not leak into v1');
  }
});

test('parsePaymentHeaders: v2 PAYMENT-SIGNATURE and v1 X-PAYMENT both normalize', () => {
  const pr = buildPaymentRequiredForRoute(ROUTE, CFG);
  const accepted = pr.accepts[1];
  const v2Payload = { x402Version: 2, accepted, payload: { transaction: 'AAAA' } };

  const fromV2 = protocol.parsePaymentHeaders(
    { 'payment-signature': protocol.encodeHeader(v2Payload) }, pr.accepts);
  assert.equal(fromV2.wireVersion, 2);
  assert.deepEqual(fromV2.paymentPayload.accepted, accepted);

  const v1Header = protocol.encodeHeader({
    x402Version: 1, scheme: 'exact', network: 'solana-devnet', payload: { transaction: 'AAAA' },
  });
  const fromV1 = protocol.parsePaymentHeaders({ 'x-payment': v1Header }, pr.accepts);
  assert.equal(fromV1.wireVersion, 1);
  assert.deepEqual(fromV1.paymentPayload.accepted, accepted, 'v1 recovers OUR offered requirements');

  assert.equal(protocol.parsePaymentHeaders({}, pr.accepts), null);
});

test('parsePaymentHeaders: every malformed client header is a PaymentParseError, never a raw throw', () => {
  // Regression: garbage base64 used to reach JSON.parse and escape as a
  // SyntaxError, which the HTTP layers answer 500 instead of re-offering a
  // 402. A malformed payment is the client's problem, not a server fault.
  const pr = buildPaymentRequiredForRoute(ROUTE, CFG);
  const b64 = (s) => Buffer.from(s, 'utf8').toString('base64');
  const cases = {
    'not base64 at all': { 'payment-signature': '@@@not-base64@@@' },
    'base64 of plain text': { 'payment-signature': b64('hello') },
    'base64 of a JSON array': { 'payment-signature': b64('[1,2,3]') },
    'base64 of a JSON string': { 'payment-signature': b64('"nope"') },
    'truncated json': { 'payment-signature': b64('{"x402Version":2,') },
    'v1 version on the v2 header': { 'payment-signature': protocol.encodeHeader({ x402Version: 1 }) },
    'accepted is an array': { 'payment-signature': protocol.encodeHeader({ x402Version: 2, accepted: [] }) },
    'accepted missing': { 'payment-signature': protocol.encodeHeader({ x402Version: 2, payload: {} }) },
    'x-payment garbage': { 'x-payment': '####' },
    'x-payment wrong version': { 'x-payment': protocol.encodeHeader({ x402Version: 2 }) },
    'x-payment unoffered lane': {
      'x-payment': protocol.encodeHeader({ x402Version: 1, scheme: 'exact', network: 'ethereum', payload: {} }),
    },
  };
  for (const [label, headers] of Object.entries(cases)) {
    assert.throws(
      () => protocol.parsePaymentHeaders(headers, pr.accepts),
      (e) => e instanceof protocol.PaymentParseError,
      `${label} must raise PaymentParseError`
    );
  }
});

test('validatePaymentRequirements rejects malformed entries', () => {
  const good = {
    scheme: 'exact', network: 'eip155:8453', amount: '5000',
    asset: '0xA', payTo: '0xB', maxTimeoutSeconds: 60,
  };
  protocol.validatePaymentRequirements(good);
  assert.throws(() => protocol.validatePaymentRequirements({ ...good, amount: '0.005' }), /atomic/);
  assert.throws(() => protocol.validatePaymentRequirements({ ...good, network: 'base' }), /CAIP-2/);
  assert.throws(() => protocol.validatePaymentRequirements({ ...good, maxTimeoutSeconds: '60' }), /maxTimeoutSeconds/);
  assert.throws(() => protocol.validatePaymentRequirements({ ...good, payTo: '' }), /payTo/);
});

test('money math: BigInt end to end, rounds up in the merchant\'s favour', () => {
  assert.equal(usdToUsdcAtomic('0.005'), '5000');
  assert.equal(usdToUsdcAtomic('1'), '1000000');
  assert.equal(usdToTokenAtomic('0.005', '0.05', 9), '100000000');
  // 0.01 / 0.03 = 0.3333... wANM -> must round UP at the last atomic unit
  assert.equal(usdToTokenAtomic('0.01', '0.03', 9), '333333334');
  assert.throws(() => decimalToScaled('1.2345678', 6), /fractional/);
  assert.throws(() => decimalToScaled('1e9', 6), /invalid decimal/);
});

test('settlement response shape', () => {
  const ok = protocol.buildSettlementResponse({ success: true, transaction: 'sig', network: 'solana:x', payer: 'p', amount: '5' });
  assert.deepEqual(ok, { success: true, transaction: 'sig', network: 'solana:x', payer: 'p', amount: '5' });
  const bad = protocol.buildSettlementResponse({ success: false, network: 'solana:x', errorReason: 'insufficient_funds' });
  assert.equal(bad.transaction, '', 'spec: transaction must be "" on failure');
});
