'use strict';
/**
 * Persistent replay/settlement store (better-sqlite3, WAL). This is the
 * ledger the whole facilitator's safety hangs on:
 *
 *   - `authorization_hash` (the EIP-712 digest of the signed authorization)
 *     is UNIQUE — the database, not application logic, is the arbiter of
 *     "has this authorization ever been accepted for settlement". Two
 *     simultaneous settles race on one INSERT; SQLite serializes them and
 *     exactly one wins (`claim()`), synchronously — better-sqlite3 is sync,
 *     so there is no await window between check and mark.
 *
 *   - every state transition is persisted BEFORE the action it authorizes
 *     (claim -> pending, sign -> submitting w/ raw tx + hash + nonce, then
 *     broadcast). After a crash, `listSubmitting()` returns every payment
 *     whose outcome is unknown; recovery queries the chain (authorization
 *     state / tx by hash) before any rebroadcast decision — never a blind
 *     resend (see settlement.js recoverInFlight).
 *
 * status: pending | submitting | settled | failed | expired.
 * Amounts are TEXT columns holding decimal atomic-unit strings (BigInt in
 * JS); SQLite INTEGER is only 63-bit signed and wei sums can pass it, and
 * TEXT keeps every read/write exact with no float anywhere.
 */

const fs = require('node:fs');
const path = require('node:path');

const SCHEMA = `
CREATE TABLE IF NOT EXISTS payments (
  payment_id         TEXT PRIMARY KEY,
  authorization_hash TEXT NOT NULL UNIQUE,
  payer              TEXT NOT NULL,
  asset              TEXT NOT NULL,
  network            TEXT NOT NULL,
  amount             TEXT NOT NULL,          -- atomic units, decimal string
  resource           TEXT NOT NULL DEFAULT '',
  created_at         INTEGER NOT NULL,       -- unix seconds
  expires_at         INTEGER NOT NULL,       -- authorization validBefore
  status             TEXT NOT NULL CHECK (status IN ('pending','submitting','settled','failed','expired')),
  settlement_tx_hash TEXT,
  settled_at         INTEGER,
  -- crash-recovery + accounting extensions
  auth_nonce         TEXT,                   -- EIP-3009 32-byte nonce (0x hex)
  tx_nonce           INTEGER,                -- facilitator account nonce used
  raw_tx             TEXT,                   -- signed raw tx (rebroadcastable as-is)
  gas_spent_wei      TEXT,                   -- gasUsed*effectiveGasPrice + l1Fee
  -- Worst-case cost (gasLimit*maxFeePerGas) reserved the moment a tx is signed.
  -- Settled spend lands in gas_spend tens of seconds later, so the daily budget
  -- must count in-flight reservations too or concurrent settles race past it.
  gas_reserved_wei   TEXT,
  error_reason       TEXT
);
CREATE INDEX IF NOT EXISTS idx_payments_status ON payments(status);
CREATE INDEX IF NOT EXISTS idx_payments_created ON payments(created_at);

CREATE TABLE IF NOT EXISTS gas_spend (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  payment_id TEXT NOT NULL,
  tx_hash    TEXT,
  spent_wei  TEXT NOT NULL,                  -- decimal string
  created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_gas_spend_created ON gas_spend(created_at);
`;

function nowSec() {
  return Math.floor(Date.now() / 1000);
}

function createStore(dbPath, { Database } = {}) {
  const Db = Database || require('better-sqlite3');
  if (dbPath !== ':memory:') {
    fs.mkdirSync(path.dirname(path.resolve(dbPath)), { recursive: true });
  }
  const db = new Db(dbPath);
  db.pragma('journal_mode = WAL');
  db.pragma('synchronous = NORMAL'); // WAL + NORMAL: durable at checkpoint, atomic always
  db.pragma('busy_timeout = 5000');
  db.exec(SCHEMA);
  // Migration for DB files created before in-flight gas reservations existed.
  const paymentCols = db.prepare('PRAGMA table_info(payments)').all().map((c) => c.name);
  if (!paymentCols.includes('gas_reserved_wei')) {
    db.exec('ALTER TABLE payments ADD COLUMN gas_reserved_wei TEXT;');
  }

  const stmts = {
    claim: db.prepare(`INSERT INTO payments
      (payment_id, authorization_hash, payer, asset, network, amount, resource, created_at, expires_at, status, auth_nonce)
      VALUES (@payment_id, @authorization_hash, @payer, @asset, @network, @amount, @resource, @created_at, @expires_at, 'pending', @auth_nonce)`),
    byAuthHash: db.prepare('SELECT * FROM payments WHERE authorization_hash = ?'),
    byId: db.prepare('SELECT * FROM payments WHERE payment_id = ?'),
    markSubmitting: db.prepare(`UPDATE payments SET status='submitting', settlement_tx_hash=@tx_hash,
      tx_nonce=@tx_nonce, raw_tx=@raw_tx, gas_reserved_wei=@gas_reserved_wei
      WHERE payment_id=@payment_id AND status IN ('pending','submitting')`),
    markSettled: db.prepare(`UPDATE payments SET status='settled', settlement_tx_hash=@tx_hash,
      settled_at=@settled_at, gas_spent_wei=@gas_spent_wei, error_reason=NULL
      WHERE payment_id=@payment_id AND status IN ('pending','submitting')`),
    markFailed: db.prepare(`UPDATE payments SET status='failed', error_reason=@error_reason
      WHERE payment_id=@payment_id AND status IN ('pending','submitting')`),
    markExpired: db.prepare(`UPDATE payments SET status='expired', error_reason=@error_reason
      WHERE payment_id=@payment_id AND status IN ('pending','submitting')`),
    reclaim: db.prepare(`UPDATE payments SET status='pending', error_reason=NULL, created_at=@created_at
      WHERE payment_id=@payment_id AND raw_tx IS NULL AND status IN ('failed','expired')`),
    listSubmitting: db.prepare(`SELECT * FROM payments WHERE status='submitting' ORDER BY created_at`),
    listByStatus: db.prepare(`SELECT * FROM payments WHERE status=? ORDER BY created_at DESC LIMIT ?`),
    addGas: db.prepare(`INSERT INTO gas_spend (payment_id, tx_hash, spent_wei, created_at)
      VALUES (@payment_id, @tx_hash, @spent_wei, @created_at)`),
    gasSince: db.prepare('SELECT spent_wei FROM gas_spend WHERE created_at >= ?'),
    gasReservedInFlight: db.prepare(
      `SELECT gas_reserved_wei FROM payments
       WHERE status IN ('pending','submitting') AND gas_reserved_wei IS NOT NULL`),
    revenueByStatus: db.prepare(`SELECT amount FROM payments WHERE status='settled'`),
  };

  return {
    db, // exposed for tests/CLI reconciliation only

    /**
     * Atomically claim an authorization for settlement. Returns
     * { claimed: true, paymentId } or { claimed: false, existing } when the
     * authorization_hash was already claimed (replay / concurrent settle).
     */
    claim({ paymentId, authorizationHash, payer, asset, network, amount, resource, expiresAt, authNonce }) {
      try {
        stmts.claim.run({
          payment_id: paymentId,
          authorization_hash: authorizationHash,
          payer,
          asset,
          network,
          amount: String(amount),
          resource: resource || '',
          created_at: nowSec(),
          expires_at: Number(expiresAt),
          auth_nonce: authNonce || null,
        });
        return { claimed: true, paymentId };
      } catch (e) {
        if (e && String(e.code || '').startsWith('SQLITE_CONSTRAINT')) {
          const existing = stmts.byAuthHash.get(authorizationHash) || null;
          // A previous attempt that FAILED BEFORE any tx was signed left the
          // authorization untouched on-chain — allow one retry by atomically
          // flipping that same row back to pending (guarded on raw_tx IS
          // NULL, so a row that ever held a signed tx can never be reclaimed).
          if (existing && existing.raw_tx === null && (existing.status === 'failed' || existing.status === 'expired')) {
            const r = stmts.reclaim.run({ payment_id: existing.payment_id, created_at: nowSec() });
            if (r.changes === 1) return { claimed: true, paymentId: existing.payment_id, reclaimed: true };
          }
          return { claimed: false, existing };
        }
        throw e;
      }
    },

    getByAuthorizationHash(hash) {
      return stmts.byAuthHash.get(hash) || null;
    },
    getByPaymentId(id) {
      return stmts.byId.get(id) || null;
    },

    /** Persist the signed tx BEFORE broadcasting it. */
    markSubmitting(paymentId, { txHash, txNonce, rawTx, gasReservedWei }) {
      const r = stmts.markSubmitting.run({
        payment_id: paymentId, tx_hash: txHash, tx_nonce: txNonce, raw_tx: rawTx,
        gas_reserved_wei: gasReservedWei === undefined || gasReservedWei === null
          ? null : String(gasReservedWei),
      });
      if (r.changes !== 1) throw new Error(`markSubmitting: payment ${paymentId} not in a claimable state`);
    },

    /**
     * Worst-case gas already committed by settlements that are signed/broadcast
     * but whose receipts have not landed. The daily budget must include this or
     * a burst of concurrent settles all pass a check that sees only past spend.
     */
    gasReservedInFlight() {
      let total = 0n;
      for (const row of stmts.gasReservedInFlight.all()) {
        try { total += BigInt(row.gas_reserved_wei); } catch { /* ignore malformed */ }
      }
      return total;
    },

    markSettled(paymentId, { txHash, gasSpentWei }) {
      const r = stmts.markSettled.run({
        payment_id: paymentId,
        tx_hash: txHash,
        settled_at: nowSec(),
        gas_spent_wei: gasSpentWei === undefined || gasSpentWei === null ? null : String(gasSpentWei),
      });
      if (r.changes !== 1) throw new Error(`markSettled: payment ${paymentId} not in a settleable state`);
      if (gasSpentWei !== undefined && gasSpentWei !== null) {
        stmts.addGas.run({ payment_id: paymentId, tx_hash: txHash, spent_wei: String(gasSpentWei), created_at: nowSec() });
      }
    },

    markFailed(paymentId, errorReason, { gasSpentWei, txHash } = {}) {
      stmts.markFailed.run({ payment_id: paymentId, error_reason: String(errorReason || 'failed') });
      // A reverted tx still burned gas — account for it.
      if (gasSpentWei !== undefined && gasSpentWei !== null) {
        stmts.addGas.run({ payment_id: paymentId, tx_hash: txHash || null, spent_wei: String(gasSpentWei), created_at: nowSec() });
      }
    },

    markExpired(paymentId, errorReason) {
      stmts.markExpired.run({ payment_id: paymentId, error_reason: String(errorReason || 'expired') });
    },

    /** Crash recovery: payments whose broadcast outcome is unknown. */
    listSubmitting() {
      return stmts.listSubmitting.all();
    },

    listByStatus(status, limit = 100) {
      return stmts.listByStatus.all(status, limit);
    },

    /** Total gas spent (BigInt wei) since a unix-seconds timestamp. */
    gasSpentSince(sinceSec) {
      let total = 0n;
      for (const row of stmts.gasSince.all(Number(sinceSec))) total += BigInt(row.spent_wei);
      return total;
    },

    /** Sum of settled amounts (BigInt atomic units) — revenue ground truth. */
    settledRevenueAtomic() {
      let total = 0n;
      for (const row of stmts.revenueByStatus.all()) total += BigInt(row.amount);
      return total;
    },

    /** Cheap liveness check for readyz. Throws when the DB is unusable. */
    ping() {
      db.prepare('SELECT 1').get();
      return true;
    },

    close() {
      db.close();
    },
  };
}

module.exports = { createStore };
