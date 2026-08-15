'use strict';
/**
 * Fixtures for the product/paywall/server tests. House conventions: no
 * network (fake fetch impls + loopback HTTP only), throwaway in-test data,
 * mock facilitator so no real chain is ever touched.
 */

const crypto = require('node:crypto');
const os = require('node:os');
const path = require('node:path');

const cfgMod = require('../src/config');
const protocol = require('../src/protocol');
const { createGateway } = require('../src/server');
const { createGatewayStore } = require('../src/store/gateway');
const { createChainIndexStore } = require('../src/chain-index');
const { createReceiptSigner } = require('../src/receipts');

/**
 * REAL response captured live from the mainnet node's
 * rand.quantumRandomBytes on 2026-08-15 (QRNG recon §3b) — the QRNG tests
 * mock the RPC with this so the product's field passthrough is tested
 * against the shape the node actually produces (software fallback source,
 * non-attested software signer — the honest current truth).
 */
const QRNG_FIXTURE = {
  bytes_hex: '3b52641f28478b9def7d9c3460d128002729a6c1cd7b20af89ee218776b2c526',
  n: 32,
  source: {
    name: 'software-fallback',
    vendor: 'os',
    model: 'os.urandom CSPRNG',
    is_hardware: false,
    is_quantum: false,
    device_path: null,
    attested: false,
    notes: 'non-attested fallback for testing/degraded mode',
  },
  health: { passed: true, min_entropy_per_byte: 7.8078 },
  attestation: {
    alg: 'ed25519',
    backend: 'software',
    attested: false,
    public_key_hex: '8a2228f4bde8f72964a7ff4aa759f24932ae1cccd9462ad94ca6e508d6b87a64',
    digest_hex: 'e3148cfa00f4c51cd0ed757604ff5594c9c199cd7d6d8c91cc54d715cee70736',
    signature_hex: '4da7baf62f0c1665e1e7841dd70f48db3dd603d0c5b49734e21d96a55d24bc4e71ab7084f9840722af123a817612d33aa7423338b93c7bf7d9ead1313819cd08',
  },
};

/** Minimal fetch-Response lookalike over pre-built text. */
function textResponse(text, { status = 200, headers = {} } = {}) {
  const lower = {};
  for (const [k, v] of Object.entries(headers)) lower[k.toLowerCase()] = v;
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: { get: (k) => lower[String(k).toLowerCase()] ?? null },
    text: async () => text,
    json: async () => JSON.parse(text),
  };
}

/**
 * Fake fetch for the Animica node RPC: dispatches single or batch JSON-RPC
 * bodies to `handlers[method](params)`. A handler may return `{__raw:
 * '<json text>'}` to splice raw JSON into the response — that is how the
 * >2^53 tx-value fixture reaches the BigInt-safe parser as pure text (a JS
 * Number would corrupt it before serialization). `events` (optional array)
 * receives 'node:<method>' labels for ordering assertions.
 */
function fakeNodeFetch(handlers, { events } = {}) {
  return async (url, opts) => {
    const body = JSON.parse(opts.body);
    const one = (r) => {
      if (events) events.push(`node:${r.method}`);
      const h = handlers[r.method];
      if (!h) return JSON.stringify({ jsonrpc: '2.0', id: r.id, error: { code: -32601, message: 'Method not found' } });
      let result;
      try {
        result = h(r.params);
      } catch (e) {
        return JSON.stringify({ jsonrpc: '2.0', id: r.id, error: { code: -32000, message: e.message } });
      }
      if (result && result.__raw) return `{"jsonrpc":"2.0","id":${r.id},"result":${result.__raw}}`;
      return JSON.stringify({ jsonrpc: '2.0', id: r.id, result: result === undefined ? null : result });
    };
    const text = Array.isArray(body) ? '[' + body.map(one).join(',') + ']' : one(body);
    return textResponse(text);
  };
}

/** Raw-JSON text for a node block (mirrors the node's duplication). */
function rawBlockJson(height, { txValues = [], timestamp } = {}) {
  const txs = txValues
    .map((v, i) =>
      `{"hash":"0x${String(height).padStart(4, '0')}${String(i).padStart(60, 'a')}",` +
      `"from":"0x${'11'.repeat(32)}","to":"0x${'22'.repeat(32)}",` +
      `"gas":23100,"tip":2157169,"value":${v},"kind":0,"data":"0x"}`)
    .join(',');
  const header = `"number":${height},"hash":"0x${String(height).padStart(64, '0')}",` +
    `"parentHash":"0x${String(Math.max(0, height - 1)).padStart(64, '0')}",` +
    `"timestamp":${timestamp || 1786470000 + height},"chainId":1,"thetaMicro":26858281,` +
    `"nonce":363818152316,"roots":{"stateRoot":"0x${'00'.repeat(32)}","txsRoot":"0x${'00'.repeat(32)}"}`;
  return `{${header},"transactions":[${txs}],"txs":[${txs}],"receipts":[],"header":{${header}}}`;
}

/** Node handlers for a healthy chain of `head` blocks. */
function chainHandlers({ head = 200, txValues = {}, qrng = QRNG_FIXTURE } = {}) {
  return {
    'chain.getHead': () => ({ height: head, hash: '0x' + '00'.repeat(32) }),
    'chain.getBlockByHeight': (p) => {
      if (p.height > head) return null;
      return { __raw: rawBlockJson(p.height, { txValues: txValues[p.height] || [] }) };
    },
    'rand.quantumRandomBytes': () => qrng,
  };
}

/**
 * Mock facilitator client factory (x402 v2 §7 contract). Tracks calls,
 * simulates consumed EIP-3009 nonces so a replayed payment fails verify
 * the way the real facilitator would, and exposes knobs for failure modes.
 */
function mockFacilitator({ events } = {}) {
  const calls = { verify: [], settle: [] };
  const behavior = {
    verifyValid: true,
    invalidReason: 'invalid_exact_evm_payload_signature',
    settleSuccess: true,
    settleError: 'invalid_transaction_state',
    settleThrow: false,
    payer: '0x' + 'ab'.repeat(20),
  };
  const consumed = new Set();
  let settleCount = 0;
  const factory = () => ({
    async verify(paymentPayload, requirements) {
      calls.verify.push({ paymentPayload, requirements });
      if (events) events.push('fac:verify');
      const nonce = paymentPayload?.payload?.authorization?.nonce;
      if (nonce && consumed.has(nonce)) {
        return { isValid: false, invalidReason: 'authorization already used' };
      }
      if (!behavior.verifyValid) return { isValid: false, invalidReason: behavior.invalidReason };
      return { isValid: true, payer: behavior.payer };
    },
    async settle(paymentPayload, requirements) {
      calls.settle.push({ paymentPayload, requirements });
      if (events) events.push('fac:settle');
      if (behavior.settleThrow) throw new Error('settle transport down');
      if (!behavior.settleSuccess) {
        return { success: false, errorReason: behavior.settleError, transaction: '', network: requirements.network };
      }
      settleCount += 1;
      const nonce = paymentPayload?.payload?.authorization?.nonce;
      if (nonce) consumed.add(nonce);
      return {
        success: true,
        transaction: '0x' + String(settleCount).padStart(64, '0'),
        network: requirements.network,
        payer: behavior.payer,
        amount: requirements.amount,
      };
    },
    async supported() {
      return { kinds: [{ x402Version: 2, scheme: 'exact', network: 'eip155:8453' }], extensions: [], signers: {} };
    },
  });
  return { factory, calls, behavior, consumed, settled: () => settleCount };
}

const BASE = cfgMod.NETWORKS.BASE_MAINNET;

/**
 * A full gateway over loopback HTTP with everything mocked. Returns
 * { gw, baseUrl, fac, events, close() }.
 */
async function buildTestGateway({ overrides = {}, handlers, inferenceFetch, chainIndex, settlementStats, receiptSecret = 'test-receipt-secret' } = {}) {
  const events = [];
  const fac = mockFacilitator({ events });
  const nodeFetch = fakeNodeFetch(handlers || chainHandlers(), { events });
  const cfg = cfgMod.loadGatewayConfig({}, Object.assign({
    enabled: true,
    networkEvm: BASE,
    usdcAsset: cfgMod.USDC_DEFAULTS[BASE],
    basePayTo: '0x' + '77'.repeat(20),
    resourceBaseUrl: 'http://127.0.0.1:0',
    // GET /x402/stats reads the facilitator's settlement ledger read-only.
    // Point it at a path that does not exist so no test can ever open (or
    // report numbers from) the repo's real ./state/x402.db; the stats tests
    // override it with their own temporary ledger.
    settlementDbPath: path.join(os.tmpdir(), 'x402-tests-no-such-ledger.db'),
    // never offer the SVM lane in these tests
    wanmMint: '', wanmTreasury: '', wanmFeePayerPubkey: '', wanmUsdPrice: '',
  }, overrides));
  const quiet = { debug() {}, info() {}, warn() {}, error() {}, child() { return quiet; } };
  // ALWAYS in-memory, and the walker is never started: no test may write a
  // sqlite file or put a single request on the (mocked) node by itself.
  const index = chainIndex === undefined ? createChainIndexStore(':memory:') : chainIndex;
  const gw = createGateway({
    cfg,
    logger: quiet,
    fetchImpl: nodeFetch,
    inferenceFetch: inferenceFetch || nodeFetch,
    facilitatorClientFactory: fac.factory,
    gatewayStore: createGatewayStore(':memory:'),
    chainIndex: index,
    chainIndexer: null,
    settlementStats,
    receiptSigner: createReceiptSigner({ secret: receiptSecret }),
    sleep: async () => {},
    availabilityTtlMs: 0,
  });
  await new Promise((resolve) => gw.server.listen(0, '127.0.0.1', resolve));
  const baseUrl = `http://127.0.0.1:${gw.server.address().port}`;
  return {
    gw, baseUrl, fac, events, chainIndex: index,
    close: () => new Promise((resolve) => { gw.capacity.stop(); gw.server.close(resolve); }),
  };
}

async function request(baseUrl, path, { method = 'GET', headers = {}, body } = {}) {
  const res = await fetch(baseUrl + path, { method, headers, body });
  const buf = Buffer.from(await res.arrayBuffer());
  const text = buf.toString('utf8');
  let json = null;
  try { json = JSON.parse(text); } catch { /* not json */ }
  return { status: res.status, headers: res.headers, text, json, buf };
}

/** Build a PAYMENT-SIGNATURE header from a 402's offered terms. */
function paymentHeaderFrom(res402, { nonce } = {}) {
  const required = protocol.decodeHeader(res402.headers.get('payment-required'));
  const accepted = required.accepts[0];
  const payload = {
    x402Version: 2,
    resource: required.resource.url,
    accepted,
    payload: {
      signature: '0x' + '11'.repeat(65),
      authorization: {
        from: '0x' + 'ab'.repeat(20),
        to: accepted.payTo,
        value: accepted.amount,
        validAfter: '0',
        validBefore: '9999999999',
        nonce: nonce || '0x' + crypto.randomBytes(32).toString('hex'),
      },
    },
  };
  return protocol.encodeHeader(payload);
}

/** 402 round-trip: unpaid -> paid retry. Returns { first, paid }. */
async function paidRequest(baseUrl, path, { method = 'GET', headers = {}, body, nonce, idemKey } = {}) {
  const first = await request(baseUrl, path, { method, headers, body });
  if (first.status !== 402) return { first, paid: null };
  const payHeaders = Object.assign({}, headers, { 'payment-signature': paymentHeaderFrom(first, { nonce }) });
  if (idemKey) payHeaders['idempotency-key'] = idemKey;
  const paid = await request(baseUrl, path, { method, headers: payHeaders, body });
  return { first, paid };
}

module.exports = {
  QRNG_FIXTURE,
  BASE,
  textResponse,
  fakeNodeFetch,
  rawBlockJson,
  chainHandlers,
  mockFacilitator,
  buildTestGateway,
  request,
  paymentHeaderFrom,
  paidRequest,
};
