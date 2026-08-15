'use strict';
/**
 * Treasury ledger — the sip/sweep history, the daily-swap-budget accounting
 * basis, the crash/stuck-transaction recovery material, and the small amount
 * of durable state the failure policy and the cross-process lease need.
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
 * only money already confirmed. For sips the row is written before the
 * APPROVE too, inside the same transaction as the budget check — otherwise
 * two callers can each read a stale total and each get a full allocation
 * (`beginSip`).
 *
 * `raw_tx` + `tx_params` are the treasury's half of crash recovery. Without
 * them a treasury transaction stuck in the mempool could not be rebroadcast or
 * fee-bumped, and every later settlement would sit behind its nonce gap.
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
  raw_tx           TEXT,        -- signed bytes, rebroadcastable as-is
  tx_params        TEXT,        -- JSON: to/data/gasLimit/fees/nonce, for a fee bump
  bump_count       INTEGER NOT NULL DEFAULT 0,
  gas_spent_wei    TEXT,
  error            TEXT
);
CREATE INDEX IF NOT EXISTS idx_treasury_created ON treasury_actions(created_at);
CREATE INDEX IF NOT EXISTS idx_treasury_kind ON treasury_actions(kind, created_at);
CREATE INDEX IF NOT EXISTS idx_treasury_status ON treasury_actions(status, created_at);

CREATE TABLE IF NOT EXISTS treasury_state (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
`;

/** Columns added after the first shipped schema — migrated in place. */
const MIGRATIONS = [
  ['raw_tx', 'ALTER TABLE treasury_actions ADD COLUMN raw_tx TEXT'],
  ['tx_params', 'ALTER TABLE treasury_actions ADD COLUMN tx_params TEXT'],
  ['bump_count', 'ALTER TABLE treasury_actions ADD COLUMN bump_count INTEGER NOT NULL DEFAULT 0'],
];

const LEASE_KEY = 'lease';

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
  const have = new Set(db.prepare('PRAGMA table_info(treasury_actions)').all().map((c) => c.name));
  for (const [column, sql] of MIGRATIONS) if (!have.has(column)) db.exec(sql);

  const nowSec = () => Math.floor(now() / 1000);

  const stmts = {
    insert: db.prepare(`INSERT INTO treasury_actions
      (action_id, kind, status, trigger, created_at, usdc_amount, quote_wei, min_out_wei, pool_fee, destination)
      VALUES (@action_id, @kind, 'submitting', @trigger, @created_at, @usdc_amount, @quote_wei, @min_out_wei, @pool_fee, @destination)`),
    setTx: db.prepare(`UPDATE treasury_actions SET tx_hash=@tx_hash, tx_nonce=@tx_nonce,
      raw_tx=COALESCE(@raw_tx, raw_tx), tx_params=COALESCE(@tx_params, tx_params) WHERE action_id=@action_id`),
    bump: db.prepare(`UPDATE treasury_actions SET tx_hash=@tx_hash, raw_tx=@raw_tx, tx_params=@tx_params,
      bump_count=bump_count+1 WHERE action_id=@action_id`),
    setAmount: db.prepare('UPDATE treasury_actions SET usdc_amount=@usdc_amount WHERE action_id=@action_id'),
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
    lastRealizedSip: db.prepare(
      `SELECT usdc_amount, eth_received_wei, confirmed_at, created_at FROM treasury_actions
       WHERE kind='sip' AND status='confirmed' AND eth_received_wei IS NOT NULL AND usdc_amount IS NOT NULL
       ORDER BY created_at DESC, rowid DESC LIMIT 1`),
    unresolved: db.prepare("SELECT * FROM treasury_actions WHERE status='submitting' ORDER BY created_at, rowid"),
    unresolvedCount: db.prepare("SELECT COUNT(*) AS n FROM treasury_actions WHERE status='submitting'"),
    gasSince: db.prepare('SELECT gas_spent_wei FROM treasury_actions WHERE gas_spent_wei IS NOT NULL AND created_at >= ?'),
    sweepsSince: db.prepare(
      `SELECT COUNT(*) AS n FROM treasury_actions
       WHERE kind='sweep' AND status IN ('submitting','confirmed') AND created_at >= ?`),
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

  function readState(key, fallback = null) {
    const r = stmts.getState.get(key);
    return r ? r.value : fallback;
  }

  function writeState(key, value) {
    stmts.setState.run({ key, value: String(value) });
  }

  function sipSpendSince(sinceSec) {
    let total = 0n;
    for (const row of stmts.sipSpendSince.all(Number(sinceSec))) {
      try { total += BigInt(row.usdc_amount || '0'); } catch (e) { /* ignore */ }
    }
    return total;
  }

  /**
   * Budget check + intent row in ONE write transaction (BEGIN IMMEDIATE takes
   * SQLite's write lock, so a second process — the CLI — serialises behind it
   * instead of reading a stale total). This is what makes
   * X402_TREASURY_DAILY_SWAP_BUDGET_USDC an actual cap rather than a
   * read-then-act suggestion: the row exists before a single wei of gas is
   * spent, so the next caller sees the commitment immediately.
   *
   * Returns { ok: true, actionId, amount } or { ok: false, reason, ... }.
   */
  const beginSipTx = db.transaction(({ amount, minAmount, budget, windowSec, row }) => {
    const spent = sipSpendSince(nowSec() - Number(windowSec));
    const remaining = budget > spent ? budget - spent : 0n;
    if (remaining < minAmount) {
      return { ok: false, reason: 'daily_budget_exhausted', spent, remaining };
    }
    const amountIn = amount > remaining ? remaining : amount;
    const actionId = newActionId('sip');
    stmts.insert.run({
      action_id: actionId,
      kind: 'sip',
      trigger: row.trigger,
      created_at: nowSec(),
      usdc_amount: String(amountIn),
      quote_wei: bigOrNull(row.quoteWei),
      min_out_wei: bigOrNull(row.minOutWei),
      pool_fee: row.poolFee === undefined ? null : Number(row.poolFee),
      destination: row.destination || null,
    });
    return { ok: true, actionId, amount: amountIn, spent, remaining };
  });

  /** Cross-process mutual exclusion for anything that signs (service vs CLI). */
  const acquireLeaseTx = db.transaction(({ owner, ttlSec, label }) => {
    const raw = readState(LEASE_KEY, null);
    let cur = null;
    if (raw) { try { cur = JSON.parse(raw); } catch (e) { cur = null; } }
    const t = nowSec();
    if (cur && cur.owner !== owner && Number(cur.expires_at || 0) > t) {
      return { ok: false, holder: cur };
    }
    const lease = { owner, label: label || '', expires_at: t + Number(ttlSec), renewed_at: t };
    writeState(LEASE_KEY, JSON.stringify(lease));
    return { ok: true, lease };
  });

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

    /**
     * Atomic "is there budget for this sip, and if so claim it" — see
     * beginSipTx. `windowSec` is the trailing budget window (24 h).
     */
    beginSip({ trigger = 'interval', amount, minAmount, budget, windowSec, quoteWei, minOutWei, poolFee, destination }) {
      return beginSipTx.immediate({
        amount: BigInt(amount),
        minAmount: BigInt(minAmount),
        budget: BigInt(budget),
        windowSec: Number(windowSec),
        row: { trigger, quoteWei, minOutWei, poolFee, destination },
      });
    },

    /** Record the size actually swapped once the quote/clamp is final. */
    setAmount(actionId, usdcAmount) {
      stmts.setAmount.run({ action_id: actionId, usdc_amount: bigOrNull(usdcAmount) });
    },

    /** Persist the signed tx (hash, nonce, bytes, bump material) BEFORE broadcasting it. */
    attachTx(actionId, { txHash, txNonce, rawTx, txParams }) {
      stmts.setTx.run({
        action_id: actionId,
        tx_hash: txHash || null,
        tx_nonce: txNonce === undefined ? null : Number(txNonce),
        raw_tx: rawTx || null,
        tx_params: txParams ? JSON.stringify(txParams) : null,
      });
    },

    /** A fee-bumped replacement for the same nonce. */
    bumped(actionId, { txHash, rawTx, txParams }) {
      stmts.bump.run({
        action_id: actionId,
        tx_hash: txHash,
        raw_tx: rawTx,
        tx_params: txParams ? JSON.stringify(txParams) : null,
      });
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

    /** Every action whose on-chain outcome we have not yet established. */
    listUnresolved() {
      return stmts.unresolved.all();
    },

    unresolvedCount() {
      const r = stmts.unresolvedCount.get();
      return r ? Number(r.n) : 0;
    },

    /** USDC atomic units committed to sips since a unix-seconds timestamp. */
    sipSpendSince,

    /** Sweeps started (or confirmed) since a timestamp — the per-day sweep cap. */
    sweepCountSince(sinceSec) {
      const r = stmts.sweepsSince.get(Number(sinceSec));
      return r ? Number(r.n) : 0;
    },

    /** Treasury gas (wei) recorded since a timestamp. */
    gasSpentSince(sinceSec) {
      let total = 0n;
      for (const row of stmts.gasSince.all(Number(sinceSec))) {
        try { total += BigInt(row.gas_spent_wei); } catch (e) { /* ignore */ }
      }
      return total;
    },

    /**
     * The realised wei-per-USDC of the most recent confirmed sip — an
     * independent-ish reference the next quote is sanity-checked against, so a
     * manipulated or stale pool price cannot drag amountOutMinimum down with
     * it (the bound is otherwise derived from the very pool it protects).
     */
    lastRealizedSip() {
      const r = stmts.lastRealizedSip.get();
      if (!r) return null;
      try {
        const usdcAtomic = BigInt(r.usdc_amount);
        const ethWei = BigInt(r.eth_received_wei);
        if (usdcAtomic <= 0n || ethWei <= 0n) return null;
        return { usdcAtomic, ethWei, at: Number(r.confirmed_at || r.created_at || 0) };
      } catch (e) {
        return null;
      }
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

    /* ------------------------------------------------------------ lease -- */

    /**
     * Take (or renew) the exclusive right to sign treasury transactions for
     * this DB. The running facilitator holds it; `animica-x402 treasury
     * sip|sweep --confirm` must take it before it signs, and refuses when the
     * service holds it — two processes on one nonce lane is how a sip steals a
     * settlement's nonce.
     */
    acquireLease(owner, ttlSec, label) {
      return acquireLeaseTx.immediate({ owner, ttlSec, label });
    },

    releaseLease(owner) {
      const raw = readState(LEASE_KEY, null);
      if (!raw) return false;
      try {
        const cur = JSON.parse(raw);
        if (cur.owner !== owner) return false;
      } catch (e) { /* malformed: clear it */ }
      writeState(LEASE_KEY, JSON.stringify({ owner: null, expires_at: 0 }));
      return true;
    },

    /** The current holder, or null when free/expired. */
    leaseHolder() {
      const raw = readState(LEASE_KEY, null);
      if (!raw) return null;
      let cur = null;
      try { cur = JSON.parse(raw); } catch (e) { return null; }
      if (!cur || !cur.owner) return null;
      if (Number(cur.expires_at || 0) <= nowSec()) return null;
      return cur;
    },

    getState: readState,
    setState: writeState,
  };
}

module.exports = { createTreasuryStore, TREASURY_SCHEMA: SCHEMA, LEASE_KEY };
