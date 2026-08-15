'use strict';
/**
 * ADVERSARIAL REGRESSION — the shared nonce lane (settlement interference).
 *
 * These started life as proofs of three defects. They now pin the fixes.
 *
 *   N1  A lagging/load-balanced RPC answers `eth_getTransactionCount(...,
 *       'pending')` from a node that has not yet seen the treasury's
 *       just-broadcast transaction. Before the fix the next settlement signed
 *       the SAME nonce, the node answered "replacement transaction
 *       underpriced", and settlement.js took its DEFINITIVE-rejection branch:
 *       markFailed, row keeps raw_tx, the payer's authorization burned —
 *       because a sip took its nonce. FIX: one allocator
 *       (src/facilitator-evm/nonce.js) shared by both writers,
 *       nonce = max(remote pending, last issued + 1).
 *
 *   N2  Transactions from one EOA are included in nonce order, so a treasury
 *       transaction stuck in the mempool blocked EVERY later settlement
 *       behind its nonce gap — and nothing could clear it (no raw_tx stored,
 *       no recovery pass, no fee bump). FIX: raw tx + tx params persisted,
 *       treasury.recover() resolves from chain truth and fee-bumps a stuck
 *       transaction (same nonce), no new treasury tx is signed while one is
 *       unresolved, and /readyz warns.
 *
 *   N3  `animica-x402 treasury sip|sweep --confirm` signed from a second
 *       process with no interlock at all. FIX: an exclusive lease row in the
 *       shared DB — the service holds it continuously, the CLI takes it or
 *       refuses.
 */

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const evm = require('../src/facilitator-evm/evm');
const uniswap = require('../src/treasury/uniswap');
const gasMod = require('../src/facilitator-evm/gas');
const { createNonceAllocator } = require('../src/facilitator-evm/nonce');
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
 * settlement.js (allocate nonce -> signTx -> commit -> sendRawTransaction ->
 * receipt poll; release on a definitive rejection). Only the calldata differs:
 * nonce mechanics do not care what the payload is, and the mock chain has no
 * EIP-3009 implementation. The static test below asserts the real file does
 * exactly this.
 */
async function settlementSubmit({ rpc, cfg, signer, submitLock, nonces, payee, sleep, now }) {
  const out = { nonce: null, txHash: null, sendError: null, receipt: null };
  await submitLock(async () => {
    const fees = await gasMod.estimateFees(rpc, { maxFeePerGasCap: cfg.maxFeePerGasWei });
    out.nonce = (await nonces.next()).nonce;
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
    nonces.commit(out.nonce);
    try {
      await rpc.call('eth_sendRawTransaction', [signed.rawTx]);
    } catch (e) {
      if (!(e && e.transport)) nonces.release(out.nonce);
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

/**
 * A chain whose FIRST broadcast on any nonce is accepted and then never
 * mined (the base fee rose between signing and inclusion — the treasury signs
 * 2*baseFee+tip). A replacement on the same nonce is accepted only at
 * >= 112.5% of the stuck fee, exactly like every mainstream client.
 */
function stickyChain(s) {
  const inner = s.rpc.call.bind(s.rpc);
  const stuck = new Map();      // nonce -> { hash, maxFee }
  const pending = new Set();    // hashes accepted but never mined
  s.rpc.call = async (method, params = []) => {
    if (method === 'eth_sendRawTransaction') {
      const tx = decodeEip1559(params[0]);
      const n = Number(tx.nonce);
      if (stuck.size === 0) {
        // The FIRST broadcast is the one that sticks (the treasury's).
        const hash = evm.bytesToHex(evm.keccak(evm.hexToBytes(params[0])));
        stuck.set(n, { hash, maxFee: tx.maxFeePerGas });
        pending.add(hash);
        return hash; // accepted into the mempool; no state effect, ever
      }
      if (!stuck.has(n)) return inner(method, params);
      const prev = stuck.get(n);
      if (tx.maxFeePerGas * 1000n < prev.maxFee * 1125n) throw new Error('replacement transaction underpriced');
      pending.delete(prev.hash);
      return inner(method, params); // the replacement mines normally
    }
    if (method === 'eth_getTransactionReceipt' && pending.has(params[0])) return null;
    if (method === 'eth_getTransactionByHash' && pending.has(params[0])) return { hash: params[0] };
    return inner(method, params);
  };
  return { stuck, pending };
}

/* ========================================================================== */

test('N0 (static): both writers take their nonce from ONE allocator, and the treasury can clear its own stuck tx', () => {
  const settlement = read('facilitator-evm/settlement.js');
  const treasury = read('treasury/treasury.js');
  const server = read('facilitator-evm/server.js');
  const nonce = read('facilitator-evm/nonce.js');

  // Neither writer derives a nonce from the RPC on its own any more (the
  // string survives only in prose explaining why).
  assert.ok(!/call\('eth_getTransactionCount'/.test(settlement), 'settlement.js must go through the allocator');
  assert.ok(!/call\('eth_getTransactionCount'/.test(treasury), 'treasury.js must go through the allocator');
  assert.match(nonce, /call\('eth_getTransactionCount'/, 'the allocator is the single reader');

  // The allocator keeps a high-water mark and hands it back on a definitive
  // rejection (otherwise a rejected send would strand the lane in a gap).
  assert.match(nonce, /lastIssued \+ 1/);
  assert.match(nonce, /function release/);

  // Settlement: allocate, commit before broadcast, release only on a
  // definitive node-side rejection.
  assert.match(settlement, /await nonces\.next\(\)/);
  assert.match(settlement, /nonces\.commit\(txNonce\)/);
  assert.match(settlement, /nonces\.release\(txNonce\)/);
  // Treasury: same allocator object, injected by the facilitator.
  assert.match(treasury, /nonceLane\.next\(\)/);
  assert.match(treasury, /nonceLane\.commit\(txNonce\)/);
  assert.match(server, /nonces: engine\.nonces/);

  // And a stuck treasury transaction now has an escape: stored bytes, a
  // recovery pass and a fee bump.
  assert.match(treasury, /function bumpStuck/);
  assert.match(treasury, /async function recover/);
  assert.match(read('treasury/store.js'), /raw_tx\s+TEXT/);
});

test('N1: a lagging `pending` nonce can no longer hand a sip and a settlement the same nonce', async () => {
  const s = treasuryScene({ eth: 1n, usdc: 30_000_000n });
  const submitLock = makeSubmitLock();

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

  // The wiring facilitator-evm/server.js builds: ONE allocator, both writers.
  const nonces = createNonceAllocator({ rpc: s.rpc, address: s.facilitator, now: s.clock.now });
  const treasury = createTreasury({
    cfg: s.cfg, rpc: s.rpc, tstore: s.tstore, signer: s.signer, metrics: s.metrics, logger: s.logger,
    withSubmitLock: submitLock, nonces, now: s.clock.now, sleep: s.clock.sleep,
  });

  const sip = await treasury.attemptSip({ trigger: 'interval' });
  assert.equal(sip.action, 'sipped');
  const sipNonce = Number(decodeEip1559(s.rpc.sent[0].raw).nonce);
  assert.equal(sipNonce, 5);

  // The next settlement, serialised behind the same submit lock and drawing
  // from the same allocator.
  const settle = await settlementSubmit({
    rpc: s.rpc, cfg: s.cfg, signer: s.signer, submitLock, nonces, payee: kp().address,
    sleep: s.clock.sleep, now: s.clock.now,
  });

  assert.equal(settle.nonce, sipNonce + 1, 'the settlement got the NEXT nonce despite the frozen `pending` count');
  assert.equal(settle.sendError, null, 'no "replacement transaction underpriced": the payer keeps their authorization');
  assert.ok(settle.receipt, 'and the settlement confirms');
});

test('N1b: a definitively rejected send hands the nonce back, so the lane does not gap', async () => {
  const s = treasuryScene({ eth: 10n ** 15n, usdc: 0n });
  const nonces = createNonceAllocator({ rpc: s.rpc, address: s.facilitator, now: s.clock.now });

  const first = await nonces.next();
  assert.equal(first.nonce, 5, 'chain truth to begin with');
  nonces.commit(first.nonce);
  // The node rejects it (bad chain id, underpriced, whatever): nothing is in
  // flight, so the nonce must become available again.
  nonces.release(first.nonce);
  const second = await nonces.next();
  assert.equal(second.nonce, 5, 'the rejected nonce is re-used, not skipped');

  // A committed nonce, by contrast, is never handed out twice.
  nonces.commit(second.nonce);
  const third = await nonces.next();
  assert.equal(third.nonce, 6);
  assert.equal(third.source, 'high_water');
});

test('N1c: a high-water mark that goes stale falls back to chain truth (a dropped tx must be re-signable)', async () => {
  const s = treasuryScene({ eth: 10n ** 15n, usdc: 0n });
  const nonces = createNonceAllocator({ rpc: s.rpc, address: s.facilitator, now: s.clock.now, ttlMs: 120_000 });
  nonces.commit((await nonces.next()).nonce); // 5
  assert.equal((await nonces.next()).nonce, 6);
  s.clock.advance(121_000); // the RPC still says 5: our tx never made it anywhere
  const after = await nonces.next();
  assert.equal(after.nonce, 5, 'past the TTL the node wins — otherwise the lane would sign into a permanent gap');
  assert.equal(after.source, 'chain');
});

test('N2: a stuck treasury tx is bumped, resolved and never left blocking the settlement lane', async () => {
  const s = treasuryScene({ eth: 1n, usdc: 30_000_000n, env: { X402_TREASURY_STUCK_TX_S: '60' } });
  s.rpc.state.allowance.set(`${s.facilitator.toLowerCase()}:${C.swapRouter02.toLowerCase()}`, 10n ** 18n);
  const sticky = stickyChain(s);

  const sip = await s.treasury.attemptSip({ trigger: 'interval' });
  assert.equal(sip.action, 'unknown', 'the sip broadcast and then timed out waiting for its receipt');

  // While that transaction is unresolved the treasury signs nothing new (its
  // nonce sits in front of every settlement) and /readyz says so.
  const blocked = await s.treasury.attemptSip({ trigger: 'interval' });
  assert.equal(blocked.reason, 'unresolved_action');
  assert.equal((await s.treasury.attemptSweep({ trigger: 'interval' })).reason, 'unresolved_action');
  assert.match(s.treasury.warning(), /unresolved on-chain/);
  assert.match(s.metrics.render(), /x402_treasury_unresolved_actions(\{\})? 1/);

  // The stuck-transaction escape: same nonce, higher fee.
  s.clock.advance(61_000);
  const bumped = await s.treasury.recover({ trigger: 'test' });
  assert.equal(bumped.bumped, 1, 'the stuck transaction was replaced, not abandoned');
  const stuckNonce = [...sticky.stuck.keys()][0];
  const replacement = s.rpc.sent[s.rpc.sent.length - 1];
  assert.equal(Number(replacement.tx.nonce), stuckNonce, 'the replacement occupies the SAME nonce — that is what clears the lane');
  assert.ok(replacement.tx.maxFeePerGas * 1000n >= sticky.stuck.get(stuckNonce).maxFee * 1125n);

  // Next pass resolves it from chain truth: the ledger and the chain agree.
  const resolved = await s.treasury.recover({ trigger: 'test' });
  assert.equal(resolved.confirmed, 1);
  const row = s.tstore.list({ kind: 'sip', limit: 5 })[0];
  assert.equal(row.status, 'confirmed');
  assert.equal(row.bump_count, 1);
  assert.equal(s.tstore.totals().sips, 1);
  assert.equal(s.tstore.unresolvedCount(), 0);
  assert.equal(s.treasury.warning(), null);

  // And a settlement behind it confirms normally.
  const nonces = s.treasury.nonces;
  const settle = await settlementSubmit({
    rpc: s.rpc, cfg: s.cfg, signer: s.signer, submitLock: makeSubmitLock(), nonces,
    payee: kp().address, sleep: s.clock.sleep, now: s.clock.now,
  });
  assert.ok(settle.receipt, 'the lane is clear');
});

test('N2b: a treasury tx dropped from the mempool is rebroadcast from its stored bytes', async () => {
  const s = treasuryScene({ eth: 1n, usdc: 30_000_000n });
  s.rpc.state.allowance.set(`${s.facilitator.toLowerCase()}:${C.swapRouter02.toLowerCase()}`, 10n ** 18n);
  const inner = s.rpc.call.bind(s.rpc);
  let vanished = null;
  let resent = false;
  s.rpc.call = async (method, params = []) => {
    if (method === 'eth_sendRawTransaction' && vanished === null) {
      const hash = evm.bytesToHex(evm.keccak(evm.hexToBytes(params[0])));
      vanished = hash; // accepted, then evicted: no receipt, no pending tx
      return hash;
    }
    if (method === 'eth_sendRawTransaction') { resent = true; return inner(method, params); }
    if (!resent && method === 'eth_getTransactionReceipt' && params[0] === vanished) return null;
    if (!resent && method === 'eth_getTransactionByHash' && params[0] === vanished) return null;
    return inner(method, params);
  };

  assert.equal((await s.treasury.attemptSip({ trigger: 'interval' })).action, 'unknown');
  const row = s.tstore.list({ kind: 'sip' })[0];
  assert.ok(row.raw_tx, 'the signed bytes are persisted (the pre-fix schema had nowhere to put them)');

  const rec = await s.treasury.recover({ trigger: 'test' });
  assert.equal(rec.rebroadcast, 1, 'the SAME signed bytes go back out — never a different transaction');
  const resolved = await s.treasury.recover({ trigger: 'test' });
  assert.equal(resolved.confirmed, 1);
  assert.equal(s.tstore.totals().sips, 1);
});

test('N2c: a legacy in-flight row (written before bump support) is reported stuck, never crashed', async () => {
  const s = treasuryScene({ eth: 1n, usdc: 30_000_000n, env: { X402_TREASURY_STUCK_TX_S: '60' } });
  // Exactly what a row upgraded from the previous schema looks like: a hash
  // and a nonce, no raw bytes and no fee parameters.
  const id = s.tstore.begin({ kind: 'sip', trigger: 'interval', usdcAmount: 5_000_000n, destination: C.swapRouter02 });
  s.tstore.attachTx(id, { txHash: '0x' + 'ee'.repeat(32), txNonce: 5 });
  const inner = s.rpc.call.bind(s.rpc);
  s.rpc.call = async (m, p = []) => {
    if (m === 'eth_getTransactionReceipt' && p[0] === '0x' + 'ee'.repeat(32)) return null;
    if (m === 'eth_getTransactionByHash' && p[0] === '0x' + 'ee'.repeat(32)) return { hash: p[0] };
    return inner(m, p);
  };

  s.clock.advance(61_000);
  const rec = await s.treasury.recover({ trigger: 'test' });
  assert.equal(rec.bumped, 0);
  assert.equal(rec.stillUnknown, 1);
  const stuckLog = s.logs.find((l) => l.event === 'treasury_tx_stuck');
  assert.ok(stuckLog, 'the operator gets the nonce to replace by hand');
  assert.equal(stuckLog.fields.tx_nonce, 5);
  assert.match(stuckLog.fields.detail, /no stored tx params/);
  assert.match(s.treasury.warning(), /unresolved on-chain/);
});

test('N3: the CLI cannot sign while the facilitator holds the treasury lease', async () => {
  // Static half: every signing CLI path goes through withTreasuryLease().
  const cli = fs.readFileSync(path.join(__dirname, '..', 'bin', 'animica-x402'), 'utf8');
  assert.match(cli, /async function withTreasuryLease/);
  assert.match(cli, /acquireLease\(\{ label: `cli:\$\{process\.pid\}`/);
  assert.match(cli, /return withTreasuryLease\(async \(treasury\) => \{/);
  assert.ok(/the treasury signing lease is held by another process/.test(cli), 'and it FAILS rather than warning');

  // Behavioural half: service and CLI over the same DB file.
  const s = treasuryScene({ eth: 1n, usdc: 30_000_000n });
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

  assert.equal(s.treasury.acquireLease({ label: 'facilitator' }).ok, true);
  const denied = cliTreasury.acquireLease({ label: 'cli:1234', ttlS: 300 });
  assert.equal(denied.ok, false, 'a second signer is refused the lane');
  assert.equal(denied.holder.label, 'facilitator');

  // Stop the unit (or let the lease expire) and the operator can act.
  s.treasury.releaseLease();
  assert.equal(cliTreasury.acquireLease({ label: 'cli:1234', ttlS: 300 }).ok, true);
  // ...and now the service is the one locked out until the CLI is done.
  assert.equal(s.treasury.acquireLease({ label: 'facilitator' }).ok, false);
});

test('N3c: a started service that does NOT hold the lease signs nothing', async () => {
  const s = treasuryScene({ eth: 1n, usdc: 30_000_000n });
  // The operator is mid-`treasury sip --confirm` when the unit is restarted.
  const cliTreasury = createTreasury({
    cfg: s.cfg, rpc: s.rpc, tstore: createTreasuryStore(s.store.db, { now: s.clock.now }),
    signer: s.signer, metrics: createMetrics(), logger: quietLogger, now: s.clock.now, sleep: s.clock.sleep,
  });
  assert.equal(cliTreasury.acquireLease({ label: 'cli:999', ttlS: 300 }).ok, true);

  s.treasury.start();                       // logs treasury_lease_busy, starts anyway
  await new Promise((r) => setImmediate(r));
  const sip = await s.treasury.attemptSip({ trigger: 'interval' });
  assert.equal(sip.reason, 'lease_unavailable', 'the service refuses to be the second signer, too');
  assert.equal((await s.treasury.attemptSweep({ trigger: 'interval' })).reason, 'lease_unavailable');
  assert.equal(s.rpc.sent.length, 0);
  assert.ok(s.logs.some((l) => l.event === 'treasury_lease_busy'));

  // The CLI finishes; the next tick reclaims the lane.
  cliTreasury.releaseLease();
  const t = await s.treasury.tick();
  assert.equal(t.sip.action, 'sipped');
  s.treasury.stop();
});

test('N3b: an expired lease is reclaimable (a crashed process must not lock the treasury forever)', async () => {
  const s = treasuryScene({ eth: 1n, usdc: 30_000_000n });
  const other = createTreasury({
    cfg: s.cfg, rpc: s.rpc, tstore: createTreasuryStore(s.store.db, { now: s.clock.now }),
    signer: s.signer, metrics: createMetrics(), logger: quietLogger, now: s.clock.now, sleep: s.clock.sleep,
  });
  assert.equal(s.treasury.acquireLease({ label: 'facilitator', ttlS: 900 }).ok, true);
  assert.equal(other.acquireLease({ label: 'cli', ttlS: 300 }).ok, false);
  s.clock.advance(901 * 1000);
  assert.equal(other.acquireLease({ label: 'cli', ttlS: 300 }).ok, true, 'the TTL expired: the lane is free again');
});
