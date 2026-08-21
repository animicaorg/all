'use strict';
/**
 * GET /x402/stats — aggregate settlement statistics.
 *
 * TWO SOURCES, ONE PER FACILITATOR MODE. Which one is authoritative depends on
 * who settles the money, and that is not a detail this endpoint gets to blur:
 *
 *   self   → the FACILITATOR's payments ledger (src/store/index.js,
 *            X402_DB_PATH). It records an on-chain-confirmed settlement, so
 *            counting anything else would be counting intentions.
 *   remote → `remote_settlements` in the gateway DB (X402_GATEWAY_DB_PATH).
 *            A third-party facilitator writes no ledger we can read; since
 *            2026-08-19 the USDC lane settles at CDP. The gateway therefore
 *            records what it OBSERVES itself — one row per settlement the
 *            facilitator confirmed to us — which is the same record the
 *            `settlements` / `revenue` CLI commands report from.
 *
 * WHY THE REMOTE SOURCE WAS ADDED (2026-08-20). Until now `remote` returned
 * `available:false, reason:"external_facilitator"`. That was honest about the
 * ledger but wrong about the endpoint's job: the third-party uptime and trust
 * crawlers that discovered us (x402-observer, mri-indexer, AgentScore) read
 * exactly this document to score whether a merchant is alive, and it told
 * every one of them that we know nothing about our own sales. We do know: the
 * gateway saw each settlement, knows the product and holds the confirmation.
 * Publishing an observed count is strictly more truthful than publishing
 * nothing, and the `source` field names which record answered so a reader can
 * weigh it. An honest zero from a live observer is still a fact; it is only
 * `available:false` when we genuinely cannot see.
 *
 * Three deliberate restrictions, unchanged by the second source:
 *
 *   1. Aggregate only. Payer addresses, transaction hashes, per-payment rows
 *      and amounts never leave this endpoint. The spec asks for counts, and
 *      counts are all the store is read for.
 *   2. Product ids are mapped onto the KNOWN registry; anything unrecognised
 *      is bucketed as `other` and never echoed. On the self ledger the
 *      `resource` column comes from the payment payload, i.e. from the client
 *      — reflecting it verbatim on a public page would be a stored-text
 *      injection with extra steps. The remote table's `product` column is
 *      server-written, but it is filtered identically rather than trusted,
 *      because "this column happens to be safe today" is not a property worth
 *      depending on.
 *   3. Unknown is reported as unknown. When neither record can be opened the
 *      endpoint says so with a reason instead of publishing a confident zero.
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
  const remoteMode = cfg.facilitatorMode !== 'self';
  // Which file answers is decided ONCE, by who settles the money. A remote
  // facilitator's settlements are never in our payments ledger and ours are
  // never in remote_settlements, so there is no merging to do and no risk of
  // counting a payment twice.
  const dbPath = remoteMode ? cfg.gatewayDbPath : cfg.settlementDbPath;
  const SOURCE = remoteMode ? 'gateway-observed-remote-settlements' : 'settlement-ledger';
  let db = null;
  let cached = null;
  let cachedAt = 0;

  // route path -> product id, and the set of product ids: the ONLY strings that
  // can appear in the per-product breakdown, whichever record is read.
  const routeToProduct = new Map();
  const knownProductIds = new Set();
  for (const p of registry.products) {
    if (p.devOnly) continue;
    knownProductIds.add(p.id);
    for (const r of p.routes) routeToProduct.set(r.path, p.id);
  }

  function open() {
    if (db) return db;
    if (dbPath !== ':memory:' && !fs.existsSync(dbPath)) {
      const e = new Error(remoteMode
        ? 'no gateway database yet — this host has not recorded a remotely settled payment'
        : 'no settlement ledger yet — the facilitator has not recorded a payment on this host');
      e.reason = 'settlement_store_empty';
      throw e;
    }
    const Db = Database || require('better-sqlite3');
    db = new Db(dbPath, { readonly: true, fileMustExist: true });
    db.pragma('busy_timeout = 2000');
    // A gateway DB predating the remote-settlement lane has no such table.
    // That is "we cannot see", not "nothing sold", so it must not read as zero.
    if (remoteMode) {
      const t = db.prepare("SELECT name FROM sqlite_master WHERE type='table' AND name='remote_settlements'").get();
      if (!t) {
        const e = new Error('this gateway database predates the remote-settlement record, so observed settlements cannot be counted');
        e.reason = 'remote_settlement_record_absent';
        throw e;
      }
    }
    return db;
  }

  function compute() {
    const handle = open();
    const dayAgo = Math.floor(now() / 1000) - 86_400;
    // Both records are read as (product-identifying column, settled_at) pairs,
    // so a single aggregation covers them. The remote table stores a product
    // id directly; the ledger stores a client-supplied resource URL.
    const rows = remoteMode
      ? handle.prepare('SELECT product AS id, settled_at FROM remote_settlements').all()
      : handle.prepare("SELECT resource, settled_at FROM payments WHERE status = 'settled'").all();
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
      const id = remoteMode
        ? (knownProductIds.has(row.id) ? row.id : 'other')
        : (routeToProduct.get(pathOfResource(row.resource)) || 'other');
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
      source: SOURCE,
      settled_by: remoteMode ? 'external-facilitator' : 'self-hosted-facilitator',
      scope: remoteMode
        ? 'aggregate only: no payer addresses, no transaction hashes, no amounts, no per-payment rows. Counts cover PAID requests this gateway observed an external facilitator settle on-chain; free catalog, reveal and health traffic is not counted.'
        : 'aggregate only: no payer addresses, no transaction hashes, no amounts, no per-payment rows. Counts cover PAID requests that settled on-chain; free catalog, reveal and health traffic is not counted.',
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
