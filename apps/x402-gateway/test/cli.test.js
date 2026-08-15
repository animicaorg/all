'use strict';
/**
 * bin/animica-x402 — operator CLI against real (temp) DB files, with a
 * loopback mock EVM RPC for `reconcile`. Spawns the actual executable.
 */

const { test, before, after } = require('node:test');
const assert = require('node:assert/strict');
const { spawnSync } = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const http = require('node:http');

const { createStore } = require('../src/store');
const { createGatewayStore } = require('../src/store/gateway');
const usdc = require('../src/facilitator-evm/usdc');

const BIN = path.join(__dirname, '..', 'bin', 'animica-x402');
const PAYER = '0x' + 'ab'.repeat(20);
const ASSET = '0x' + 'cc'.repeat(20);
const NONCE = '0x' + '01'.repeat(32);
const GOOD_TX = '0x' + 'f1'.repeat(32);
const MISSING_TX = '0x' + 'f2'.repeat(32);

let dir;
let rpcServer;
let rpcUrl;
let incidentId;

function cliEnv() {
  return Object.assign({}, process.env, {
    X402_DB_PATH: path.join(dir, 'x402.db'),
    X402_GATEWAY_DB_PATH: path.join(dir, 'x402-gateway.db'),
    X402_RPC_URL: rpcUrl,
  });
}

function runCli(args) {
  const r = spawnSync(process.execPath, [BIN, ...args], { encoding: 'utf8', env: cliEnv() });
  return { code: r.status, stdout: r.stdout, stderr: r.stderr };
}

/** Async spawn — required when the CLI must reach the in-process mock RPC
 * server (spawnSync would block the event loop that serves it). */
function runCliAsync(args) {
  return new Promise((resolve) => {
    const child = require('node:child_process').execFile(
      process.execPath, [BIN, ...args], { encoding: 'utf8', env: cliEnv() },
      (err, stdout, stderr) => resolve({ code: err ? err.code : 0, stdout, stderr })
    );
    void child;
  });
}

before(async () => {
  dir = fs.mkdtempSync(path.join(os.tmpdir(), 'x402-cli-'));

  // Facilitator ledger with a settled+verifiable, a settled+unfindable and
  // a failed payment (real store code writes the rows).
  const store = createStore(path.join(dir, 'x402.db'));
  store.claim({
    paymentId: 'pay_good', authorizationHash: '0x' + 'a1'.repeat(32), payer: PAYER, asset: ASSET,
    network: 'eip155:8453', amount: '10000', resource: '/x402/qrng/draw', expiresAt: 9999999999, authNonce: NONCE,
  });
  store.markSettled('pay_good', { txHash: GOOD_TX, gasSpentWei: '540000000000' });
  store.claim({
    paymentId: 'pay_lost', authorizationHash: '0x' + 'a2'.repeat(32), payer: PAYER, asset: ASSET,
    network: 'eip155:8453', amount: '50000', resource: '/x402/chain/export', expiresAt: 9999999999, authNonce: '0x' + '02'.repeat(32),
  });
  store.markSettled('pay_lost', { txHash: MISSING_TX, gasSpentWei: '540000000000' });
  store.claim({
    paymentId: 'pay_failed', authorizationHash: '0x' + 'a3'.repeat(32), payer: PAYER, asset: ASSET,
    network: 'eip155:8453', amount: '10000', resource: '/x402/qrng/draw', expiresAt: 9999999999, authNonce: '0x' + '03'.repeat(32),
  });
  store.markFailed('pay_failed', 'invalid_transaction_state');
  store.close();

  const gstore = createGatewayStore(path.join(dir, 'x402-gateway.db'));
  incidentId = gstore.addIncident({
    paymentId: GOOD_TX, settlementTx: GOOD_TX, payer: PAYER, resource: '/x402/chain/blocks',
    amount: '50000', network: 'eip155:8453', kind: 'downstream_failed', error: 'boom',
    receipt: { payment_id: GOOD_TX, signature: 'aa' },
  });
  gstore.close();

  // Mock EVM RPC: a receipt only for GOOD_TX, with the AuthorizationUsed log.
  rpcServer = http.createServer((req, res) => {
    let raw = '';
    req.on('data', (c) => (raw += c));
    req.on('end', () => {
      const body = JSON.parse(raw);
      let result = null;
      if (body.method === 'eth_getTransactionReceipt' && body.params[0] === GOOD_TX) {
        result = {
          transactionHash: GOOD_TX,
          status: '0x1',
          logs: [{
            address: ASSET,
            topics: [usdc.TOPICS.authorizationUsed, '0x' + '00'.repeat(12) + PAYER.slice(2), NONCE],
            data: '0x',
          }],
        };
      }
      res.writeHead(200, { 'content-type': 'application/json' });
      res.end(JSON.stringify({ jsonrpc: '2.0', id: body.id, result }));
    });
  });
  await new Promise((resolve) => rpcServer.listen(0, '127.0.0.1', resolve));
  rpcUrl = `http://127.0.0.1:${rpcServer.address().port}`;
});

after(async () => {
  await new Promise((resolve) => rpcServer.close(resolve));
  fs.rmSync(dir, { recursive: true, force: true });
});

test('settlements list: all rows and --status filter', () => {
  const all = runCli(['settlements', 'list', '--json']);
  assert.equal(all.code, 0, all.stderr);
  const rows = JSON.parse(all.stdout);
  assert.equal(rows.length, 3);
  assert.deepEqual(rows.map((r) => r.payment_id).sort(), ['pay_failed', 'pay_good', 'pay_lost']);
  assert.equal(rows.find((r) => r.payment_id === 'pay_good').amount_usdc, '0.01');

  const failed = JSON.parse(runCli(['settlements', 'list', '--status', 'failed', '--json']).stdout);
  assert.equal(failed.length, 1);
  assert.equal(failed[0].payment_id, 'pay_failed');
  assert.equal(failed[0].error, 'invalid_transaction_state');
});

test('payment get: by id and by tx hash, never a secret in sight', () => {
  const byId = runCli(['payment', 'get', 'pay_good', '--json']);
  assert.equal(byId.code, 0, byId.stderr);
  const row = JSON.parse(byId.stdout);
  assert.equal(row.status, 'settled');
  assert.equal(row.settlement_tx_hash, GOOD_TX);
  const byTx = JSON.parse(runCli(['payment', 'get', GOOD_TX, '--json']).stdout);
  assert.equal(byTx.payment_id, 'pay_good');
  const missing = runCli(['payment', 'get', 'pay_nope']);
  assert.equal(missing.code, 1);
});

test('revenue: settled sums per product, failed rows never counted', () => {
  const r = runCli(['revenue', '--json']);
  assert.equal(r.code, 0, r.stderr);
  const rows = JSON.parse(r.stdout);
  const byResource = Object.fromEntries(rows.map((x) => [x.resource, x]));
  assert.equal(byResource['/x402/qrng/draw'].settled, 1); // pay_failed excluded
  assert.equal(byResource['/x402/qrng/draw'].revenue_usdc, '0.01');
  assert.equal(byResource['/x402/chain/export'].revenue_usdc, '0.05');
  // --since far in the future excludes everything
  const later = JSON.parse(runCli(['revenue', '--since', String(Math.floor(Date.now() / 1000) + 3600), '--json']).stdout);
  assert.equal(later.length, 0);
});

test('gas report: totals include both settlements', () => {
  const r = runCli(['gas', 'report', '--json']);
  assert.equal(r.code, 0, r.stderr);
  const rows = JSON.parse(r.stdout);
  assert.equal(rows.length, 1); // both today
  assert.equal(rows[0].settlements, 2);
  assert.equal(rows[0].gas_wei, String(2n * 540000000000n));
});

test('reconcile: chain receipt cross-check flags the unfindable settlement, exit code 1', async () => {
  const r = await runCliAsync(['reconcile', '--json']);
  assert.equal(r.code, 1, 'mismatches must fail the exit code');
  const rows = JSON.parse(r.stdout);
  const byId = Object.fromEntries(rows.map((x) => [x.payment_id, x]));
  assert.match(byId.pay_good.verdict, /^OK/);
  assert.equal(byId.pay_lost.verdict, 'NO_RECEIPT');
});

test('incidents: list and resolve workflow', () => {
  const list = runCli(['incidents', 'list', '--status', 'open', '--json']);
  assert.equal(list.code, 0, list.stderr);
  const rows = JSON.parse(list.stdout);
  assert.equal(rows.length, 1);
  assert.equal(rows[0].incident_id, incidentId);
  assert.equal(rows[0].kind, 'downstream_failed');

  const resolve = runCli(['incidents', 'resolve', incidentId, '--status', 'refunded']);
  assert.equal(resolve.code, 0, resolve.stderr);
  const open = JSON.parse(runCli(['incidents', 'list', '--status', 'open', '--json']).stdout);
  assert.equal(open.length, 0);
  const refunded = JSON.parse(runCli(['incidents', 'list', '--status', 'refunded', '--json']).stdout);
  assert.equal(refunded[0].incident_id, incidentId);
});

test('commitments: list/get/prune, and a sealed secret is never printed', () => {
  const gstore = createGatewayStore(path.join(dir, 'x402-gateway.db'));
  const nowSec = Math.floor(Date.now() / 1000);
  const draw = { bytes_hex: 'aa'.repeat(32), n: 32, source: { name: 'software-fallback' }, attestation: { digest_hex: 'bb'.repeat(32), attested: false } };
  gstore.putCommitment({
    commitId: 'rc_' + '11'.repeat(16), commitment: 'cc'.repeat(32), algorithm: 'sha3_256(secret||salt)',
    kind: 'commit', requestId: 'open-one', memo: 'openable', secretHex: 'de'.repeat(32), saltHex: 'ad'.repeat(32),
    draw, revealAfter: nowSec - 10, createdAt: nowSec - 60,
  });
  gstore.putCommitment({
    commitId: 'rc_' + '22'.repeat(16), commitment: 'dd'.repeat(32), algorithm: 'sha3_256(secret||salt)',
    kind: 'commit', requestId: 'sealed-one', memo: 'sealed', secretHex: 'be'.repeat(32), saltHex: 'ef'.repeat(32),
    draw, revealAfter: nowSec + 3600, createdAt: nowSec,
  });
  gstore.close();

  const list = runCli(['commitments', 'list', '--json']);
  assert.equal(list.code, 0, list.stderr);
  const rows = JSON.parse(list.stdout);
  assert.equal(rows.length, 2);
  const states = Object.fromEntries(rows.map((r) => [r.request_id, r.state]));
  assert.equal(states['open-one'], 'open');
  assert.equal(states['sealed-one'], 'sealed');
  // list never carries secret material at all
  assert.doesNotMatch(list.stdout, /de{10}|be{10}/);
  const sealedOnly = JSON.parse(runCli(['commitments', 'list', '--state', 'sealed', '--json']).stdout);
  assert.equal(sealedOnly.length, 1);
  assert.equal(sealedOnly[0].request_id, 'sealed-one');

  // get: the sealed one withholds, the open one (already public at the free
  // reveal route) discloses
  const sealed = JSON.parse(runCli(['commitments', 'get', 'rc_' + '22'.repeat(16), '--json']).stdout);
  assert.equal(sealed.state, 'sealed');
  assert.equal(sealed.secret, '<sealed>');
  assert.equal(sealed.salt, '<sealed>');
  assert.equal(sealed.randomness, '<sealed>');
  assert.equal(sealed.attestation_digest, 'bb'.repeat(32));
  const open = JSON.parse(runCli(['commitments', 'get', 'rc_' + '11'.repeat(16), '--json']).stdout);
  assert.equal(open.state, 'open');
  assert.equal(open.secret, 'de'.repeat(32));
  assert.equal(open.randomness, 'aa'.repeat(32));
  assert.equal(runCli(['commitments', 'get', 'rc_nope']).code, 1);

  // prune by age: only the older row goes
  const pruned = runCli(['commitments', 'prune', '--older-than', '30s']);
  assert.equal(pruned.code, 0, pruned.stderr);
  assert.match(pruned.stdout, /pruned 1 commitment/);
  const left = JSON.parse(runCli(['commitments', 'list', '--json']).stdout);
  assert.equal(left.length, 1);
  assert.equal(left[0].request_id, 'sealed-one');
});

test('usage on unknown command exits non-zero', () => {
  const r = runCli(['frobnicate']);
  assert.equal(r.code, 1);
  assert.match(r.stdout, /operator CLI/);
});
