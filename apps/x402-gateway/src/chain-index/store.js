'use strict';
/**
 * Gateway-owned address index (its OWN sqlite file — the payments ledger in
 * src/store/index.js and the idempotency/incident DB in src/store/gateway.js
 * stay separate schemas). This exists because the chain-data recon
 * (2026-08-15) established that NO account-history index exists anywhere on
 * this box: the node RPC has no history method at all, and the explorer's
 * `/api/address/:bech32` is a live reverse block scan capped at 250 blocks /
 * 3.5 s per call (measured: 3.54 s to return ZERO txs for the most active
 * sender, whose txs sit ~3,000 blocks back). Full history for an old address
 * over that path is ~300 paged calls. The index is the only reason
 * `chain_address_history` is worth money.
 *
 * Two tables:
 *
 *   blocks     — one row per indexed block (height, hash, parent_hash,
 *                timestamp, tx_count). Its only job beyond metadata is
 *                REORG SAFETY: rollbackAbove(h) can restore the exact tip
 *                hash at height h, which a single "last hash" meta value
 *                could not.
 *
 *   address_tx — one row per (account digest, tx) with the direction that
 *                account played in it. A transfer between two distinct
 *                accounts yields TWO rows (out for the sender, in for the
 *                recipient); a self-transfer yields ONE row with
 *                direction='self' so a paid history can never double-count
 *                it. PK (digest, height, tx_index) is exactly the lookup
 *                order the product pages over, so both asc and desc paging
 *                are index scans.
 *
 * Money (value/tip, unit nANM = 1e-9 ANM) is stored as decimal TEXT: live
 * balances already exceed 2^53, and the node client quotes those fields
 * before JSON.parse so they arrive here as exact strings. Nothing in this
 * file ever turns an amount into a JS Number.
 */

const fs = require('node:fs');
const path = require('node:path');

const SCHEMA = `
CREATE TABLE IF NOT EXISTS meta (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS blocks (
  height      INTEGER PRIMARY KEY,
  hash        TEXT NOT NULL,
  parent_hash TEXT,
  timestamp   INTEGER,
  tx_count    INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS address_tx (
  digest       TEXT NOT NULL,
  height       INTEGER NOT NULL,
  tx_index     INTEGER NOT NULL,
  direction    TEXT NOT NULL CHECK (direction IN ('in','out','self')),
  tx_hash      TEXT NOT NULL,
  counterparty TEXT,
  value        TEXT NOT NULL,
  tip          TEXT,
  gas          INTEGER,
  kind         INTEGER,
  block_hash   TEXT,
  timestamp    INTEGER,
  PRIMARY KEY (digest, height, tx_index)
);
CREATE INDEX IF NOT EXISTS idx_address_tx_height ON address_tx(height);
`;

/** 0x-hex (any case) -> bare lowercase hex; anything else -> ''. */
function digestOf(hex0x) {
  return typeof hex0x === 'string' ? hex0x.replace(/^0x/, '').toLowerCase() : '';
}

/** Amounts are decimal strings end to end; never a Number, never a float. */
function amountString(v) {
  if (v === undefined || v === null) return null;
  if (typeof v === 'string') return v;
  if (typeof v === 'bigint') return v.toString();
  // Only reachable if a node response bypassed the BigInt-safe parser; keep
  // it exact for anything below 2^53 and refuse to invent digits above it.
  if (typeof v === 'number' && Number.isSafeInteger(v)) return String(v);
  return String(v);
}

/**
 * Pure block -> index rows. Exported because the direction rule is part of
 * the product's published derivation and must be testable on its own.
 *
 *   from == to  -> one row,  direction 'self'
 *   otherwise   -> one row per side present: 'out' for from, 'in' for to
 */
function extractRows(block) {
  const height = block.number;
  const txs = Array.isArray(block.transactions) ? block.transactions : [];
  const rows = [];
  for (let i = 0; i < txs.length; i++) {
    const t = txs[i] || {};
    const from = digestOf(t.from);
    const to = digestOf(t.to);
    const base = {
      height,
      tx_index: i,
      tx_hash: typeof t.hash === 'string' ? t.hash : '',
      value: amountString(t.value) ?? '0',
      tip: amountString(t.tip),
      gas: Number.isInteger(t.gas) ? t.gas : null,
      kind: Number.isInteger(t.kind) ? t.kind : null,
      block_hash: typeof block.hash === 'string' ? block.hash : null,
      timestamp: Number.isInteger(block.timestamp) ? block.timestamp : null,
    };
    if (from && to && from === to) {
      rows.push(Object.assign({}, base, { digest: from, direction: 'self', counterparty: to }));
      continue;
    }
    if (from) rows.push(Object.assign({}, base, { digest: from, direction: 'out', counterparty: to || null }));
    if (to) rows.push(Object.assign({}, base, { digest: to, direction: 'in', counterparty: from || null }));
  }
  return rows;
}

function createChainIndexStore(dbPath, { Database } = {}) {
  const Db = Database || require('better-sqlite3');
  if (dbPath !== ':memory:') {
    fs.mkdirSync(path.dirname(path.resolve(dbPath)), { recursive: true });
  }
  const db = new Db(dbPath);
  db.pragma('journal_mode = WAL');
  db.pragma('synchronous = NORMAL');
  db.pragma('busy_timeout = 5000');
  db.exec(SCHEMA);

  const stmts = {
    getMeta: db.prepare('SELECT value FROM meta WHERE key = ?'),
    setMeta: db.prepare('INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value'),
    insBlock: db.prepare(`INSERT INTO blocks (height, hash, parent_hash, timestamp, tx_count)
      VALUES (@height, @hash, @parent_hash, @timestamp, @tx_count)
      ON CONFLICT(height) DO UPDATE SET hash = excluded.hash, parent_hash = excluded.parent_hash,
        timestamp = excluded.timestamp, tx_count = excluded.tx_count`),
    insRow: db.prepare(`INSERT INTO address_tx
      (digest, height, tx_index, direction, tx_hash, counterparty, value, tip, gas, kind, block_hash, timestamp)
      VALUES (@digest, @height, @tx_index, @direction, @tx_hash, @counterparty, @value, @tip, @gas, @kind, @block_hash, @timestamp)
      ON CONFLICT(digest, height, tx_index) DO UPDATE SET
        direction = excluded.direction, tx_hash = excluded.tx_hash, counterparty = excluded.counterparty,
        value = excluded.value, tip = excluded.tip, gas = excluded.gas, kind = excluded.kind,
        block_hash = excluded.block_hash, timestamp = excluded.timestamp`),
    delRowsAbove: db.prepare('DELETE FROM address_tx WHERE height > ?'),
    delBlocksAbove: db.prepare('DELETE FROM blocks WHERE height > ?'),
    blockHashAt: db.prepare('SELECT hash FROM blocks WHERE height = ?'),
    countRows: db.prepare('SELECT COUNT(*) AS n FROM address_tx'),
    countBlocks: db.prepare('SELECT COUNT(*) AS n FROM blocks'),
  };

  function getMeta(key, fallback = null) {
    const r = stmts.getMeta.get(key);
    return r ? r.value : fallback;
  }
  function setMeta(key, value) {
    stmts.setMeta.run(String(key), String(value));
  }
  function metaInt(key, fallback) {
    const v = getMeta(key, null);
    if (v === null) return fallback;
    const n = Number(v);
    return Number.isFinite(n) ? n : fallback;
  }

  /**
   * `indexed_height` is the highest FULLY indexed height (-1 = empty). It is
   * written inside the same transaction as the rows it describes, so a crash
   * can never leave it pointing past data that was not committed.
   */
  function getState() {
    return {
      indexedHeight: metaInt('indexed_height', -1),
      indexedHash: getMeta('indexed_hash', null),
      chainId: metaInt('chain_id', null),
      headHeight: metaInt('head_height', null),
      lastTickMs: metaInt('last_tick_ms', 0),
      caughtUpMs: metaInt('caught_up_ms', 0),
      startedMs: metaInt('started_ms', 0),
    };
  }

  const applyBlocks = db.transaction((blocks) => {
    let last = null;
    for (const raw of blocks) {
      const rows = extractRows(raw);
      stmts.insBlock.run({
        height: raw.number,
        hash: typeof raw.hash === 'string' ? raw.hash : '',
        parent_hash: typeof raw.parentHash === 'string' ? raw.parentHash : null,
        timestamp: Number.isInteger(raw.timestamp) ? raw.timestamp : null,
        tx_count: Array.isArray(raw.transactions) ? raw.transactions.length : 0,
      });
      for (const row of rows) stmts.insRow.run(row);
      if (raw.chainId !== undefined && raw.chainId !== null) setMeta('chain_id', raw.chainId);
      last = raw;
    }
    if (last) {
      setMeta('indexed_height', last.number);
      setMeta('indexed_hash', typeof last.hash === 'string' ? last.hash : '');
    }
    return last ? last.number : null;
  });

  const rollbackAbove = db.transaction((height) => {
    const h = Math.max(-1, Math.floor(height));
    stmts.delRowsAbove.run(h);
    stmts.delBlocksAbove.run(h);
    setMeta('indexed_height', h);
    const row = h >= 0 ? stmts.blockHashAt.get(h) : null;
    setMeta('indexed_hash', row ? row.hash : '');
    return h;
  });

  return {
    db, // tests/CLI only

    getState,
    getMeta,
    setMeta,

    /** Insert a contiguous, already-continuity-checked run of blocks. */
    applyBlocks,

    /** Drop everything above `height` and restore that height's tip hash. */
    rollbackAbove,

    /**
     * Walker heartbeat. `caught_up_ms` is set the FIRST time the index is
     * within maxLagBlocks of the head — that is what lets the product tell a
     * buyer "still backfilling" apart from "was live, now stale".
     */
    recordTick({ headHeight, atMs, maxLagBlocks }) {
      setMeta('last_tick_ms', Math.floor(atMs));
      if (Number.isInteger(headHeight)) setMeta('head_height', headHeight);
      if (!metaInt('started_ms', 0)) setMeta('started_ms', Math.floor(atMs));
      const st = getState();
      if (
        Number.isInteger(headHeight) &&
        st.indexedHeight >= 0 &&
        headHeight - st.indexedHeight <= Number(maxLagBlocks)
      ) {
        setMeta('caught_up_ms', Math.floor(atMs));
      }
    },

    /**
     * One page of an account's history.
     *
     *   digest     bare lowercase 32-byte hex (see src/bech32m.js)
     *   fromHeight/toHeight  inclusive window
     *   direction  'any' | 'in' | 'out' | 'self'
     *   order      'desc' (newest first, default) | 'asc'
     *   after      {height, txIndex} — STRICTLY after in the chosen order
     *   limit      rows to return
     *
     * Returns { rows, hasMore }: `limit + 1` rows are fetched so `hasMore` is
     * exact without a second query.
     */
    queryHistory({ digest, fromHeight = 0, toHeight, direction = 'any', order = 'desc', after = null, limit = 100 }) {
      const desc = order !== 'asc';
      const where = ['digest = @digest', 'height >= @fromHeight', 'height <= @toHeight'];
      const params = { digest, fromHeight, toHeight, limit: limit + 1 };
      if (direction !== 'any') {
        where.push('direction = @direction');
        params.direction = direction;
      }
      if (after) {
        params.afterHeight = after.height;
        params.afterIndex = after.txIndex;
        where.push(desc
          ? '(height < @afterHeight OR (height = @afterHeight AND tx_index < @afterIndex))'
          : '(height > @afterHeight OR (height = @afterHeight AND tx_index > @afterIndex))');
      }
      const sql =
        `SELECT height, tx_index, direction, tx_hash, counterparty, value, tip, gas, kind, block_hash, timestamp
         FROM address_tx WHERE ${where.join(' AND ')}
         ORDER BY height ${desc ? 'DESC' : 'ASC'}, tx_index ${desc ? 'DESC' : 'ASC'}
         LIMIT @limit`;
      const rows = db.prepare(sql).all(params);
      const hasMore = rows.length > limit;
      return { rows: hasMore ? rows.slice(0, limit) : rows, hasMore };
    },

    /** Total matching rows in the same window/filter (the chain is small). */
    countHistory({ digest, fromHeight = 0, toHeight, direction = 'any' }) {
      const where = ['digest = @digest', 'height >= @fromHeight', 'height <= @toHeight'];
      const params = { digest, fromHeight, toHeight };
      if (direction !== 'any') {
        where.push('direction = @direction');
        params.direction = direction;
      }
      return db.prepare(`SELECT COUNT(*) AS n FROM address_tx WHERE ${where.join(' AND ')}`).get(params).n;
    },

    stats() {
      const st = getState();
      return Object.assign({}, st, {
        rows: stmts.countRows.get().n,
        blocks: stmts.countBlocks.get().n,
      });
    },

    ping() {
      db.prepare('SELECT 1').get();
      return true;
    },

    close() {
      db.close();
    },
  };
}

module.exports = { createChainIndexStore, extractRows, digestOf, amountString };
