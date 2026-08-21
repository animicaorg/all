#!/usr/bin/env node
'use strict';
/**
 * Seed the CDP Bazaar by settling ONE real payment per product.
 *
 * WHY THIS EXISTS. CDP indexes a resource into the Bazaar only after ITS
 * facilitator settles a payment for that resource — per resource, not per
 * origin. A correct discovery extension gets a product listed exactly when
 * someone pays for it, and with no external demand yet that means paying for it
 * ourselves, once each. `paid-test.js` proves delivery on 13 endpoints; this
 * walks the whole live catalog.
 *
 * REAL MONEY MOVES. The payer wallet holds a fixed float, so the run is capped
 * two ways: a hard total budget, and a per-product ceiling that skips the few
 * expensive products outright (credits_buy at $0.50 and crawl_pass_100 at
 * $1.00 would consume the entire float between them for two listings). Both are
 * printed, and the run stops rather than overspending.
 *
 * WHAT SUCCESS LOOKS LIKE. Not a 200 — a SETTLEMENT. A product whose downstream
 * fails after settling is still indexed, and a product that 402s or 400s is
 * not. The report therefore counts settlements, and separately shows CDP's own
 * verdict on the discovery extension, read from the gateway's log of the
 * EXTENSION-RESPONSES header: `processing` means accepted and queued,
 * `rejected` means the block was refused and no amount of paying will list it.
 *
 *   node bin/seed-bazaar.js [--budget 0.30] [--max-price 0.02] [--dry-run]
 */
const fs = require('node:fs');
const path = require('node:path');
const { execFileSync } = require('node:child_process');

const GW = path.resolve(__dirname, '..');
const evm = require(path.join(GW, 'src/facilitator-evm/evm.js'));
const usdc = require(path.join(GW, 'src/facilitator-evm/usdc.js'));
const secp = require('@noble/secp256k1');

const BASE = process.env.X402_BASE || 'https://animica.dev';
const USDC_ADDR = '0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913';
const CHAIN_ID = 8453;

function arg(name, dflt) {
  const i = process.argv.indexOf(`--${name}`);
  return i >= 0 && process.argv[i + 1] ? process.argv[i + 1] : dflt;
}
const BUDGET_USD = Number(arg('budget', '0.30'));
const MAX_PRICE_USD = Number(arg('max-price', '0.02'));
const DRY_RUN = process.argv.includes('--dry-run');
// Re-seeding only the products CDP rejected costs nothing for the ones it
// already accepted — paying twice for the same listing buys nothing.
const ONLY = (arg('only', '') || '').split(',').map((s) => s.trim()).filter(Boolean);

const key = (() => {
  const m = /SMOKE_PRIVATE_KEY=(.+)/.exec(fs.readFileSync('/root/animica-x402-payer.env', 'utf8'));
  return Buffer.from(m[1].trim().replace(/^0x/, ''), 'hex');
})();
const PAYER = evm.privateKeyToAddress(key);
const DOMAIN = evm.domainSeparator({ name: 'USD Coin', version: '2', chainId: CHAIN_ID, verifyingContract: USDC_ADDR });

// The request bodies live in src/discovery/request-examples.js, because the
// Bazaar listings publish the very same values — two copies would drift, and a
// drifted example is one an agent pays to discover is wrong.
const { REQUEST_EXAMPLES, QUERY_EXAMPLES } = require(path.join(GW, 'src/discovery/request-examples'));

/**
 * Products taking NO request body. Listed explicitly rather than inferred from
 * a missing entry, so a product that simply lost its example is reported as
 * unseedable instead of being silently paid for with an empty body.
 */
// `credits_buy` is NOT here: it takes an optional `label`, and the Bazaar
// listing advertises one as its example. Sending an empty body settled fine but
// minted an unlabelled voucher, so the example we publish and the request we
// actually make disagreed — exactly the drift this table exists to prevent.
const NO_BODY = new Set(['qrng', 'price_oracle', 'mempool_feed', 'bulk_chain']);

/** Products whose address arguments must be REAL, taken from the captured
 *  sample rather than typed out — a bech32m checksum does not survive a
 *  hand-copied address, and the endpoint 400s before any payment. */
const { SAMPLES } = require(path.join(GW, 'src/discovery/samples'));
const SAMPLE_ADDRESS = ((SAMPLES.chain_batch_balances || {}).body || {}).addresses;
const EXTRA_BODIES = SAMPLE_ADDRESS && SAMPLE_ADDRESS.length ? {
  chain_batch_balances: { addresses: [SAMPLE_ADDRESS[0]] },
  chain_address_history: { address: SAMPLE_ADDRESS[0], limit: 3 },
} : {};

const BODIES = Object.assign({}, REQUEST_EXAMPLES, EXTRA_BODIES);
for (const id of NO_BODY) BODIES[id] = null;

function b64(o) { return Buffer.from(JSON.stringify(o), 'utf8').toString('base64'); }

/** Pay one product once. Returns what happened, including whether it SETTLED. */
async function payOnce(ep) {
  const url = BASE + ep.path;
  const init = { method: ep.method, headers: { 'content-type': 'application/json' } };
  if (ep.method === 'POST') init.body = JSON.stringify(ep.body || {});

  const r1 = await fetch(url, init);
  if (r1.status !== 402) {
    // 400 = our body is wrong; 503 = the product is genuinely unavailable
    // (media needs a GPU miner online). Neither charges anything.
    return { settled: false, stage: `pre-402 ${r1.status}`, detail: (await r1.text()).slice(0, 110).replace(/\s+/g, ' ') };
  }
  const pr = JSON.parse(Buffer.from(r1.headers.get('payment-required'), 'base64').toString('utf8'));
  const lane = pr.accepts.find((a) => String(a.network).startsWith('eip155:'));
  if (!lane) return { settled: false, stage: 'no-usdc-lane' };

  const now = Math.floor(Date.now() / 1000);
  const auth = {
    from: PAYER,
    to: lane.payTo,
    value: BigInt(lane.amount),
    validAfter: 0n,
    validBefore: BigInt(now + 600),
    nonce: evm.bytesToHex(secp.etc.randomBytes(32)).toLowerCase(),
  };
  const sig = evm.signDigest(usdc.transferAuthDigest(DOMAIN, auth), key);
  const signature = '0x' + Buffer.from(sig.rWord).toString('hex')
    + Buffer.from(sig.sWord).toString('hex') + sig.v.toString(16).padStart(2, '0');

  const paymentPayload = {
    x402Version: 2,
    resource: ep.path,
    accepted: lane,
    payload: {
      signature,
      authorization: {
        from: auth.from, to: auth.to, value: auth.value.toString(),
        validAfter: auth.validAfter.toString(), validBefore: auth.validBefore.toString(), nonce: auth.nonce,
      },
    },
  };
  const init2 = { method: ep.method, headers: Object.assign({}, init.headers, { 'payment-signature': b64(paymentPayload) }) };
  if (init2.method === 'POST') init2.body = init.body;
  const r2 = await fetch(url, init2);
  const text = await r2.text();
  // A settled payment is the thing that indexes. Delivery can still fail
  // afterwards (the payer gets a signed receipt) and the listing still happens.
  const settled = !!r2.headers.get('payment-response');
  return {
    settled,
    status: r2.status,
    spentAtomic: settled ? Number(lane.amount) : 0,
    stage: settled ? (r2.status === 200 ? 'delivered' : 'settled-not-delivered') : `unsettled ${r2.status}`,
    detail: r2.status === 200 ? '' : text.slice(0, 110).replace(/\s+/g, ' '),
  };
}

/** CDP's verdict on the discovery extension, from the gateway's own log. */
function cdpVerdicts(sinceIso) {
  try {
    const out = execFileSync('journalctl', ['-u', 'animica-x402.service', '--since', sinceIso, '--no-pager', '-o', 'cat'], { encoding: 'utf8' });
    const seen = new Set();
    for (const line of out.split('\n')) {
      if (!line.includes('facilitator_extension_responses')) continue;
      try {
        const j = JSON.parse(line);
        const b = j.responses && j.responses.bazaar;
        if (b) seen.add(b.status === 'rejected' ? `rejected: ${b.rejectedReason}` : b.status);
      } catch { /* a partial line is not a verdict */ }
    }
    return [...seen];
  } catch { return []; }
}

(async () => {
  const cat = await (await fetch(`${BASE}/.well-known/x402`)).json();
  const products = cat.products || [];
  // Chain export needs a real height window; the head moves, so read it now.
  let head = null;
  try {
    const h = await (await fetch('https://explorer.animica.org/api/head')).json();
    // The explorer nests it: {head:{height,...}}. Reading h.height gives
    // undefined and silently skips bulk_chain, so take the nested value first.
    head = Number((h.head && h.head.height) || h.height || 0) || null;
  } catch { /* bulk_chain is skipped below if this fails */ }

  // journalctl --since reads LOCAL time; an ISO/UTC string silently widens the
  // window by the UTC offset and reports verdicts from earlier runs as this
  // run's. Format in local time so the report describes what just happened.
  const t0 = new Date(Date.now() - 5000);
  const pad = (n) => String(n).padStart(2, '0');
  const startedAt = `${t0.getFullYear()}-${pad(t0.getMonth() + 1)}-${pad(t0.getDate())} `
    + `${pad(t0.getHours())}:${pad(t0.getMinutes())}:${pad(t0.getSeconds())}`;
  console.log(`payer      : ${PAYER}`);
  console.log(`budget     : $${BUDGET_USD.toFixed(4)}  |  per-product ceiling $${MAX_PRICE_USD.toFixed(4)}${DRY_RUN ? '  (DRY RUN)' : ''}`);
  console.log(`products   : ${products.length}\n`);

  let spent = 0, settledN = 0, skipped = 0, failed = 0;
  const tooDear = [], unseedable = [], failures = [];

  for (const p of products) {
    const price = Number(p.price);
    if (ONLY.length && !ONLY.includes(p.id)) { skipped++; continue; }
    if (!(p.id in BODIES)) { unseedable.push(`${p.id} (no known-good request body)`); skipped++; continue; }
    if (price > MAX_PRICE_USD) { tooDear.push(`${p.id} $${price.toFixed(4)}`); skipped++; continue; }
    if (spent + price > BUDGET_USD) { tooDear.push(`${p.id} $${price.toFixed(4)} (budget exhausted)`); skipped++; continue; }

    let epPath = p.path;
    if (p.id === 'bulk_chain') {
      if (!head) { unseedable.push('bulk_chain (could not read chain head)'); skipped++; continue; }
      // The exportable bound trails the head by a finality margin (6 blocks);
      // asking for a window inside it is a 400 before any payment.
      epPath = `/x402/chain/export?from=${head - 12}&to=${head - 10}&format=json`;
    }
    if (p.id === 'qrng') epPath = '/x402/qrng/draw?bytes=8';

    const ep = { id: p.id, method: p.method || 'POST', path: epPath, body: BODIES[p.id] };
    if (DRY_RUN) { console.log(` DRY   ${p.id.padEnd(22)} $${price.toFixed(4)} ${ep.method} ${ep.path}`); spent += price; continue; }

    // Space the calls: each verify makes several Base RPC calls and the public
    // endpoint rate-limits, which surfaces as a verify error, not a payment bug.
    await new Promise((r) => setTimeout(r, 3500));
    let res;
    try { res = await payOnce(ep); } catch (e) { res = { settled: false, stage: 'threw', detail: e.message }; }
    spent += (res.spentAtomic || 0) / 1e6;
    if (res.settled) settledN++; else { failed++; failures.push(`${p.id}: ${res.stage} ${res.detail || ''}`.trim()); }
    console.log(` ${res.settled ? 'SETTLED' : 'no-pay '} ${p.id.padEnd(22)} $${price.toFixed(4)} ${String(res.stage).padEnd(24)} ${(res.detail || '').slice(0, 60)}`);
  }

  console.log(`\nsettled ${settledN} | unsettled ${failed} | skipped ${skipped} | spent $${spent.toFixed(6)}`);
  if (tooDear.length) console.log(`\nskipped as too expensive / over budget:\n  ${tooDear.join('\n  ')}`);
  if (unseedable.length) console.log(`\nskipped as unseedable:\n  ${unseedable.join('\n  ')}`);
  if (failures.length) console.log(`\nunsettled (nothing charged, nothing indexed):\n  ${failures.join('\n  ')}`);
  if (!DRY_RUN) {
    const v = cdpVerdicts(startedAt);
    console.log(`\nCDP verdict on the discovery extension: ${v.length ? v.join(', ') : '(no EXTENSION-RESPONSES seen)'}`);
  }
})();
