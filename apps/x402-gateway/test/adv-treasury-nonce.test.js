'use strict';
/**
 * ADVERSARIAL REVIEW — the shared nonce lane (settlement interference).
 *
 * The module's stated invariant (src/treasury/treasury.js header, docs/x402.md
 * "It never touches the settlement path") is:
 *
 *   "The only shared resource is the facilitator account's tx nonce, and the
 *    treasury holds the settlement engine's submit lock for exactly one
 *    sign+broadcast — never across receipt polling."
 *
 * The FIFO submit lock serialises the *code*. It does not make the nonce lane
 * safe, because neither writer keeps a nonce of its own: both re-derive it
 * from `eth_getTransactionCount(addr, 'pending')` immediately before signing
 * (settlement.js and treasury.js — asserted statically below). Three defects
 * follow, each proved here against the module's real bytes:
 *
 *   N1  A lagging/load-balanced RPC answers `pending` from a node that has
 *       not yet seen the treasury's just-broadcast tx. The next settlement
 *       signs the SAME nonce and the node rejects it as a replacement. The
 *       settlement engine classifies a non-transport send error as a
 *       DEFINITIVE rejection (settlement.js "Definitive node-side rejection")
 *       -> markFailed, and the row keeps raw_tx so the authorization is not
 *       reclaimable: the payer must sign a fresh one. A sip caused that.
 *
 *   N2  Transactions are included in nonce order. A treasury tx that sticks
 *       in the mempool (fee spike after signing, dropped-then-restored, an
 *       RPC that accepted it and stalled) blocks EVERY later settlement
 *       behind the nonce gap: receipts never arrive, settlements time out.
 *       Nothing in the treasury bumps, replaces or recovers its own tx —
 *       recoverInFlight() only knows the payments table.
 *
 *   N3  `animica-x402 treasury sip|sweep --confirm` builds a treasury with NO
 *       submit lock (bin/animica-x402 liveTreasury() omits withSubmitLock, so
 *       the default no-op is used) in a SECOND process. It therefore signs
 *       against the same key, on the same nonce lane, with no interlock at
 *       all. The CLI prints a warning about this when --confirm is missing;
 *       nothing enforces it.
 *
 * Every test asserts today's real behaviour so the suite stays green.
 */

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const evm = require('../src/facilitator-evm/evm');
const uniswap = require('../src/treasury/uniswap');
const gasMod = require('../src/facilitator-evm/gas');
const { createTreasury } = require('../src/treasury/treasury');
const { createTreasuryStore } = require('../src/treasury/store');
const { createMetrics } = require('../src/metrics');
const { treasuryScene, decodeEip1559, C } = require('./treasury-helpers');
const { kp, quietLogger } = require('./evm-helpers');

const SRC = path.join(__dirname, '..', 'src');
const read = (p) => fs.readFileSync(path.join(SRC, p), 'utf8');

/** FIFO submit lock, byte-identical to settlement.js's withSubmitLock. */
function makeSubmitLock() {
  let lock = Promise.resolve();
  return (fn) => {
    const run = lock.then(fn, fn);
    lock = run.then(() => undefined, () => undefined);
    return run;
  };
}

/**
 * The settlement engine's submit critical section, reproduced from
 * settlement.js lines ~270-321 (nonce read -> signTx -> sendRawTransaction ->
 * receipt poll). Only the calldata differs: nonce mechanics do not care what
 * the payload is, and the mock chain has no EIP-3009 implementation.
 */
async function settlementSubmit({ rpc, cfg, signer, submitLock, payee, sleep, now }) {
  const out = { nonce: null, txHash: null, sendError: null, receipt: null };
  await submitLock(async () => {
    const fees = await gasMod.estimateFees(rpc, { maxFeePerGasCap: cfg.maxFeePerGasWei });
    out.nonce = Number(evm.quantityToBigInt(await rpc.call('eth_getTransactionCount', [signer.address, 'pending'])));
    const signed = signer.signTx({
      chainId: cfg.chainId,
      nonce: out.nonce,
      maxPriorityFeePerGas: fees.maxPriorityFeePerGas,
      maxFeePerGas: fees.maxFeePerGas,
      gasLimit: 100_000n,
      to: cfg.asset,
      value: 0n,
      data: uniswap.transferCalldata(payee, 1n), // stand-in for transferWithAuthorization
    });
    out.txHash = signed.hash;
    try {
      await rpc.call('eth_sendRawTransaction', [signed.rawTx]);
    } catch (e) {
      out.sendError = e;
      return;
    }
    // confirmSettlement's receipt poll, bounded by X402_RECEIPT_TIMEOUT_MS.
    const deadline = now() + cfg.receiptTimeoutMs;
    while (now() < deadline) {
      const r = await rpc.call('eth_getTransactionReceipt', [out.txHash]);
      if (r) { out.receipt = r; return; }
      await sleep(cfg.receiptPollMs);
    }
  });
  return out;
}

/* ========================================================================== */

test('N0 (static): both writers re-read the pending nonce per tx; neither keeps a high-water mark', () => {
  const settlement = read('facilitator-evm/settlement.js');
  const treasury = read('treasury/treasury.js');

  assert.match(settlement, /eth_getTransactionCount', \[signer\.address, 'pending'\]/);
  assert.match(treasury, /eth_getTransactionCount', \[ME, 'pending'\]/);

  // No local nonce tracking anywhere: the RPC's answer is the only source.
  for (const [name, src] of [['settlement.js', settlement], ['treasury.js', treasury]]) {
    assert.ok(
      !/(lastNonce|nextNonce|nonceHighWater|localNonce)/.test(src),
      `${name} would need a local high-water mark to make two writers safe`
    );
  }
  // And nothing ever replaces/bumps a stuck treasury transaction.
  assert.ok(!/(replaceTx|bumpFee|cancelTx|speedUp)/.test(treasury), 'treasury.js has no stuck-tx escape');
  // recoverInFlight only knows the payments store, never treasury_actions.
  assert.ok(!/treasury_actions/.test(settlement));
});

test('N1: a lagging `pending` nonce makes a sip and the next settlement sign the SAME nonce', async () => {
  const s = treasuryScene({ eth: 1n, usdc: 30_000_000n });
  const submitLock = makeSubmitLock();
  s.treasuryLock = submitLock;

  // Pre-approve so the sip is a single transaction (clearer nonce trace).
  s.rpc.state.allowance.set(`${s.facilitator.toLowerCase()}:${C.swapRouter02.toLowerCase()}`, 10n ** 18n);

  // A load-balanced public RPC: eth_getTransactionCount is answered by a node
  // that is one block behind the one that accepted the broadcast. This is the
  // documented behaviour of every multi-node RPC front-end under load.
  const inner = s.rpc.call.bind(s.rpc);
  const usedNonces = new Map();
  s.rpc.call = async (method, params = []) => {
    if (method === 'eth_getTransactionCount') return '0x5'; // frozen: never advances
    if (method === 'eth_sendRawTransaction') {
      const tx = decodeEip1559(params[0]);
      const n = Number(tx.nonce);
      if (usedNonces.has(n)) {
        // geth/reth/op-node wording for a same-nonce, same-price resubmit.
        throw new Error('replacement transaction underpriced');
      }
      usedNonces.set(n, true);
      return inner(method, params);
    }
    return inner(method, params);
  };

  const treasury = createTreasury({
    cfg: s.cfg, rpc: s.rpc, tstore: s.tstore, signer: s.signer, metrics: s.metrics, logger: s.logger,
    withSubmitLock: submitLock, now: s.clock.now, sleep: s.clock.sleep,
  });

  const sip = await treasury.attemptSip({ trigger: 'interval' });
  assert.equal(sip.action, 'sipped');
  const sipNonce = Number(decodeEip1559(s.rpc.sent[0].raw).nonce);
  assert.equal(sipNonce, 5);

  // The next settlement, correctly serialised behind the same submit lock.
  const settle = await settlementSubmit({
    rpc: s.rpc, cfg: s.cfg, signer: s.signer, submitLock, payee: kp().address,
    sleep: s.clock.sleep, now: s.clock.now,
  });

  assert.equal(settle.nonce, sipNonce, 'the submit lock did NOT prevent a duplicate nonce');
  assert.ok(settle.sendError, 'the node rejected the settlement');
  assert.match(settle.sendError.message, /replacement transaction underpriced/);
  assert.notEqual(settle.sendError.transport, true,
    'a non-transport send error takes settlement.js\'s DEFINITIVE-rejection branch: markFailed, row keeps raw_tx, '
    + 'authorization not reclaimable — the payer must sign a new one, because a sip took its nonce');
});

test('N2: a treasury tx stuck in the mempool blocks every later settlement (nonce-ordered inclusion)', async () => {
  const s = treasuryScene({ eth: 1n, usdc: 30_000_000n });
  const submitLock = makeSubmitLock();
  s.rpc.state.allowance.set(`${s.facilitator.toLowerCase()}:${C.swapRouter02.toLowerCase()}`, 10n ** 18n);

  // A chain that includes transactions in nonce order and in which the
  // treasury's tx is stuck (underpriced after a base-fee jump between signing
  // and inclusion — the treasury signs 2*baseFee+tip and never bumps).
  const inner = s.rpc.call.bind(s.rpc);
  const nonceOf = new Map();
  const stuck = new Set();
  s.rpc.call = async (method, params = []) => {
    if (method === 'eth_sendRawTransaction') {
      const hash = await inner(method, params);
      const n = Number(decodeEip1559(params[0]).nonce);
      nonceOf.set(hash, n);
      if (stuck.size === 0) stuck.add(n); // the FIRST broadcast is the stuck one
      return hash;
    }
    if (method === 'eth_getTransactionReceipt') {
      const n = nonceOf.get(params[0]);
      if (n === undefined) return null;
      if (stuck.has(n)) return null;                       // never mined
      if ([...stuck].some((sn) => sn < n)) return null;    // blocked behind the gap
      return inner(method, params);
    }
    return inner(method, params);
  };

  const treasury = createTreasury({
    cfg: s.cfg, rpc: s.rpc, tstore: s.tstore, signer: s.signer, metrics: s.metrics, logger: s.logger,
    withSubmitLock: submitLock, now: s.clock.now, sleep: s.clock.sleep,
  });

  const sip = await treasury.attemptSip({ trigger: 'interval' });
  assert.equal(sip.action, 'unknown', 'the sip broadcast and then timed out waiting for its receipt');

  // Now a settlement. It queues behind the lock for one round trip, exactly
  // as documented — and then can never be mined, because the treasury owns
  // the nonce in front of it.
  const settle = await settlementSubmit({
    rpc: s.rpc, cfg: s.cfg, signer: s.signer, submitLock, payee: kp().address,
    sleep: s.clock.sleep, now: s.clock.now,
  });
  assert.equal(settle.nonce, 6);
  assert.equal(settle.sendError, null, 'broadcast fine...');
  assert.equal(settle.receipt, null, '...but no receipt within X402_RECEIPT_TIMEOUT_MS: the settlement fails');

  // ...and it stays that way for every subsequent settlement.
  const settle2 = await settlementSubmit({
    rpc: s.rpc, cfg: s.cfg, signer: s.signer, submitLock, payee: kp().address,
    sleep: s.clock.sleep, now: s.clock.now,
  });
  assert.equal(settle2.receipt, null, 'the outage persists — a stuck sip is a full settlement outage');

  // The treasury's own row is left 'submitting' forever; nothing reconciles it.
  const rows = s.tstore.list({ kind: 'sip', limit: 10 });
  assert.equal(rows[0].status, 'submitting');
  assert.equal(typeof treasury.recover, 'undefined', 'the treasury exposes no recovery entry point at all');

  // Control: with nothing stuck in front of it, the identical settlement
  // confirms immediately — the treasury tx is the whole difference.
  const clean = treasuryScene({ eth: 10n ** 15n, usdc: 30_000_000n });
  const ok = await settlementSubmit({
    rpc: clean.rpc, cfg: clean.cfg, signer: clean.signer, submitLock: makeSubmitLock(),
    payee: kp().address, sleep: clean.clock.sleep, now: clean.clock.now,
  });
  assert.ok(ok.receipt, 'control settlement confirms');
});

test('N3: `treasury sip|sweep --confirm` runs with NO submit lock and collides with a live settlement', async () => {
  // Static half: the CLI's liveTreasury() never passes withSubmitLock, so
  // createTreasury falls back to its no-op default.
  const cli = fs.readFileSync(path.join(__dirname, '..', 'bin', 'animica-x402'), 'utf8');
  assert.match(cli, /return createTreasury\(\{ cfg, rpc, tstore, signer, metrics: createMetrics\(\), logger \}\)/);
  assert.ok(!/withSubmitLock/.test(cli), 'the CLI has no interlock with the running service — only a printed warning');
  assert.match(read('treasury/treasury.js'), /withSubmitLock = \(fn\) => Promise\.resolve\(\)\.then\(fn\)/,
    'the default lock is a no-op, which is correct for unit tests and wrong for a second process');

  // Behavioural half: service settlement + CLI sip, same key, same chain,
  // same DB file, two processes -> one nonce.
  const s = treasuryScene({ eth: 1n, usdc: 30_000_000n });
  const submitLock = makeSubmitLock();
  s.rpc.state.allowance.set(`${s.facilitator.toLowerCase()}:${C.swapRouter02.toLowerCase()}`, 10n ** 18n);

  // The CLI process: same signer, its own store handle over the same DB, and
  // (the defect) no submit lock.
  const cliTreasury = createTreasury({
    cfg: s.cfg,
    rpc: s.rpc,
    tstore: createTreasuryStore(s.store.db, { now: s.clock.now }),
    signer: s.signer,
    metrics: createMetrics(),
    logger: quietLogger,
    now: s.clock.now,
    sleep: s.clock.sleep,
  });

  const usedNonces = new Set();
  const inner = s.rpc.call.bind(s.rpc);
  let fired = false;
  s.rpc.call = async (method, params = []) => {
    if (method === 'eth_sendRawTransaction') {
      const n = Number(decodeEip1559(params[0]).nonce);
      if (usedNonces.has(n)) throw new Error('replacement transaction underpriced');
      usedNonces.add(n);
      return inner(method, params);
    }
    const out = await inner(method, params);
    if (method === 'eth_getTransactionCount' && !fired) {
      // The operator runs `animica-x402 treasury sip --confirm` in the window
      // between the settlement's nonce read and its broadcast. Nothing in
      // either process can see the other.
      fired = true;
      await cliTreasury.sipNow();
    }
    return out;
  };

  const settle = await settlementSubmit({
    rpc: s.rpc, cfg: s.cfg, signer: s.signer, submitLock, payee: kp().address,
    sleep: s.clock.sleep, now: s.clock.now,
  });

  const cliNonce = Number(decodeEip1559(s.rpc.sent[0].raw).nonce);
  assert.equal(settle.nonce, cliNonce, 'the CLI sip took the settlement\'s nonce');
  assert.ok(settle.sendError, 'and the settlement was rejected');
  assert.match(settle.sendError.message, /replacement transaction underpriced/);
  assert.equal(evm.addressEquals(s.rpc.sent[0].tx.to, C.swapRouter02), true, 'the tx that won the nonce is the sip');
});
