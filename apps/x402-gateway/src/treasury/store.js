'use strict';
/**
 * Treasury ledger — the sip/sweep history, the daily-swap-budget accounting
 * basis, and the small amount of durable state the failure policy needs.
 *
 * It lives in the SAME sqlite file as the payments ledger (one DB, one
 * backup, one CLI) but in its own tables, and it is opened from the payments
 * store's handle so both share the WAL and the busy timeout.
 *
 * Money columns are TEXT holding decimal atomic-unit strings, read back as
 * BigInt — identical discipline to the payments ledger. USDC amounts are
 * 6-decimal atomic units, ETH amounts are wei.
 *
 * A row is written BEFORE the transaction it describes is broadcast, exactly
 * like a settlement: after a crash the operator can see what may be in
 * flight, and the daily budget counts money already committed rather than
 * only money already confirmed.
 */

const crypto = require('node:crypto');

const SCHEMA = `
CREATE TABLE IF NOT EXISTS treasury_actions (
  action_id        TEXT PRIMARY KEY,
  kind             TEXT NOT NULL CHECK (kind IN ('sip','sweep','approve')),
  status           TEXT NOT NULL CHECK (status IN ('submitting','confirmed','failed')),
  trigger          TEXT NOT NULL DEFAULT 'interval',
  created_at       INTEGER NOT NULL,
  confirmed_at     INTEGER,
  usdc_amount      TEXT,        -- atomic units spent (sip) / swept (sweep)
  quote_wei        TEXT,        -- QuoterV2 amountOut at signing time
  min_out_wei      TEXT,        -- amountOutMinimum actually encoded
  eth_received_wei TEXT,        -- from the WETH9 Withdrawal log (chain truth)
  pool_fee         INTEGER,
  destination      TEXT,        -- sweep: the cold address; sip: the router
  tx_hash          TEXT,
  tx_nonce         INTEGER,
  gas_spent_wei    TEXT,
  error            TEXT
);
CREATE INDEX IF NOT EXISTS idx_treasury_created ON treasury_actions(created_at);
CREATE INDEX IF NOT EXISTS idx_treasury_kind ON treasury_actions(kind, created_at);

CREATE TABLE IF NOT EXISTS treasury_state (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
`;

function newActionId(kind) {
  return `${kind}_${Date.now().toString(36)}${crypto.randomBytes(5).toString('hex')}`;
}

function bigOrNull(v) {
  return v === undefined || v === null ? null : String(v);
}

/**
 * @param db a better-sqlite3 handle (payments store exposes `.db`).
 * @param now injectable clock (ms) — the cooldown and the daily budget are
 *        both read back through it, so tests drive them deterministically
 *        instead of sleeping.
 */
function createTreasuryStore(db, { now = Date.now } = {}) {
  db.exec(SCHEMA);
  const nowSec = () => Math.floor(now() / 1000);

  const stmts = {
    insert: db.prepare(`INSERT INTO treasury_actions
      (action_id, kind, status, trigger, created_at, usdc_amount, quote_wei, min_out_wei, pool_fee, destination)
      VALUES (@action_id, @kind, 'submitting', @trigger, @created_at, @usdc_amount, @quote_wei, @min_out_wei, @pool_fee, @destination)`),
    setTx: db.prepare(`UPDATE treasury_actions SET tx_hash=@tx_hash, tx_nonce=@tx_nonce WHERE action_id=@action_id`),
    confirm: db.prepare(`UPDATE treasury_actions SET status='confirmed', confirmed_at=@confirmed_at,
      eth_received_wei=@eth_received_wei, gas_spent_wei=@gas_spent_wei, tx_hash=COALESCE(@tx_hash, tx_hash), error=NULL
      WHERE action_id=@action_id`),
    failed: db.prepare(`UPDATE treasury_actions SET status='failed', error=@error,
      gas_spent_wei=COALESCE(@gas_spent_wei, gas_spent_wei), tx_hash=COALESCE(@tx_hash, tx_hash)
      WHERE action_id=@action_id`),
    byId: db.prepare('SELECT * FROM treasury_actions WHERE action_id = ?'),
    recent: db.prepare('SELECT * FROM treasury_actions ORDER BY created_at DESC, rowid DESC LIMIT ?'),
    recentByKind: db.prepare('SELECT * FROM treasury_actions WHERE kind = ? ORDER BY created_at DESC, rowid DESC LIMIT ?'),
    // Budget basis: money committed, not merely confirmed. A sip whose
    // receipt never came back may still have moved USDC, so it counts.
    sipSpendSince: db.prepare(
      `SELECT usdc_amount FROM treasury_actions
       WHERE kind='sip' AND status IN ('submitting','confirmed') AND created_at >= ?`),
    // Cooldown basis: ATTEMPTS, including failed ones. A failed sip that did
    // not reset the clock would let the module retry in a tight loop.
    lastSipAt: db.prepare("SELECT MAX(created_at) AS t FROM treasury_actions WHERE kind='sip'"),
    lastByKind: db.prepare('SELECT * FROM treasury_actions WHERE kind = ? ORDER BY created_at DESC, rowid DESC LIMIT 1'),
    sums: db.prepare(
      `SELECT kind, status, COUNT(*) AS n, GROUP_CONCAT(COALESCE(usdc_amount,'0')) AS usdc,
              GROUP_CONCAT(COALESCE(eth_received_wei,'0')) AS eth,
              GROUP_CONCAT(COALESCE(gas_spent_wei,'0')) AS gas
       FROM treasury_actions GROUP BY kind, status`),
    getState: db.prepare('SELECT value FROM treasury_state WHERE key = ?'),
    setState: db.prepare(
      `INSERT INTO treasury_state (key, value) VALUES (@key, @value)
       ON CONFLICT(key) DO UPDATE SET value = excluded.value`),
  };

  function sumConcat(s) {
    let total = 0n;
    for (const part of String(s || '').split(',')) {
      if (!part) continue;
      try { total += BigInt(part); } catch (e) { /* ignore malformed */ }
    }
    return total;
  }

  return {
    db,

    /** Record the INTENT before anything is signed or broadcast. */
    begin({ kind, trigger = 'interval', usdcAmount, quoteWei, minOutWei, poolFee, destination }) {
      const actionId = newActionId(kind);
      stmts.insert.run({
        action_id: actionId,
        kind,
        trigger,
        created_at: nowSec(),
        usdc_amount: bigOrNull(usdcAmount),
        quote_wei: bigOrNull(quoteWei),
        min_out_wei: bigOrNull(minOutWei),
        pool_fee: poolFee === undefined ? null : Number(poolFee),
        destination: destination || null,
      });
      return actionId;
    },

    /** Persist the signed tx hash BEFORE broadcasting it. */
    attachTx(actionId, { txHash, txNonce }) {
      stmts.setTx.run({ action_id: actionId, tx_hash: txHash || null, tx_nonce: txNonce === undefined ? null : Number(txNonce) });
    },

    confirm(actionId, { ethReceivedWei, gasSpentWei, txHash } = {}) {
      stmts.confirm.run({
        action_id: actionId,
        confirmed_at: nowSec(),
        eth_received_wei: bigOrNull(ethReceivedWei),
        gas_spent_wei: bigOrNull(gasSpentWei),
        tx_hash: txHash || null,
      });
    },

    fail(actionId, error, { gasSpentWei, txHash } = {}) {
      stmts.failed.run({
        action_id: actionId,
        error: String(error || 'failed').slice(0, 500),
        gas_spent_wei: bigOrNull(gasSpentWei),
        tx_hash: txHash || null,
      });
    },

    get(actionId) {
      return stmts.byId.get(actionId) || null;
    },

    list({ limit = 50, kind } = {}) {
      return kind ? stmts.recentByKind.all(kind, limit) : stmts.recent.all(limit);
    },

    last(kind) {
      return stmts.lastByKind.get(kind) || null;
    },

    /** USDC atomic units committed to sips since a unix-seconds timestamp. */
    sipSpendSince(sinceSec) {
      let total = 0n;
      for (const row of stmts.sipSpendSince.all(Number(sinceSec))) {
        try { total += BigInt(row.usdc_amount || '0'); } catch (e) { /* ignore */ }
      }
      return total;
    },

    /** Unix seconds of the most recent sip ATTEMPT (any status), or 0. */
    lastSipAt() {
      const r = stmts.lastSipAt.get();
      return r && r.t ? Number(r.t) : 0;
    },

    /** Lifetime totals for `treasury status` and the accounting tests. */
    totals() {
      const out = {
        sips: 0, sipsFailed: 0, sweeps: 0, sweepsFailed: 0, approvals: 0,
        sippedUsdcAtomic: 0n, sweptUsdcAtomic: 0n, ethReceivedWei: 0n, gasSpentWei: 0n,
      };
      for (const row of stmts.sums.all()) {
        const usdc = sumConcat(row.usdc);
        out.gasSpentWei += sumConcat(row.gas);
        if (row.kind === 'sip') {
          if (row.status === 'confirmed') {
            out.sips += row.n;
            out.sippedUsdcAtomic += usdc;
            out.ethReceivedWei += sumConcat(row.eth);
          } else if (row.status === 'failed') {
            out.sipsFailed += row.n;
          }
        } else if (row.kind === 'sweep') {
          if (row.status === 'confirmed') {
            out.sweeps += row.n;
            out.sweptUsdcAtomic += usdc;
          } else if (row.status === 'failed') {
            out.sweepsFailed += row.n;
          }
        } else if (row.kind === 'approve' && row.status === 'confirmed') {
          out.approvals += row.n;
        }
      }
      return out;
    },

    getState(key, fallback = null) {
      const r = stmts.getState.get(key);
      return r ? r.value : fallback;
    },

    setState(key, value) {
      stmts.setState.run({ key, value: String(value) });
    },
  };
}

module.exports = { createTreasuryStore, TREASURY_SCHEMA: SCHEMA };
