'use strict';
/**
 * End-to-end middleware tests: real HTTP servers on loopback (facilitator
 * with a mocked Solana RPC behind it + a gated resource server), real fetch,
 * no external network.
 */

const test = require('node:test');
const assert = require('node:assert/strict');
const http = require('node:http');

const protocol = require('../src/protocol');
const { createFacilitator, createFacilitatorServer } = require('../src/facilitator');
const { createX402Gate } = require('../src/middleware');
const { scene } = require('./helpers');

function listen(server) {
  return new Promise((resolve) => {
    server.listen(0, '127.0.0.1', () => resolve(`http://127.0.0.1:${server.address().port}`));
  });
}

/** Boot facilitator + gated demo-style server for one scene. */
async function boot(t, s, { enabled = true } = {}) {
  const facilitator = createFacilitator({
    network: s.network,
    signer: s.feePayerKp,
    rpc: s.rpc,
    sleep: async () => {},
    logger: { error() {} },
  });
  const facServer = createFacilitatorServer(facilitator, { logger: { error() {} } });
  const facUrl = await listen(facServer);

  const x402 = createX402Gate({
    enabled,
    resourceBaseUrl: 'http://127.0.0.1:0', // overwritten below once we know the port
    serviceName: 'Animica',
    svmFacilitatorUrl: facUrl,
    // wANM lane only — the EVM lane needs an external facilitator.
    basePayTo: '',
    usdcAsset: '',
    wanmMint: s.mint,
    wanmTreasury: s.treasury,
    wanmDecimals: 9,
    wanmUsdPrice: '0.05',
    wanmFeePayerPubkey: s.feePayerKp.publicKey,
    maxTimeoutSeconds: 60,
    logger: { error() {} },
  });

  const route = { path: '/paid/echo', priceUsd: '0.005', description: 'Echo', mimeType: 'application/json' };
  const gated = x402.gate(route, async (req) => ({
    status: 200,
    body: JSON.stringify({ echoed: true, url: req.url }),
  }));
  const appServer = http.createServer((req, res) => {
    if (req.url.startsWith('/paid/echo')) return gated(req, res);
    res.writeHead(404); res.end();
  });
  const appUrl = await listen(appServer);
  x402.cfg.resourceBaseUrl = appUrl;

  t.after(() => { facServer.close(); appServer.close(); });
  return { facUrl, appUrl, x402, route };
}

test('unpaid request gets a spec-shaped 402 with PAYMENT-REQUIRED header and v1 body', async (t) => {
  const s = scene({ priceAtomic: '100000000' });
  const { appUrl } = await boot(t, s);

  const res = await fetch(`${appUrl}/paid/echo`);
  assert.equal(res.status, 402);

  const header = res.headers.get('payment-required');
  assert.ok(header, 'PAYMENT-REQUIRED header must be present (v2 wire)');
  const pr = protocol.decodeHeader(header);
  assert.equal(pr.x402Version, 2);
  assert.equal(pr.resource.url, `${appUrl}/paid/echo`);
  assert.equal(pr.accepts.length, 1);
  assert.equal(pr.accepts[0].asset, s.mint);
  assert.equal(pr.accepts[0].amount, '100000000'); // $0.005 at $0.05/wANM
  assert.equal(pr.accepts[0].extra.feePayer, s.feePayerKp.publicKey);

  const v1 = await res.json();
  assert.equal(v1.x402Version, 1, 'body must carry the legacy v1 rendering');
  assert.equal(v1.accepts[0].maxAmountRequired, '100000000');
  assert.equal(v1.accepts[0].network, 'solana-devnet');
});

test('v2 PAYMENT-SIGNATURE payment: verified, served, settled, receipt header attached', async (t) => {
  const s = scene({ priceAtomic: '100000000' });
  const { appUrl } = await boot(t, s);

  // Client flow: read the offer, build + partially sign the transfer.
  const offer = protocol.decodeHeader((await fetch(`${appUrl}/paid/echo`)).headers.get('payment-required'));
  const accepted = offer.accepts[0];
  const paymentPayload = {
    x402Version: 2,
    accepted,
    payload: { transaction: s.tx({ amount: BigInt(accepted.amount) }) },
  };

  const res = await fetch(`${appUrl}/paid/echo?q=1`, {
    headers: { 'payment-signature': protocol.encodeHeader(paymentPayload) },
  });
  assert.equal(res.status, 200);
  assert.deepEqual(await res.json(), { echoed: true, url: '/paid/echo?q=1' });

  const receipt = protocol.decodeHeader(res.headers.get('payment-response'));
  assert.equal(receipt.success, true);
  assert.equal(receipt.network, s.network);
  assert.equal(receipt.payer, s.payerKp.publicKey);
  assert.ok(receipt.transaction.length > 0);
  assert.equal(s.rpc.sent.length, 1, 'exactly one settlement broadcast');
});

test('v1 X-PAYMENT payment: legacy wire works and gets X-PAYMENT-RESPONSE', async (t) => {
  const s = scene({ priceAtomic: '100000000' });
  const { appUrl } = await boot(t, s);

  const xPayment = protocol.encodeHeader({
    x402Version: 1,
    scheme: 'exact',
    network: 'solana-devnet',
    payload: { transaction: s.tx({ amount: 100000000n }) },
  });
  const res = await fetch(`${appUrl}/paid/echo`, { headers: { 'x-payment': xPayment } });
  assert.equal(res.status, 200);
  const receipt = protocol.decodeHeader(res.headers.get('x-payment-response'));
  assert.equal(receipt.success, true);
});

test('tampered accepted terms are refused before any facilitator call', async (t) => {
  const s = scene({ priceAtomic: '100000000' });
  const { appUrl } = await boot(t, s);

  const offer = protocol.decodeHeader((await fetch(`${appUrl}/paid/echo`)).headers.get('payment-required'));
  const cheaper = Object.assign({}, offer.accepts[0], { amount: '1' }); // client edits the price
  const res = await fetch(`${appUrl}/paid/echo`, {
    headers: {
      'payment-signature': protocol.encodeHeader({
        x402Version: 2, accepted: cheaper, payload: { transaction: s.tx({ amount: 1n }) },
      }),
    },
  });
  assert.equal(res.status, 402);
  const rpcMethods = s.rpc.calls.map((c) => c.method);
  assert.deepEqual(rpcMethods, [], 'tampered terms must never reach verify/settle');
});

test('invalid payment (wrong amount in tx) is re-offered a 402, never served', async (t) => {
  const s = scene({ priceAtomic: '100000000' });
  const { appUrl } = await boot(t, s);
  const offer = protocol.decodeHeader((await fetch(`${appUrl}/paid/echo`)).headers.get('payment-required'));
  const res = await fetch(`${appUrl}/paid/echo`, {
    headers: {
      'payment-signature': protocol.encodeHeader({
        x402Version: 2, accepted: offer.accepts[0], payload: { transaction: s.tx({ amount: 5n }) },
      }),
    },
  });
  assert.equal(res.status, 402);
  const v1 = await res.json();
  assert.match(v1.error, /invalid_exact_svm_payload_amount/);
  assert.equal(s.rpc.sent.length, 0, 'nothing may be broadcast for an invalid payment');
});

test('kill switch: ANM_X402_ENABLED off means 503, not free service', async (t) => {
  const s = scene();
  const { appUrl } = await boot(t, s, { enabled: false });
  const res = await fetch(`${appUrl}/paid/echo`);
  assert.equal(res.status, 503);
  assert.equal((await res.json()).error, 'x402_disabled');
});

test('facilitator /supported over HTTP matches the direct call', async (t) => {
  const s = scene();
  const { facUrl } = await boot(t, s);
  const sup = await (await fetch(`${facUrl}/supported`)).json();
  assert.deepEqual(sup.kinds[0], { x402Version: 2, scheme: 'exact', network: s.network, extra: { feePayer: s.feePayerKp.publicKey } });
  assert.deepEqual(sup.signers[s.network], [s.feePayerKp.publicKey]);
});
