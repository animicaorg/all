'use strict';
/**
 * GET /x402/stats — aggregate settlement statistics.
 *
 * Source of truth: the FACILITATOR's payments ledger (src/store/index.js,
 * X402_DB_PATH), opened READ-ONLY. That table is the only place a settlement
 * is recorded on-chain-confirmed, so counting anything else would be
 * counting intentions instead of money.
 *
 * Three deliberate restrictions:
 *
 *   1. Aggregate only. Payer addresses, transaction hashes, per-payment rows
 *      and amounts never leave this endpoint. The spec asks for counts, and
 *      counts are all the store is read for.
 *   2. Resources are mapped onto KNOWN product routes; anything unrecognised
 *      is bucketed as `other` and never echoed. The `resource` column comes
 *      from the payment payload, i.e. from the client — reflecting it
 *      verbatim on a public page would be a stored-text injection with extra
 *      steps.
 *   3. Unknown is reported as unknown. When the ledger is absent (facilitator
 *      never started, or X402_FACILITATOR_MODE=remote, where settlements live
 *      in someone else's database) the endpoint says so with a reason instead
 *      of publishing a confident zero.
 *
 * The DB handle is opened lazily, cached, and re-opened after a failure; the
 * aggregate itself is cached for a few seconds so a crawler cannot turn this
 * into a query loop against the settlement ledger.
 */

const fs = require('node:fs');

const { identity, networkFacts } = require('./links');

const DEFAULT_TTL_MS = 10_000;

/** Last path segment normalisation: '/x402/qrng/draw?x=1' -> '/x402/qrng/draw'. */
function pathOfResource(resource) {
  const s = String(resource || '');
  if (!s) return '';
  try {
    // Resources are absolute URLs in practice; tolerate bare paths too.
    return new URL(s, 'http://placeholder.invalid').pathname.replace(/\/+$/, '') || '/';
  } catch {
    return '';
  }
}

/**
 * @param {object}   opts.cfg       gateway config (settlementDbPath, network, asset)
 * @param {object}   opts.registry  product registry (route path -> product id)
 * @param {Function} opts.Database  better-sqlite3 ctor override (tests)
 */
function createSettlementStats({ cfg, registry, Database, now = Date.now, ttlMs = DEFAULT_TTL_MS }) {
  const dbPath = cfg.settlementDbPath;
  let db = null;
  let cached = null;
  let cachedAt = 0;

  // route path -> product id, built once from the registry: the ONLY strings
  // that can appear in the per-product breakdown.
  const routeToProduct = new Map();
  for (const p of registry.products) {
    if (p.devOnly) continue;
    for (const r of p.routes) routeToProduct.set(r.path, p.id);
  }

  function open() {
    if (db) return db;
    if (cfg.facilitatorMode !== 'self') {
      const e = new Error('settlements are recorded by the external facilitator configured in X402_FACILITATOR_MODE=remote, not by this gateway');
      e.reason = 'external_facilitator';
      throw e;
    }
    if (dbPath !== ':memory:' && !fs.existsSync(dbPath)) {
      const e = new Error('no settlement ledger yet — the facilitator has not recorded a payment on this host');
      e.reason = 'settlement_store_empty';
      throw e;
    }
    const Db = Database || require('better-sqlite3');
    db = new Db(dbPath, { readonly: true, fileMustExist: true });
    db.pragma('busy_timeout = 2000');
    return db;
  }

  function compute() {
    const handle = open();
    const dayAgo = Math.floor(now() / 1000) - 86_400;
    const rows = handle
      .prepare("SELECT resource, settled_at FROM payments WHERE status = 'settled'")
      .all();
    const perProduct = new Map();
    let total = 0;
    let last24h = 0;
    let firstAt = null;
    let lastAt = null;
    for (const row of rows) {
      total += 1;
      const settledAt = Number(row.settled_at || 0);
      if (settledAt) {
        if (settledAt >= dayAgo) last24h += 1;
        if (firstAt === null || settledAt < firstAt) firstAt = settledAt;
        if (lastAt === null || settledAt > lastAt) lastAt = settledAt;
      }
      const id = routeToProduct.get(pathOfResource(row.resource)) || 'other';
      const bucket = perProduct.get(id) || { settled_total: 0, settled_24h: 0 };
      bucket.settled_total += 1;
      if (settledAt && settledAt >= dayAgo) bucket.settled_24h += 1;
      perProduct.set(id, bucket);
    }
    return {
      available: true,
      settlements: {
        settled_total: total,
        settled_24h: last24h,
        paid_requests_served_total: total,
        first_settled_at: firstAt ? new Date(firstAt * 1000).toISOString() : null,
        last_settled_at: lastAt ? new Date(lastAt * 1000).toISOString() : null,
      },
      products: [...perProduct.entries()]
        .map(([id, v]) => Object.assign({ id }, v))
        .sort((a, b) => b.settled_total - a.settled_total || a.id.localeCompare(b.id)),
    };
  }

  /** Public aggregate. Never throws: an unreadable ledger is a reported fact. */
  function snapshot() {
    if (cached && now() - cachedAt < ttlMs) return cached;
    const facts = networkFacts(cfg);
    const base = {
      name: identity(cfg).name,
      payment_protocol: facts.payment_protocol,
      network: facts.network,
      network_caip2: facts.network_caip2,
      chain_id: facts.chain_id,
      asset: facts.asset,
      asset_address: facts.asset_address,
      generated_at: new Date(now()).toISOString(),
      source: 'settlement-ledger',
      scope: 'aggregate only: no payer addresses, no transaction hashes, no amounts, no per-payment rows. Counts cover PAID requests that settled on-chain; free catalog, reveal and health traffic is not counted.',
    };
    let body;
    try {
      body = compute();
    } catch (e) {
      // Drop a broken handle so the next call re-opens rather than sticking.
      try { if (db) db.close(); } catch { /* already gone */ }
      db = null;
      body = {
        available: false,
        reason: e.reason || 'settlement_store_unavailable',
        detail: e.message,
        settlements: null,
        products: [],
      };
    }
    cached = Object.assign(base, body);
    cachedAt = now();
    return cached;
  }

  function close() {
    try { if (db) db.close(); } catch { /* nothing to do */ }
    db = null;
  }

  return { snapshot, close, pathOfResource };
}

module.exports = { createSettlementStats, pathOfResource };
