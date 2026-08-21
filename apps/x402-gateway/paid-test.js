#!/usr/bin/env node
'use strict';
/**
 * Pay each x402 endpoint for real and verify it delivers.
 *
 * The payer holds USDC but NO ETH — which is fine: x402 uses EIP-3009
 * transferWithAuthorization, so the payer only ever SIGNS and the facilitator
 * submits and pays gas. Budget is therefore pure USDC.
 */
const fs = require('node:fs');
const path = require('node:path');
const GW = '/root/animica/apps/x402-gateway';
const evm = require(path.join(GW, 'src/facilitator-evm/evm.js'));
const usdc = require(path.join(GW, 'src/facilitator-evm/usdc.js'));
const secp = require('@noble/secp256k1');

const BASE = 'https://animica.dev';
const USDC_ADDR = '0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913';
const CHAIN_ID = 8453;

const key = (() => {
  const txt = fs.readFileSync('/root/animica-x402-payer.env', 'utf8');
  const m = /SMOKE_PRIVATE_KEY=(.+)/.exec(txt);
  return Buffer.from(m[1].trim().replace(/^0x/, ''), 'hex');
})();
const PAYER = evm.privateKeyToAddress(key);
const DOMAIN = evm.domainSeparator({
  name: 'USD Coin', version: '2', chainId: CHAIN_ID, verifyingContract: USDC_ADDR,
});

function b64(obj) { return Buffer.from(JSON.stringify(obj), 'utf8').toString('base64'); }
function decodeHeader(h) { return JSON.parse(Buffer.from(h, 'base64').toString('utf8')); }

async function payOnce(ep) {
  const url = BASE + ep.path;
  const init = { method: ep.method, headers: { 'content-type': 'application/json' } };
  if (ep.method === 'POST') init.body = JSON.stringify(ep.body || {});

  // 1. unpaid -> 402 + the v2 offer in the header
  const r1 = await fetch(url, init);
  if (r1.status !== 402) return { ok: false, stage: '402', detail: `got ${r1.status}` };
  const pr = decodeHeader(r1.headers.get('payment-required'));
  const lane = pr.accepts.find((a) => String(a.network).startsWith('eip155:'));
  if (!lane) return { ok: false, stage: 'lane', detail: 'no USDC lane offered' };

  // 2. sign an EIP-3009 authorization for EXACTLY the offered terms
  const now = Math.floor(Date.now() / 1000);
  const auth = {
    from: PAYER,
    to: lane.payTo,
    value: BigInt(lane.amount),
    validAfter: 0n,
    validBefore: BigInt(now + 600),
    nonce: evm.bytesToHex(secp.etc.randomBytes(32)).toLowerCase(),
  };
  const signature = (() => {
    // signDigest wants RAW key bytes and returns components; the wire wants a
    // 65-byte r||s||v hex string (same as test/evm-helpers.js ethSign).
    const digest = usdc.transferAuthDigest(DOMAIN, auth);
    const sig = evm.signDigest(digest, key);
    return '0x' + Buffer.from(sig.rWord).toString('hex')
                + Buffer.from(sig.sWord).toString('hex')
                + sig.v.toString(16).padStart(2, '0');
  })();

  const paymentPayload = {
    x402Version: 2,
    resource: ep.path,
    accepted: lane,
    payload: {
      signature,
      authorization: {
        from: auth.from, to: auth.to, value: auth.value.toString(),
        validAfter: auth.validAfter.toString(), validBefore: auth.validBefore.toString(),
        nonce: auth.nonce,
      },
    },
  };

  // 3. retry WITH payment
  const init2 = { method: ep.method, headers: Object.assign({}, init.headers, { 'payment-signature': b64(paymentPayload) }) };
  if (init2.method === 'POST') init2.body = init.body;
  const r2 = await fetch(url, init2);
  const text = await r2.text();
  return {
    ok: r2.status === 200,
    stage: r2.status === 200 ? 'delivered' : 'paid-request',
    status: r2.status,
    spent: r2.status === 200 ? Number(lane.amount) : 0,
    settled: r2.headers.get('payment-response') ? 'yes' : 'no',
    body: text.slice(0, 160).replace(/\s+/g, ' '),
  };
}

const D64 = 'a'.repeat(64);
const ENDPOINTS = [
  { id: 'fetch_extract',   method: 'POST', path: '/x402/web/fetch',    body: { url: 'https://example.com' } },
  { id: 'embed_batch',     method: 'POST', path: '/x402/embed',        body: { texts: ['hello world'] } },
  { id: 'pq_verify',       method: 'POST', path: '/x402/pq/verify',    body: { alg_id: 4099, message: '00', signature: '00', public_key: '00' } },
  { id: 'price_oracle',    method: 'GET',  path: '/x402/oracle/price' },
  { id: 'mempool_feed',    method: 'GET',  path: '/x402/chain/mempool' },
  { id: 'notarize',        method: 'POST', path: '/x402/notarize',     body: { digest: D64, memo: 'paid-test' } },
  { id: 'blob_put',        method: 'POST', path: '/x402/blob',         body: { data: 'aGVsbG8geDQwMg==' } },
  { id: 'holder_snapshot', method: 'POST', path: '/x402/chain/holders', body: { limit: 3 } },
  { id: 'qrng',            method: 'GET',  path: '/x402/qrng/draw?bytes=8' },
  { id: 'random_int',      method: 'POST', path: '/x402/random/int',   body: { min: 1, max: 6, count: 3 } },
  { id: 'forecast',        method: 'POST', path: '/x402/forecast',      body: { question: 'Will Bitcoin trade above $200,000 before 2027?' } },
  { id: 'ask_url',         method: 'POST', path: '/x402/web/ask',      body: { url: 'https://example.com', question: 'What is this domain for?' } },
  // The cheapest paid route in the catalog, and therefore the one to use when
  // the payer's balance only covers a single settlement.
  { id: 'tier_standards',  method: 'POST', path: '/x402/v1/standard/chat/completions', body: { messages: [{ role: 'user', content: 'Reply with exactly: OK' }], max_tokens: 8 } },
];

// X402_ONLY=<id>[,<id>] runs just those endpoints. Real money moves here, so
// being able to spend ONE payment deliberately — to prove a facilitator change
// settles at all — matters more than running the whole sweep.
const ONLY = (process.env.X402_ONLY || '').split(',').map((s) => s.trim()).filter(Boolean);
const SELECTED = ONLY.length ? ENDPOINTS.filter((e) => ONLY.includes(e.id)) : ENDPOINTS;
if (ONLY.length && SELECTED.length !== ONLY.length) {
  const missing = ONLY.filter((id) => !ENDPOINTS.some((e) => e.id === id));
  console.error(`unknown endpoint id(s): ${missing.join(', ')}`);
  process.exit(1);
}

(async () => {
  console.log('payer   :', PAYER);
  let spent = 0, pass = 0;
  for (const ep of SELECTED) {
    await new Promise((res) => setTimeout(res, 4000));
    let r;
    // Space the calls: each verify makes ~3 Base RPC calls and the public
    // endpoint rate-limits, which surfaces as `unexpected_verify_error`
    // (rpc latest block / balanceOf failed) rather than anything wrong with
    // the payment. One retry on that specific reason.
    for (let attempt = 1; attempt <= 2; attempt++) {
      try { r = await payOnce(ep); } catch (e) { r = { ok: false, stage: 'threw', detail: e.message }; }
      if (r.ok || !String(r.body || '').includes('unexpected_verify_error')) break;
      await new Promise((res) => setTimeout(res, 6000));
    }
    spent += r.spent || 0;
    if (r.ok) pass++;
    const mark = r.ok ? ' PAID ' : ' FAIL ';
    console.log(`${mark} ${ep.id.padEnd(18)} ${String(r.status || r.stage).padEnd(6)} settled=${r.settled || '-'}  ${(r.detail || r.body || '').slice(0, 90)}`);
  }
  console.log(`\n${pass}/${SELECTED.length} delivered | spent ${spent} atomic ($${(spent / 1e6).toFixed(6)})`);
})();
