'use strict';
/**
 * ADVERSARIAL REVIEW 3/3 — vector (9) secret leakage.
 *
 * Findings surfaced:
 *   1. POSITIVE controls (no leak): the facilitator key never echoes on a
 *      malformed value; structured error paths and the log redactor never
 *      emit the payer's authorization signature.
 *   2. FINDING: the operator CLI `animica-x402 payment get` prints the
 *      `raw_tx` column verbatim (both --json and the plain dump). raw_tx is
 *      a fully-signed, self-contained, rebroadcastable settlement tx that
 *      embeds the payer's EIP-3009 signature — exactly the "signed payload
 *      containing replayable material" the logging policy redacts (key.js
 *      REDACT_FIELDS matches /raw_tx/). The CLI bypasses that policy and its
 *      own docstring ("never prints secrets (the DBs hold none)") is wrong.
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const http = require('node:http');
const { execFileSync } = require('node:child_process');

const { redact } = require('../src/facilitator-evm/key');
const { loadSigner } = require('../src/facilitator-evm/key');
const { createStore } = require('../src/store');
const { scene } = require('./evm-helpers');
const { createEvmFacilitatorServer } = require('../src/facilitator-evm/server');

const BIN = path.join(__dirname, '..', 'bin', 'animica-x402');

/* ---- POSITIVE: no key echo, no signature in errors/redacted logs ---- */

test('loadSigner never echoes a malformed key value', () => {
  const secret = 'deadbeef'.repeat(9); // 72 hex chars — wrong length, "looks" keyish
  try {
    loadSigner(secret);
    assert.fail('should have thrown');
  } catch (e) {
    assert.ok(!e.message.includes(secret), 'error message must not contain the candidate key');
    assert.match(e.message, /malformed/);
  }
});

test('log redactor scrubs raw_tx / signature / private fields', () => {
  const out = redact({
    payer: '0xabc', amount: '10000',
    signature: '0x' + '11'.repeat(65),
    raw_tx: '0x02f8...signed',
    authorization: { from: '0xabc', nonce: '0x' + '22'.repeat(32) },
    nested: { private_key: '0x' + '33'.repeat(32), api_key: 'sk-xyz' },
  });
  assert.equal(out.signature, '[REDACTED]');
  assert.equal(out.raw_tx, '[REDACTED]');
  assert.equal(out.authorization, '[REDACTED]');
  assert.equal(out.nested.private_key, '[REDACTED]');
  assert.equal(out.nested.api_key, '[REDACTED]');
  assert.equal(out.payer, '0xabc'); // non-secret passthrough
});

test('facilitator HTTP error/verify paths never return the payer signature', async () => {
  const sc = scene();
  const server = createEvmFacilitatorServer(sc.facilitator);
  await new Promise((r) => server.listen(0, '127.0.0.1', r));
  const base = `http://127.0.0.1:${server.address().port}`;
  try {
    // valid body -> the on-wire signature we sent
    const body = sc.payment();
    const sig = body.paymentPayload.payload.signature;

    // Force the verify path AND a wrong-amount reject; inspect responses.
    const good = await (await fetch(`${base}/verify`, {
      method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body),
    })).text();
    const bad = await (await fetch(`${base}/verify`, {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify(sc.payment({ amount: '20000' })),
    })).text();

    for (const [label, text] of [['verify-ok', good], ['verify-reject', bad]]) {
      assert.ok(!text.includes(sig.slice(2)), `${label} response must not echo the signature`);
      assert.ok(!/[0-9a-f]{130}/i.test(text), `${label} response has no 65-byte sig hex`);
    }
  } finally {
    server.close();
  }
});

/* ---- FINDING: the CLI prints raw_tx (replayable signed settlement tx) ---- */

test('`animica-x402 payment get` redacts the signed raw_tx unless explicitly demanded', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'x402-cli-leak-'));
  const dbPath = path.join(dir, 'x402.db');
  // A distinctive fake signed tx standing in for a real submitting/dropped row.
  const RAW_TX = '0x02f8b0018203e8843b9aca008504a817c8008252089442' + 'ab'.repeat(40) + 'c0de';
  try {
    const store = createStore(dbPath);
    store.claim({
      paymentId: 'pay_leak', authorizationHash: '0x' + 'a1'.repeat(32),
      payer: '0x' + '11'.repeat(20), asset: '0x' + '22'.repeat(20),
      network: 'eip155:8453', amount: 10000n, resource: '/x402/qrng',
      expiresAt: 2_000_000_000, authNonce: '0x' + 'cd'.repeat(32),
    });
    // A settlement in flight: raw_tx is populated and NOT yet consumed on-chain.
    store.markSubmitting('pay_leak', { txHash: '0xdead', txNonce: 3, rawTx: RAW_TX });
    store.close();

    const env = Object.assign({}, process.env, { X402_DB_PATH: dbPath });
    const jsonOut = execFileSync(process.execPath, [BIN, 'payment', 'get', 'pay_leak', '--json'], { env, encoding: 'utf8' });
    const plainOut = execFileSync(process.execPath, [BIN, 'payment', 'get', 'pay_leak'], { env, encoding: 'utf8' });

    // FIXED: neither output path prints the rebroadcastable signed tx, which
    // embeds the payer's EIP-3009 signature.
    assert.ok(!jsonOut.includes(RAW_TX), 'CLI --json does not dump the signed raw_tx');
    assert.ok(!plainOut.includes(RAW_TX), 'CLI plain output does not dump the signed raw_tx');
    assert.match(jsonOut, /redacted/i, 'the field is present but summarised');
    // Everything an operator actually needs for reconciliation is still shown.
    assert.ok(plainOut.includes('0xdead'), 'the settlement tx HASH is still visible');

    // The escape hatch exists for genuine recovery work and must be explicit.
    const unsafe = execFileSync(
      process.execPath, [BIN, 'payment', 'get', 'pay_leak', '--json', '--unsafe-show-raw'],
      { env, encoding: 'utf8' });
    assert.ok(unsafe.includes(RAW_TX), '--unsafe-show-raw reveals it deliberately');
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});
