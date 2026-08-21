'use strict';
/**
 * Gateway-side persistent state (its OWN sqlite file — the facilitator's
 * payments ledger in ./index.js stays that process's private schema):
 *
 *   idempotency — (idem_key, payment_fingerprint) -> the delivered response.
 *     A retry carrying the SAME payment payload and the SAME Idempotency-Key
 *     replays the stored result with no facilitator round-trip, so a client
 *     that paid once can never be charged twice for a network hiccup. The
 *     PRIMARY KEY makes the first writer win; concurrent duplicates land on
 *     INSERT OR IGNORE. Only *delivered* outcomes are stored (success or a
 *     signed error receipt) — a failed settlement stores nothing, because
 *     the payer keeps their money and a later retry must run for real.
 *
 *   incidents — every payment that settled but whose service failed (or
 *     whose settlement outcome was ambiguous). status open|refunded|resolved
 *     is the operator's reconciliation workflow (bin/animica-x402 incidents).
 *
 *   random_commitments — the commit-reveal product (random_commit). The paid
 *     POST stores the sealed secret+salt and the draw that produced them; the
 *     FREE public GET /x402/random/reveal/{id} discloses them so anyone can
 *     check commitment == sha3_256(secret||salt). Reveal must be idempotent
 *     and must never need a payment, so revealed_at is written once (first
 *     disclosure) and never gates a later read. Rows carry no payment
 *     material — the commitment is written during the paid request's execute
 *     phase, BEFORE settlement, so a failed settlement leaves an orphan row
 *     whose id was never disclosed to anyone; pruning collects it.
 *
 * Bodies are capped (X402_IDEMPOTENCY_MAX_BODY_BYTES): an oversize result
 * stores a deterministic status reference instead of megabytes of export.
 */

const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');

const SCHEMA = `
CREATE TABLE IF NOT EXISTS idempotency (
  idem_key            TEXT NOT NULL,
  payment_fingerprint TEXT NOT NULL,
  resource            TEXT NOT NULL,
  response_status     INTEGER NOT NULL,
  content_type        TEXT,
  content_encoding    TEXT,
  body                BLOB,
  oversize            INTEGER NOT NULL DEFAULT 0,
  settlement_header   TEXT,
  settlement_tx       TEXT,
  created_at          INTEGER NOT NULL,
  PRIMARY KEY (idem_key, payment_fingerprint)
);
CREATE INDEX IF NOT EXISTS idx_idem_created ON idempotency(created_at);

-- Free-trial quota: how many unpaid sample calls a client has spent on a
-- product today. Keyed on (product, client, day) so the quota resets at UTC
-- midnight without a sweeper. This is deliberately a WEAK control — it is
-- keyed on client IP, which an agent can rotate — because its job is to stop
-- casual over-use of a free sample, not to be an authorization boundary. The
-- real protection is that every trial-eligible product is cheap to serve and
-- the per-product cap is small.
CREATE TABLE IF NOT EXISTS trial_usage (
  product    TEXT NOT NULL,
  client     TEXT NOT NULL,
  day        TEXT NOT NULL,
  used       INTEGER NOT NULL DEFAULT 0,
  created_at INTEGER NOT NULL,
  PRIMARY KEY (product, client, day)
);
CREATE INDEX IF NOT EXISTS idx_trial_created ON trial_usage(created_at);

CREATE TABLE IF NOT EXISTS incidents (
  incident_id         TEXT PRIMARY KEY,
  payment_id          TEXT,
  payment_fingerprint TEXT,
  settlement_tx       TEXT,
  payer               TEXT,
  resource            TEXT NOT NULL,
  amount              TEXT,
  network             TEXT,
  kind                TEXT NOT NULL,
  error               TEXT NOT NULL,
  receipt_json        TEXT,
  auth_nonce          TEXT,
  status              TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','refunded','resolved')),
  created_at          INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_incidents_status ON incidents(status);
CREATE INDEX IF NOT EXISTS idx_incidents_auth_nonce ON incidents(auth_nonce);

CREATE TABLE IF NOT EXISTS random_commitments (
  commit_id           TEXT PRIMARY KEY,
  commitment          TEXT NOT NULL,
  algorithm           TEXT NOT NULL,
  kind                TEXT NOT NULL,
  request_id          TEXT,
  memo                TEXT,
  secret_hex          TEXT NOT NULL,
  salt_hex            TEXT NOT NULL,
  draw_json           TEXT NOT NULL,
  reveal_after        INTEGER NOT NULL,
  created_at          INTEGER NOT NULL,
  revealed_at         INTEGER
);
CREATE INDEX IF NOT EXISTS idx_commit_created ON random_commitments(created_at);
CREATE INDEX IF NOT EXISTS idx_commit_reveal_after ON random_commitments(reveal_after);

-- Prepaid credit vouchers. THE POINT: one on-chain settlement buys many
-- calls. Every settlement costs the gateway sponsored Base gas (~$0.002-0.004
-- measured), which is why no per-call product can be priced below ~half a
-- cent. A voucher amortises that ONE gas cost over N calls, so cheap
-- per-unit products (embeddings, fetch, chain reads) become possible at all.
--
-- Money is TEXT and only ever passes through BigInt — the same rule the rest
-- of this codebase follows. A JS Number would silently lose atomic units.
-- The token itself is NEVER stored: voucher_id is sha256(token), so a stolen
-- database cannot spend anyone's balance.
CREATE TABLE IF NOT EXISTS credit_vouchers (
  voucher_id      TEXT PRIMARY KEY,
  label           TEXT,
  minted_atomic   TEXT NOT NULL,
  bonus_atomic    TEXT NOT NULL DEFAULT '0',
  balance_atomic  TEXT NOT NULL,
  payer           TEXT,
  settlement_tx   TEXT,
  created_at      INTEGER NOT NULL,
  expires_at      INTEGER NOT NULL,
  revoked         INTEGER NOT NULL DEFAULT 0,
  last_used_at    INTEGER,
  spend_count     INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_voucher_expires ON credit_vouchers(expires_at);

-- Every debit, so a buyer can audit where their credit went and an operator
-- can reconcile credit-served calls against settlements that never happened
-- on-chain (by design — that is what they prepaid for).
CREATE TABLE IF NOT EXISTS credit_ledger (
  entry_id       TEXT PRIMARY KEY,
  voucher_id     TEXT NOT NULL,
  product        TEXT NOT NULL,
  resource       TEXT NOT NULL,
  amount_atomic  TEXT NOT NULL,
  balance_after  TEXT NOT NULL,
  request_id     TEXT,
  created_at     INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_credit_ledger_voucher ON credit_ledger(voucher_id, created_at);

-- ANM-lane payments. A signed transaction is a BEARER instrument: the same
-- bytes must never buy two calls. The chain would reject the duplicate on its
-- own (salt/nonce), but we must not DELIVER the second call before finding
-- that out — so replay is settled here, before the handler runs.
CREATE TABLE IF NOT EXISTS anm_payments (
  txid        TEXT PRIMARY KEY,
  payer       TEXT,
  amount_nanm TEXT NOT NULL,
  resource    TEXT,
  status      TEXT NOT NULL CHECK (status IN ('submitted','settled','failed','unknown')),
  created_at  INTEGER NOT NULL,
  settled_at  INTEGER
);
CREATE INDEX IF NOT EXISTS idx_anm_payments_status ON anm_payments(status, created_at);

-- ANM 402 SCAN: the public directory of x402 services that settle in ANM.
-- "verified" means WE probed the URL and got a real 402 advertising an
-- animica:* lane — never merely that someone submitted it.
CREATE TABLE IF NOT EXISTS scan_services (
  service_id     TEXT PRIMARY KEY,
  url            TEXT NOT NULL UNIQUE,
  host           TEXT NOT NULL,
  name           TEXT,
  description    TEXT,
  provider       TEXT,
  contact        TEXT,
  http_method    TEXT NOT NULL DEFAULT 'GET',
  price_nanm     TEXT,
  price_display  TEXT,
  pay_to         TEXT,
  network        TEXT,
  asset          TEXT,
  category       TEXT,
  verified       INTEGER NOT NULL DEFAULT 0,
  status         TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','live','dead','rejected')),
  last_probe_at  INTEGER,
  last_ok_at     INTEGER,
  probe_detail   TEXT,
  fail_count     INTEGER NOT NULL DEFAULT 0,
  submitted_by   TEXT,
  created_at     INTEGER NOT NULL,
  updated_at     INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_scan_status ON scan_services(status, last_probe_at);
CREATE INDEX IF NOT EXISTS idx_scan_host ON scan_services(host);

-- Adoption bounty claims. A claim is only ever RESERVED here; the payout is
-- signed by an operator, because this process holds no treasury key.
CREATE TABLE IF NOT EXISTS bounty_claims (
  claim_id       TEXT PRIMARY KEY,
  service_id     TEXT,
  url            TEXT NOT NULL,
  host           TEXT NOT NULL,
  payout_address TEXT NOT NULL,
  amount_usd     TEXT NOT NULL,
  amount_nanm    TEXT NOT NULL,
  rate_usd_anm   TEXT NOT NULL,
  status         TEXT NOT NULL DEFAULT 'pending'
                 CHECK (status IN ('pending','verified','reserved','paid','rejected','expired')),
  reason         TEXT,
  payout_txid    TEXT,
  created_at     INTEGER NOT NULL,
  updated_at     INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_bounty_status ON bounty_claims(status, created_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_bounty_host_open
  ON bounty_claims(host) WHERE status IN ('pending','verified','reserved','paid');

-- Block-reward share leases against the TREASURY's own 25% of each block.
CREATE TABLE IF NOT EXISTS reward_leases (
  lease_id       TEXT PRIMARY KEY,
  buyer_address  TEXT NOT NULL,
  share_bps      INTEGER NOT NULL,
  start_height   INTEGER NOT NULL,
  end_height     INTEGER NOT NULL,
  paid_usd       TEXT NOT NULL,
  quoted_nanm    TEXT NOT NULL,
  rate_usd_anm   TEXT NOT NULL,
  accrued_nanm   TEXT NOT NULL DEFAULT '0',
  settled_nanm   TEXT NOT NULL DEFAULT '0',
  last_height    INTEGER,
  status         TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','complete','cancelled')),
  created_at     INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_lease_window ON reward_leases(status, start_height, end_height);

-- Notarised forecasts. The PRODUCT is the record, not the prediction: anyone
-- can claim after the fact that they called it, so what is sold is a
-- timestamped, tamper-evident, publicly verifiable statement of what was
-- believed BEFORE the outcome. Both the model estimate and the live market
-- price are stored, because publishing only ours would imply a superiority we
-- have not earned. Resolution and Brier scores are filled in later so the
-- track record can be published honestly, including when we are worse.
CREATE TABLE IF NOT EXISTS forecasts (
  forecast_id     TEXT PRIMARY KEY,
  commitment      TEXT NOT NULL,
  blob_id         TEXT,
  question        TEXT NOT NULL,
  market_id       TEXT,
  market_slug     TEXT,
  market_price    TEXT,
  model_prob      TEXT NOT NULL,
  model_reasoning TEXT,
  model_name      TEXT,
  head_height     INTEGER,
  head_hash       TEXT,
  end_date        TEXT,
  created_at      INTEGER NOT NULL,
  resolved_at     INTEGER,
  resolved_outcome TEXT,
  brier_model     TEXT,
  brier_market    TEXT
);
CREATE INDEX IF NOT EXISTS idx_forecast_commit ON forecasts(commitment);
CREATE INDEX IF NOT EXISTS idx_forecast_open ON forecasts(resolved_at, market_id);

-- mesh_probes — what we learned by actually CALLING an indexed x402 resource
-- without paying. A 402 is the SUCCESS case: it is the merchant's own
-- authoritative statement of price and payment terms, and frequently carries
-- the request schema the directory omitted. Anything else is still worth
-- recording: a 404 or a connection failure is reliability data no directory
-- publishes, and a 200 means the resource is not actually paywalled at all.
CREATE TABLE IF NOT EXISTS mesh_probes (
  key             TEXT PRIMARY KEY,   -- canonical host+path, same key the index merges on
  resource        TEXT NOT NULL,
  method          TEXT,               -- the verb that actually elicited a 402
  outcome         TEXT NOT NULL,      -- paywalled | open | dead | error | blocked
  http_status     INTEGER,
  price_atomic    TEXT,               -- from the merchant's own accepts[], not the directory
  price_usd       TEXT,
  asset           TEXT,
  network         TEXT,
  pay_to          TEXT,
  scheme          TEXT,
  max_timeout_s   INTEGER,
  call_spec_json  TEXT,               -- request shape, when the 402 published one
  accepts_json    TEXT,
  error           TEXT,
  latency_ms      INTEGER,
  probed_at       INTEGER NOT NULL,
  attempts        INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_mesh_probe_at ON mesh_probes(probed_at);
CREATE INDEX IF NOT EXISTS idx_mesh_probe_outcome ON mesh_probes(outcome, probed_at);

-- exec_spend — every dollar Animica spends buying somebody else's x402 call,
-- on our own wallet, on a caller's behalf. Separate from the payments ledger
-- because this is money going OUT, and a daily cap that lives only in memory
-- is not a cap: a restart would reset it.
CREATE TABLE IF NOT EXISTS exec_spend (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  day         TEXT NOT NULL,
  resource    TEXT NOT NULL,
  spent_usd   TEXT NOT NULL,
  outcome     TEXT NOT NULL,
  request_id  TEXT,
  spent_at    INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_exec_spend_day ON exec_spend(day);

-- mesh_index_snapshot — the merged directory harvest, persisted.
-- Without this, every process restart re-harvested two third-party directories
-- from scratch. Five deploys in twenty minutes is five full sweeps of somebody
-- else's API, which is how you earn a 429 and lose the source entirely. The
-- snapshot makes a restart free and the harvest genuinely periodic.
-- remote_settlements — payments settled by a THIRD-PARTY facilitator.
--
-- WHY THIS TABLE EXISTS. Our own facilitator writes every settlement to its own
-- payments ledger, which is what animica-x402 settlements|revenue|gas report|
-- reconcile all read. The moment settlement moved to the CDP facilitator
-- (2026-08-19) those commands went BLIND for the USDC lane: the money still
-- arrives at our payTo, but nothing local records that it did. An operator
-- running revenue would see the last self-settled payment and reasonably
-- conclude nothing had sold since.
--
-- So the gateway records what it observes itself: it knows the product, the
-- price, the payer and the transaction hash the facilitator returned. This is
-- NOT a replacement for the facilitator ledger — there is no gas accounting
-- here, because we no longer pay the gas — it is the record of what we sold.
CREATE TABLE IF NOT EXISTS remote_settlements (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  settled_at    INTEGER NOT NULL,
  product       TEXT NOT NULL,
  resource      TEXT,
  payer         TEXT,
  tx            TEXT,
  network       TEXT,
  asset         TEXT,
  amount_atomic TEXT NOT NULL,
  price_usd     TEXT,
  facilitator   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_remote_settled_at ON remote_settlements(settled_at);
-- A settlement tx is unique; recording one twice would double-count revenue.
-- Partial, because a settlement can legitimately return an empty tx.
CREATE UNIQUE INDEX IF NOT EXISTS idx_remote_settle_tx
  ON remote_settlements(tx) WHERE tx IS NOT NULL AND tx != '';

-- analytics_market_history — one row per recorded observation of a market
-- segment's aggregate statistics.
--
-- WHY THIS TABLE EXISTS. The mesh index is a SNAPSHOT: it says what the x402
-- economy looks like right now and nothing at all about where it is heading.
-- An analytics product that inferred a trend from one snapshot would be making
-- it up. So each analytics/market call records the aggregate it just computed,
-- keyed by the segment, and trend becomes available once two observations of
-- the SAME segment exist. Until then the response says insufficient_history
-- rather than guessing a direction of travel.
--
-- Writes are rate-limited per segment (analyticsSnapshotMinIntervalMs) so a
-- popular segment cannot turn this into a per-request append log, and old rows
-- are pruned per segment rather than globally — a rarely-queried segment's two
-- data points are the only history it will ever have and must not be evicted
-- by a busy one.
CREATE TABLE IF NOT EXISTS analytics_market_history (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  segment_key   TEXT NOT NULL,      -- normalised segment + filters
  segment       TEXT,               -- the caller's words, for readability
  observed_at   INTEGER NOT NULL,   -- unix seconds
  stats_json    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_analytics_hist ON analytics_market_history(segment_key, observed_at DESC);

CREATE TABLE IF NOT EXISTS mesh_index_snapshot (
  id           INTEGER PRIMARY KEY CHECK (id = 1),
  harvested_at INTEGER NOT NULL,
  counts_json  TEXT NOT NULL,
  records_json TEXT NOT NULL
);

-- ---------------------------------------------------------------- PAID CRAWL
-- Sites that have pointed their edge at the crawl gate. Registration is FREE
-- and unauthenticated by design: the operator side of this product never pays
-- and never signs up, so there is no account to create and no key to leak.
-- What stops one person registering somebody else's domain is "verified" —
-- an unverified row can be READ (so a crawler can see the declared terms) but
-- never receives a payout, because payouts follow proof of control, not
-- whoever typed the domain in first.
CREATE TABLE IF NOT EXISTS crawl_sites (
  domain             TEXT PRIMARY KEY,
  price_usd          TEXT NOT NULL DEFAULT '0.001',
  free_per_day       INTEGER NOT NULL DEFAULT 100,
  unknown_policy     TEXT NOT NULL DEFAULT 'charge' CHECK (unknown_policy IN ('charge','block','allow')),
  rate_threshold     INTEGER NOT NULL DEFAULT 30,
  operator_share_bps INTEGER NOT NULL DEFAULT 9000,
  payout_address     TEXT,
  payout_network     TEXT,
  allow_ua_json      TEXT NOT NULL DEFAULT '[]',
  free_paths_json    TEXT NOT NULL DEFAULT '[]',
  enabled            INTEGER NOT NULL DEFAULT 1,
  verified           INTEGER NOT NULL DEFAULT 0,
  verify_token       TEXT NOT NULL,
  verified_at        INTEGER,
  contact            TEXT,
  created_at         INTEGER NOT NULL,
  updated_at         INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_crawl_sites_verified ON crawl_sites(verified, updated_at);

-- Per-site, per-client, per-UTC-day grace consumption. The PRIMARY KEY makes
-- the counter a single UPSERT: two concurrent requests from one crawler can
-- never both read "99 used" and both pass free.
CREATE TABLE IF NOT EXISTS crawl_usage (
  domain      TEXT NOT NULL,
  client_key  TEXT NOT NULL,
  day         TEXT NOT NULL,
  used        INTEGER NOT NULL DEFAULT 0,
  first_at    INTEGER NOT NULL,
  last_at     INTEGER NOT NULL,
  PRIMARY KEY (domain, client_key, day)
);
CREATE INDEX IF NOT EXISTS idx_crawl_usage_day ON crawl_usage(day);

-- A purchased crawl pass: one settlement buys N requests against ONE domain
-- for a bounded window. This is what makes the economics work at all — a
-- tenth-of-a-cent page cannot carry its own Base settlement gas, so the
-- chain sees one payment and the gate decrements a counter.
CREATE TABLE IF NOT EXISTS crawl_passes (
  pass_id       TEXT PRIMARY KEY,
  token_hash    TEXT NOT NULL UNIQUE,
  domain        TEXT NOT NULL,
  requests_total INTEGER NOT NULL,
  requests_used INTEGER NOT NULL DEFAULT 0,
  price_usd     TEXT NOT NULL,
  paid_usd      TEXT NOT NULL,
  payer         TEXT,
  payment_fingerprint TEXT,
  issued_at     INTEGER NOT NULL,
  expires_at    INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_crawl_pass_domain ON crawl_passes(domain, expires_at);
CREATE INDEX IF NOT EXISTS idx_crawl_pass_expires ON crawl_passes(expires_at);

-- The operator's earnings ledger: one row per BILLED request. Written when a
-- pass is spent, not when it is sold, so a site earns per page actually
-- served and an unspent pass is never counted as somebody's revenue.
CREATE TABLE IF NOT EXISTS crawl_events (
  event_id     INTEGER PRIMARY KEY AUTOINCREMENT,
  domain       TEXT NOT NULL,
  pass_id      TEXT,
  actor        TEXT,
  operator     TEXT,
  kind         TEXT NOT NULL,
  path         TEXT,
  price_usd    TEXT NOT NULL,
  operator_usd TEXT NOT NULL,
  gateway_usd  TEXT NOT NULL,
  at           INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_crawl_events_domain ON crawl_events(domain, at);
CREATE INDEX IF NOT EXISTS idx_crawl_events_at ON crawl_events(at);

-- User-Agent strings the deterministic taxonomy did not recognise, with how
-- often each was seen. The AICF triage job reads this table and proposes
-- classifications; "proposal_json" is ADVISORY and is never consulted by the
-- gate. Billing follows the hand-reviewed taxonomy in crawl-classify.js and
-- nothing else, so a hallucinating model cannot invent a chargeable crawler.
CREATE TABLE IF NOT EXISTS crawl_unknown_ua (
  ua_hash       TEXT PRIMARY KEY,
  user_agent    TEXT NOT NULL,
  seen_count    INTEGER NOT NULL DEFAULT 1,
  first_at      INTEGER NOT NULL,
  last_at       INTEGER NOT NULL,
  triaged_at    INTEGER,
  proposal_json TEXT,
  served_by     TEXT,
  status        TEXT NOT NULL DEFAULT 'new' CHECK (status IN ('new','triaged','accepted','rejected'))
);
CREATE INDEX IF NOT EXISTS idx_crawl_ua_status ON crawl_unknown_ua(status, seen_count DESC);
`;

function nowSec() {
  return Math.floor(Date.now() / 1000);
}

function createGatewayStore(dbPath, { Database, maxBodyBytes = 4_000_000 } = {}) {
  const Db = Database || require('better-sqlite3');
  if (dbPath !== ':memory:') {
    fs.mkdirSync(path.dirname(path.resolve(dbPath)), { recursive: true });
  }
  const db = new Db(dbPath);
  db.pragma('journal_mode = WAL');
  db.pragma('synchronous = NORMAL');
  db.pragma('busy_timeout = 5000');
  // The schema is idempotent (CREATE TABLE/INDEX IF NOT EXISTS) and runs on
  // every open, so adding a TABLE — e.g. random_commitments for the
  // commit-reveal product — migrates existing DB files by itself. Adding a
  // COLUMN to an existing table needs the explicit ALTER below.
  db.exec(SCHEMA);
  // Migration for DB files created before the cross-ledger join key existed:
  // auth_nonce (the EIP-3009 authorization nonce) lets an incident be joined
  // deterministically to the facilitator's payments row and to the on-chain
  // AuthorizationUsed event (see bin/animica-x402 reconcile --incidents).
  const incidentCols = db.prepare('PRAGMA table_info(incidents)').all().map((c) => c.name);
  if (!incidentCols.includes('auth_nonce')) {
    db.exec('ALTER TABLE incidents ADD COLUMN auth_nonce TEXT;' +
      'CREATE INDEX IF NOT EXISTS idx_incidents_auth_nonce ON incidents(auth_nonce);');
  }

  const stmts = {
    putIdem: db.prepare(`INSERT OR IGNORE INTO idempotency
      (idem_key, payment_fingerprint, resource, response_status, content_type, content_encoding, body, oversize, settlement_header, settlement_tx, created_at)
      VALUES (@idem_key, @payment_fingerprint, @resource, @response_status, @content_type, @content_encoding, @body, @oversize, @settlement_header, @settlement_tx, @created_at)`),
    getIdem: db.prepare('SELECT * FROM idempotency WHERE idem_key = ? AND payment_fingerprint = ?'),
    pruneIdem: db.prepare('DELETE FROM idempotency WHERE created_at < ?'),
    addIncident: db.prepare(`INSERT INTO incidents
      (incident_id, payment_id, payment_fingerprint, settlement_tx, payer, resource, amount, network, kind, error, receipt_json, auth_nonce, status, created_at)
      VALUES (@incident_id, @payment_id, @payment_fingerprint, @settlement_tx, @payer, @resource, @amount, @network, @kind, @error, @receipt_json, @auth_nonce, 'open', @created_at)`),
    listIncidents: db.prepare('SELECT * FROM incidents WHERE status = ? ORDER BY created_at DESC LIMIT ?'),
    listAllIncidents: db.prepare('SELECT * FROM incidents ORDER BY created_at DESC LIMIT ?'),
    getIncident: db.prepare('SELECT * FROM incidents WHERE incident_id = ?'),
    setIncidentStatus: db.prepare('UPDATE incidents SET status = @status WHERE incident_id = @incident_id'),
    putCommitment: db.prepare(`INSERT INTO random_commitments
      (commit_id, commitment, algorithm, kind, request_id, memo, secret_hex, salt_hex, draw_json, reveal_after, created_at, revealed_at)
      VALUES (@commit_id, @commitment, @algorithm, @kind, @request_id, @memo, @secret_hex, @salt_hex, @draw_json, @reveal_after, @created_at, NULL)`),
    getCommitment: db.prepare('SELECT * FROM random_commitments WHERE commit_id = ?'),
    markRevealed: db.prepare('UPDATE random_commitments SET revealed_at = @revealed_at WHERE commit_id = @commit_id AND revealed_at IS NULL'),
    pruneCommitments: db.prepare('DELETE FROM random_commitments WHERE created_at < ?'),
    countCommitments: db.prepare('SELECT COUNT(*) AS n FROM random_commitments'),
    getTrial: db.prepare('SELECT used FROM trial_usage WHERE product = ? AND client = ? AND day = ?'),
    // Single statement, so two concurrent trial calls from one client cannot
    // both read 0 and both write 1. UPSERT is atomic in SQLite; RETURNING
    // gives us the post-increment value without a second read.
    bumpTrial: db.prepare(`INSERT INTO trial_usage (product, client, day, used, created_at)
      VALUES (@product, @client, @day, 1, @created_at)
      ON CONFLICT(product, client, day) DO UPDATE SET used = used + 1
      RETURNING used`),
    pruneTrials: db.prepare('DELETE FROM trial_usage WHERE created_at < ?'),

    putVoucher: db.prepare(`INSERT INTO credit_vouchers
      (voucher_id, label, minted_atomic, bonus_atomic, balance_atomic, payer, settlement_tx, created_at, expires_at, revoked, last_used_at, spend_count)
      VALUES (@voucher_id, @label, @minted_atomic, @bonus_atomic, @balance_atomic, @payer, @settlement_tx, @created_at, @expires_at, 0, NULL, 0)`),
    getVoucher: db.prepare('SELECT * FROM credit_vouchers WHERE voucher_id = ?'),
    setVoucherBalance: db.prepare(`UPDATE credit_vouchers
      SET balance_atomic = @balance_atomic, last_used_at = @last_used_at, spend_count = spend_count + 1
      WHERE voucher_id = @voucher_id AND balance_atomic = @expected_balance AND revoked = 0`),
    revokeVoucher: db.prepare('UPDATE credit_vouchers SET revoked = 1 WHERE voucher_id = ?'),
    pruneVouchers: db.prepare('DELETE FROM credit_vouchers WHERE expires_at < ? AND balance_atomic = \'0\''),
    addCreditEntry: db.prepare(`INSERT INTO credit_ledger
      (entry_id, voucher_id, product, resource, amount_atomic, balance_after, request_id, created_at)
      VALUES (@entry_id, @voucher_id, @product, @resource, @amount_atomic, @balance_after, @request_id, @created_at)`),
    listCreditEntries: db.prepare('SELECT * FROM credit_ledger WHERE voucher_id = ? ORDER BY created_at DESC LIMIT ?'),
    countVouchers: db.prepare('SELECT COUNT(*) AS n FROM credit_vouchers'),

    putAnmPayment: db.prepare(`INSERT OR IGNORE INTO anm_payments
      (txid, payer, amount_nanm, resource, status, created_at, settled_at)
      VALUES (@txid, @payer, @amount_nanm, @resource, @status, @created_at, NULL)`),
    getAnmPayment: db.prepare('SELECT * FROM anm_payments WHERE txid = ?'),
    setAnmPaymentStatus: db.prepare('UPDATE anm_payments SET status = @status, settled_at = @settled_at WHERE txid = @txid'),
    countAnmPayments: db.prepare("SELECT COUNT(*) AS n FROM anm_payments WHERE status = 'settled'"),

    putScanService: db.prepare(`INSERT INTO scan_services
      (service_id, url, host, name, description, provider, contact, http_method, price_nanm, price_display,
       pay_to, network, asset, category, verified, status, last_probe_at, last_ok_at, probe_detail,
       fail_count, submitted_by, created_at, updated_at)
      VALUES (@service_id, @url, @host, @name, @description, @provider, @contact, @http_method, @price_nanm, @price_display,
       @pay_to, @network, @asset, @category, @verified, @status, @last_probe_at, @last_ok_at, @probe_detail,
       0, @submitted_by, @created_at, @updated_at)`),
    getScanServiceByUrl: db.prepare('SELECT * FROM scan_services WHERE url = ?'),
    getScanService: db.prepare('SELECT * FROM scan_services WHERE service_id = ?'),
    updateScanProbe: db.prepare(`UPDATE scan_services SET
       verified = @verified, status = @status, last_probe_at = @last_probe_at,
       last_ok_at = COALESCE(@last_ok_at, last_ok_at), probe_detail = @probe_detail,
       fail_count = @fail_count, price_nanm = COALESCE(@price_nanm, price_nanm),
       price_display = COALESCE(@price_display, price_display), pay_to = COALESCE(@pay_to, pay_to),
       network = COALESCE(@network, network), asset = COALESCE(@asset, asset),
       name = COALESCE(@name, name), updated_at = @updated_at
       WHERE service_id = @service_id`),
    listScanServices: db.prepare("SELECT * FROM scan_services WHERE (@status IS NULL OR status = @status) ORDER BY (status='live') DESC, last_ok_at DESC NULLS LAST, created_at DESC LIMIT @limit OFFSET @offset"),
    countScanServices: db.prepare("SELECT COUNT(*) AS n, SUM(status='live') AS live FROM scan_services"),
    countScanByHostSince: db.prepare('SELECT COUNT(*) AS n FROM scan_services WHERE submitted_by = ? AND created_at > ?'),
    staleScanServices: db.prepare("SELECT * FROM scan_services WHERE status IN ('pending','live','dead') AND (last_probe_at IS NULL OR last_probe_at < ?) ORDER BY last_probe_at ASC NULLS FIRST LIMIT ?"),

    putBountyClaim: db.prepare(`INSERT INTO bounty_claims
      (claim_id, service_id, url, host, payout_address, amount_usd, amount_nanm, rate_usd_anm, status, reason, payout_txid, created_at, updated_at)
      VALUES (@claim_id, @service_id, @url, @host, @payout_address, @amount_usd, @amount_nanm, @rate_usd_anm, @status, @reason, NULL, @created_at, @updated_at)`),
    getBountyClaim: db.prepare('SELECT * FROM bounty_claims WHERE claim_id = ?'),
    getBountyClaimByHost: db.prepare("SELECT * FROM bounty_claims WHERE host = ? AND status IN ('pending','verified','reserved','paid')"),
    setBountyStatus: db.prepare('UPDATE bounty_claims SET status = @status, reason = COALESCE(@reason, reason), payout_txid = COALESCE(@payout_txid, payout_txid), updated_at = @updated_at WHERE claim_id = @claim_id'),
    listBountyClaims: db.prepare('SELECT * FROM bounty_claims WHERE (@status IS NULL OR status = @status) ORDER BY created_at DESC LIMIT @limit'),
    sumReservedBounty: db.prepare("SELECT COALESCE(SUM(CAST(amount_nanm AS INTEGER)),0) AS total, COUNT(*) AS n FROM bounty_claims WHERE status IN ('verified','reserved')"),
    countPaidBounty: db.prepare("SELECT COUNT(*) AS n FROM bounty_claims WHERE status IN ('reserved','paid')"),

    putLease: db.prepare(`INSERT INTO reward_leases
      (lease_id, buyer_address, share_bps, start_height, end_height, paid_usd, quoted_nanm, rate_usd_anm, accrued_nanm, settled_nanm, last_height, status, created_at)
      VALUES (@lease_id, @buyer_address, @share_bps, @start_height, @end_height, @paid_usd, @quoted_nanm, @rate_usd_anm, '0', '0', NULL, 'active', @created_at)`),
    getLease: db.prepare('SELECT * FROM reward_leases WHERE lease_id = ?'),
    listActiveLeases: db.prepare("SELECT * FROM reward_leases WHERE status = 'active' AND end_height >= ? ORDER BY start_height"),
    sumOverlappingBps: db.prepare(`SELECT COALESCE(SUM(share_bps),0) AS bps FROM reward_leases
      WHERE status = 'active' AND start_height <= @end_height AND end_height >= @start_height`),

    recordExecSpend: db.prepare(`INSERT INTO exec_spend (day, resource, spent_usd, outcome, request_id, spent_at)
      VALUES (@day, @resource, @spent_usd, @outcome, @request_id, @spent_at)`),
    execSpentToday: db.prepare("SELECT COALESCE(SUM(CAST(spent_usd AS REAL)), 0) AS total, COUNT(*) AS n FROM exec_spend WHERE day = ? AND outcome = 'paid'"),
    putIndexSnapshot: db.prepare(`INSERT INTO mesh_index_snapshot (id, harvested_at, counts_json, records_json)
      VALUES (1, @harvested_at, @counts_json, @records_json)
      ON CONFLICT(id) DO UPDATE SET harvested_at=excluded.harvested_at,
        counts_json=excluded.counts_json, records_json=excluded.records_json`),
    getIndexSnapshot: db.prepare('SELECT * FROM mesh_index_snapshot WHERE id = 1'),
    putRemoteSettlement: db.prepare(`INSERT OR IGNORE INTO remote_settlements
      (settled_at, product, resource, payer, tx, network, asset, amount_atomic, price_usd, facilitator)
      VALUES (@settled_at, @product, @resource, @payer, @tx, @network, @asset, @amount_atomic, @price_usd, @facilitator)`),
    listRemoteSettlements: db.prepare('SELECT * FROM remote_settlements ORDER BY settled_at DESC LIMIT ?'),
    remoteRevenue: db.prepare(`SELECT product, COUNT(*) AS n, SUM(CAST(amount_atomic AS INTEGER)) AS atomic
      FROM remote_settlements WHERE settled_at >= ? GROUP BY product ORDER BY atomic DESC`),
    putMarketSnapshot: db.prepare(`INSERT INTO analytics_market_history (segment_key, segment, observed_at, stats_json)
      VALUES (@segment_key, @segment, @observed_at, @stats_json)`),
    lastMarketSnapshotAt: db.prepare('SELECT MAX(observed_at) AS at FROM analytics_market_history WHERE segment_key = ?'),
    marketHistory: db.prepare('SELECT * FROM analytics_market_history WHERE segment_key = ? ORDER BY observed_at DESC LIMIT ?'),
    // Pruned PER SEGMENT: a rarely-queried segment's only two observations must
    // not be evicted by a busy one, so the cap is per key, not global.
    pruneMarketHistory: db.prepare(`DELETE FROM analytics_market_history
      WHERE segment_key = @segment_key AND id NOT IN (
        SELECT id FROM analytics_market_history WHERE segment_key = @segment_key
        ORDER BY observed_at DESC LIMIT @keep)`),
    putProbe: db.prepare(`INSERT INTO mesh_probes
      (key, resource, method, outcome, http_status, price_atomic, price_usd, asset, network,
       pay_to, scheme, max_timeout_s, call_spec_json, accepts_json, error, latency_ms, probed_at, attempts)
      VALUES (@key, @resource, @method, @outcome, @http_status, @price_atomic, @price_usd, @asset, @network,
       @pay_to, @scheme, @max_timeout_s, @call_spec_json, @accepts_json, @error, @latency_ms, @probed_at, 1)
      ON CONFLICT(key) DO UPDATE SET
        resource=excluded.resource, method=excluded.method, outcome=excluded.outcome,
        http_status=excluded.http_status, price_atomic=excluded.price_atomic, price_usd=excluded.price_usd,
        asset=excluded.asset, network=excluded.network, pay_to=excluded.pay_to, scheme=excluded.scheme,
        max_timeout_s=excluded.max_timeout_s, call_spec_json=excluded.call_spec_json,
        accepts_json=excluded.accepts_json, error=excluded.error, latency_ms=excluded.latency_ms,
        probed_at=excluded.probed_at, attempts=mesh_probes.attempts+1`),
    getProbe: db.prepare('SELECT * FROM mesh_probes WHERE key = ?'),
    allProbes: db.prepare('SELECT * FROM mesh_probes'),
    probeStats: db.prepare(`SELECT outcome, COUNT(*) AS n FROM mesh_probes GROUP BY outcome`),
    probeFreshness: db.prepare('SELECT MIN(probed_at) AS oldest, MAX(probed_at) AS newest, COUNT(*) AS total FROM mesh_probes'),
    putForecast: db.prepare(`INSERT INTO forecasts
      (forecast_id, commitment, blob_id, question, market_id, market_slug, market_price,
       model_prob, model_reasoning, model_name, head_height, head_hash, end_date, created_at)
      VALUES (@forecast_id, @commitment, @blob_id, @question, @market_id, @market_slug, @market_price,
       @model_prob, @model_reasoning, @model_name, @head_height, @head_hash, @end_date, @created_at)`),
    getForecast: db.prepare('SELECT * FROM forecasts WHERE commitment = ?'),
    openForecasts: db.prepare('SELECT * FROM forecasts WHERE resolved_at IS NULL AND market_id IS NOT NULL ORDER BY created_at LIMIT ?'),
    resolveForecast: db.prepare(`UPDATE forecasts SET resolved_at = @resolved_at, resolved_outcome = @resolved_outcome,
       brier_model = @brier_model, brier_market = @brier_market WHERE forecast_id = @forecast_id`),
    forecastStats: db.prepare(`SELECT COUNT(*) AS n,
       SUM(CASE WHEN resolved_at IS NOT NULL THEN 1 ELSE 0 END) AS resolved,
       AVG(CASE WHEN brier_model IS NOT NULL THEN CAST(brier_model AS REAL) END) AS brier_model,
       AVG(CASE WHEN brier_market IS NOT NULL THEN CAST(brier_market AS REAL) END) AS brier_market
       FROM forecasts`),
    recentForecasts: db.prepare('SELECT * FROM forecasts ORDER BY created_at DESC LIMIT ?'),

    // ------------------------------------------------------------ paid crawl
    putSite: db.prepare(`INSERT INTO crawl_sites
      (domain, price_usd, free_per_day, unknown_policy, rate_threshold, operator_share_bps,
       payout_address, payout_network, allow_ua_json, free_paths_json, enabled, verified,
       verify_token, contact, created_at, updated_at)
      VALUES (@domain, @price_usd, @free_per_day, @unknown_policy, @rate_threshold, @operator_share_bps,
       @payout_address, @payout_network, @allow_ua_json, @free_paths_json, @enabled, 0,
       @verify_token, @contact, @created_at, @updated_at)
      ON CONFLICT(domain) DO UPDATE SET
        price_usd = @price_usd, free_per_day = @free_per_day, unknown_policy = @unknown_policy,
        rate_threshold = @rate_threshold, operator_share_bps = @operator_share_bps,
        payout_address = @payout_address, payout_network = @payout_network,
        allow_ua_json = @allow_ua_json, free_paths_json = @free_paths_json,
        enabled = @enabled, contact = @contact, updated_at = @updated_at`),
    getSite: db.prepare('SELECT * FROM crawl_sites WHERE domain = ?'),
    listSites: db.prepare('SELECT * FROM crawl_sites ORDER BY updated_at DESC LIMIT ?'),
    markSiteVerified: db.prepare('UPDATE crawl_sites SET verified = 1, verified_at = @at, updated_at = @at WHERE domain = @domain'),
    deleteSite: db.prepare('DELETE FROM crawl_sites WHERE domain = ?'),

    bumpUsage: db.prepare(`INSERT INTO crawl_usage (domain, client_key, day, used, first_at, last_at)
      VALUES (@domain, @client_key, @day, 1, @at, @at)
      ON CONFLICT(domain, client_key, day) DO UPDATE SET used = used + 1, last_at = @at`),
    getUsage: db.prepare('SELECT used FROM crawl_usage WHERE domain = ? AND client_key = ? AND day = ?'),
    pruneUsage: db.prepare('DELETE FROM crawl_usage WHERE day < ?'),

    putPass: db.prepare(`INSERT INTO crawl_passes
      (pass_id, token_hash, domain, requests_total, requests_used, price_usd, paid_usd, payer,
       payment_fingerprint, issued_at, expires_at)
      VALUES (@pass_id, @token_hash, @domain, @requests_total, 0, @price_usd, @paid_usd, @payer,
       @payment_fingerprint, @issued_at, @expires_at)`),
    getPassByHash: db.prepare('SELECT * FROM crawl_passes WHERE token_hash = ?'),
    spendPass: db.prepare(`UPDATE crawl_passes SET requests_used = requests_used + 1
      WHERE token_hash = @token_hash AND requests_used < requests_total AND expires_at > @at`),
    prunePasses: db.prepare('DELETE FROM crawl_passes WHERE expires_at < ?'),

    addCrawlEvent: db.prepare(`INSERT INTO crawl_events
      (domain, pass_id, actor, operator, kind, path, price_usd, operator_usd, gateway_usd, at)
      VALUES (@domain, @pass_id, @actor, @operator, @kind, @path, @price_usd, @operator_usd, @gateway_usd, @at)`),
    earningsFor: db.prepare(`SELECT COUNT(*) AS billed_requests,
       COALESCE(SUM(CAST(operator_usd AS REAL)), 0) AS operator_usd,
       COALESCE(SUM(CAST(gateway_usd AS REAL)), 0) AS gateway_usd
       FROM crawl_events WHERE domain = ? AND at >= ?`),
    topActorsFor: db.prepare(`SELECT actor, operator, COUNT(*) AS n,
       COALESCE(SUM(CAST(operator_usd AS REAL)), 0) AS operator_usd
       FROM crawl_events WHERE domain = ? AND at >= ? GROUP BY actor, operator ORDER BY n DESC LIMIT ?`),

    seeUnknownUa: db.prepare(`INSERT INTO crawl_unknown_ua (ua_hash, user_agent, seen_count, first_at, last_at)
      VALUES (@ua_hash, @user_agent, 1, @at, @at)
      ON CONFLICT(ua_hash) DO UPDATE SET seen_count = seen_count + 1, last_at = @at`),
    untriagedUa: db.prepare("SELECT * FROM crawl_unknown_ua WHERE status = 'new' ORDER BY seen_count DESC LIMIT ?"),
    setUaProposal: db.prepare(`UPDATE crawl_unknown_ua SET proposal_json = @proposal_json,
      served_by = @served_by, triaged_at = @at, status = 'triaged' WHERE ua_hash = @ua_hash`),
    listUaProposals: db.prepare('SELECT * FROM crawl_unknown_ua ORDER BY seen_count DESC LIMIT ?'),
  };

  return {
    db, // tests/CLI only

    /**
     * Store a delivered outcome. First writer wins; returns true when this
     * call stored the row (false = someone beat us — harmless).
     */
    putIdempotent({ idemKey, paymentFingerprint, resource, status, contentType, contentEncoding, body, settlementHeader, settlementTx }) {
      // Test the cap BEFORE copying. UTF-8 byte length is never below the
      // string length, so an over-cap string is known to be oversize without
      // materializing a second copy of it — which matters because the body
      // can be megabytes and Buffer.from() would be one more full copy of it
      // on the way to being discarded.
      const known = typeof body === 'string' ? body.length : (body && body.length) || 0;
      if (body !== undefined && body !== null && known > maxBodyBytes) {
        const r0 = stmts.putIdem.run({
          idem_key: String(idemKey),
          payment_fingerprint: String(paymentFingerprint),
          resource: String(resource || ''),
          response_status: Number(status),
          content_type: contentType || null,
          content_encoding: contentEncoding || null,
          body: null,
          oversize: 1,
          settlement_header: settlementHeader || null,
          settlement_tx: settlementTx || null,
          created_at: nowSec(),
        });
        return r0.changes === 1;
      }
      const buf = body === undefined || body === null ? null : Buffer.from(body);
      const oversize = buf !== null && buf.length > maxBodyBytes ? 1 : 0;
      const r = stmts.putIdem.run({
        idem_key: String(idemKey),
        payment_fingerprint: String(paymentFingerprint),
        resource: String(resource || ''),
        response_status: Number(status),
        content_type: contentType || null,
        content_encoding: contentEncoding || null,
        body: oversize ? null : buf,
        oversize,
        settlement_header: settlementHeader || null,
        settlement_tx: settlementTx || null,
        created_at: nowSec(),
      });
      return r.changes === 1;
    },

    getIdempotent(idemKey, paymentFingerprint) {
      return stmts.getIdem.get(String(idemKey), String(paymentFingerprint)) || null;
    },

    pruneIdempotency(ttlSeconds) {
      return stmts.pruneIdem.run(nowSec() - Number(ttlSeconds)).changes;
    },

    addIncident({ paymentId, paymentFingerprint, settlementTx, payer, resource, amount, network, kind, error, receipt, authNonce }) {
      const incidentId = 'inc_' + crypto.randomBytes(8).toString('hex');
      stmts.addIncident.run({
        incident_id: incidentId,
        payment_id: paymentId || null,
        payment_fingerprint: paymentFingerprint || null,
        settlement_tx: settlementTx || null,
        payer: payer || null,
        resource: String(resource || ''),
        amount: amount === undefined || amount === null ? null : String(amount),
        network: network || null,
        kind: String(kind || 'downstream_failed'),
        error: String(error || 'unknown'),
        receipt_json: receipt ? JSON.stringify(receipt) : null,
        auth_nonce: authNonce ? String(authNonce).toLowerCase() : null,
        created_at: nowSec(),
      });
      return incidentId;
    },

    listIncidents(status, limit = 100) {
      return status ? stmts.listIncidents.all(status, limit) : stmts.listAllIncidents.all(limit);
    },

    getIncident(id) {
      return stmts.getIncident.get(id) || null;
    },

    setIncidentStatus(id, status) {
      if (!['open', 'refunded', 'resolved'].includes(status)) throw new Error(`bad incident status ${status}`);
      return stmts.setIncidentStatus.run({ incident_id: id, status }).changes === 1;
    },

    /**
     * Seal one commitment. `draw` is the whole randomness response (raw
     * bytes + source/health/attestation) so the free reveal can publish the
     * full provenance later without a second node call.
     */
    putCommitment({ commitId, commitment, algorithm, kind, requestId, memo, secretHex, saltHex, draw, revealAfter, createdAt }) {
      stmts.putCommitment.run({
        commit_id: String(commitId),
        commitment: String(commitment),
        algorithm: String(algorithm),
        kind: String(kind),
        request_id: requestId === undefined || requestId === null ? null : String(requestId),
        memo: memo === undefined || memo === null ? null : String(memo),
        secret_hex: String(secretHex),
        salt_hex: String(saltHex),
        draw_json: JSON.stringify(draw),
        reveal_after: Number(revealAfter),
        created_at: createdAt === undefined ? nowSec() : Number(createdAt),
      });
      return commitId;
    },

    getCommitment(commitId) {
      return stmts.getCommitment.get(String(commitId)) || null;
    },

    /** Idempotent: only the FIRST disclosure writes revealed_at. */
    markCommitmentRevealed(commitId, atSec) {
      return stmts.markRevealed.run({
        commit_id: String(commitId),
        revealed_at: atSec === undefined ? nowSec() : Number(atSec),
      }).changes === 1;
    },

    pruneCommitments(ttlSeconds) {
      return stmts.pruneCommitments.run(nowSec() - Number(ttlSeconds)).changes;
    },

    countCommitments() {
      return stmts.countCommitments.get().n;
    },

    /** Trials this client has already spent on `product` today (UTC day). */
    trialUsed(product, client, day) {
      const row = stmts.getTrial.get(String(product), String(client), String(day));
      return row ? Number(row.used) : 0;
    },

    /**
     * Consume one trial. Returns {allowed, used, remaining}.
     *
     * The increment happens FIRST and is only rolled back when it took the
     * client past the cap — so two concurrent calls cannot both see the last
     * remaining trial. Erring toward charging a trial we did not serve is the
     * safe direction: the caller is told to pay, not given a free call twice.
     */
    consumeTrial(product, client, day, limit) {
      const cap = Math.max(0, Number(limit) || 0);
      if (cap === 0) return { allowed: false, used: 0, remaining: 0 };
      const used = Number(stmts.bumpTrial.get({
        product: String(product), client: String(client), day: String(day), created_at: nowSec(),
      }).used);
      if (used > cap) return { allowed: false, used: used - 1, remaining: 0 };
      return { allowed: true, used, remaining: cap - used };
    },

    pruneTrials(ttlSeconds) {
      return stmts.pruneTrials.run(nowSec() - Number(ttlSeconds)).changes;
    },

    // ---------------------------------------------------------------------
    // Prepaid credits.
    //
    // A voucher token is a bearer secret. It is never stored — only
    // sha256(token) — so a database read cannot spend anyone's balance, the
    // same reason the commit-reveal secrets are sealed rather than kept.
    // Balances are BigInt-through-TEXT end to end; a JS Number would lose
    // atomic units, which is the money bug this codebase already fixed twice.
    // ---------------------------------------------------------------------

    putVoucher({ voucherId, label, mintedAtomic, bonusAtomic, payer, settlementTx, createdAt, expiresAt }) {
      const minted = BigInt(mintedAtomic);
      const bonus = BigInt(bonusAtomic || 0);
      stmts.putVoucher.run({
        voucher_id: String(voucherId),
        label: label ? String(label) : null,
        minted_atomic: minted.toString(),
        bonus_atomic: bonus.toString(),
        // The buyer's spendable balance is face value PLUS the bonus: the
        // bonus exists because they saved us N-1 on-chain settlements.
        balance_atomic: (minted + bonus).toString(),
        payer: payer ? String(payer) : null,
        settlement_tx: settlementTx ? String(settlementTx) : null,
        created_at: Number(createdAt),
        expires_at: Number(expiresAt),
      });
      return true;
    },

    getVoucher(voucherId) {
      return stmts.getVoucher.get(String(voucherId)) || null;
    },

    /**
     * Spend `amountAtomic` from a voucher. Returns
     * {ok, reason?, balanceBefore?, balanceAfter?, row?}.
     *
     * better-sqlite3 is synchronous, so the read-modify-write below runs
     * inside ONE transaction with no await between the read and the write —
     * two concurrent requests cannot both observe the same balance and both
     * spend it. The UPDATE additionally carries the expected balance in its
     * WHERE clause, so even a second writer process (WAL) loses rather than
     * double-spends: it sees 0 changes and we report insufficient_credit.
     */
    debitVoucher({ voucherId, amountAtomic, product, resource, requestId, now: nowMs }) {
      const id = String(voucherId);
      const amount = BigInt(amountAtomic);
      if (amount < 0n) return { ok: false, reason: 'negative_amount' };
      const at = nowMs === undefined ? nowSec() : Math.floor(Number(nowMs) / 1000);
      const run = db.transaction(() => {
        const row = stmts.getVoucher.get(id);
        if (!row) return { ok: false, reason: 'unknown_voucher' };
        if (row.revoked) return { ok: false, reason: 'voucher_revoked' };
        if (Number(row.expires_at) <= at) return { ok: false, reason: 'voucher_expired', expiresAt: Number(row.expires_at) };
        const before = BigInt(row.balance_atomic);
        if (before < amount) {
          return { ok: false, reason: 'insufficient_credit', balanceBefore: before.toString(), required: amount.toString() };
        }
        const after = before - amount;
        const upd = stmts.setVoucherBalance.run({
          voucher_id: id,
          balance_atomic: after.toString(),
          expected_balance: before.toString(),
          last_used_at: at,
        });
        if (upd.changes !== 1) return { ok: false, reason: 'concurrent_modification' };
        stmts.addCreditEntry.run({
          entry_id: crypto.randomUUID(),
          voucher_id: id,
          product: String(product || ''),
          resource: String(resource || ''),
          amount_atomic: amount.toString(),
          balance_after: after.toString(),
          request_id: requestId ? String(requestId) : null,
          created_at: at,
        });
        return { ok: true, balanceBefore: before.toString(), balanceAfter: after.toString(), row };
      });
      return run();
    },

    /**
     * Return credit to a voucher. Used when a credit-funded call was debited
     * but the service then failed — the codebase's rule is that a payer never
     * pays for a service they did not receive, and with credits we CAN make
     * that true automatically (an on-chain settlement cannot be un-sent).
     */
    refundVoucher({ voucherId, amountAtomic, product, resource, requestId, now: nowMs }) {
      const id = String(voucherId);
      const amount = BigInt(amountAtomic);
      const at = nowMs === undefined ? nowSec() : Math.floor(Number(nowMs) / 1000);
      const run = db.transaction(() => {
        const row = stmts.getVoucher.get(id);
        if (!row) return { ok: false, reason: 'unknown_voucher' };
        const before = BigInt(row.balance_atomic);
        const after = before + amount;
        const upd = stmts.setVoucherBalance.run({
          voucher_id: id,
          balance_atomic: after.toString(),
          expected_balance: before.toString(),
          last_used_at: at,
        });
        if (upd.changes !== 1) return { ok: false, reason: 'concurrent_modification' };
        stmts.addCreditEntry.run({
          entry_id: crypto.randomUUID(),
          voucher_id: id,
          product: String(product || ''),
          resource: String(resource || ''),
          amount_atomic: ('-' + amount.toString()),
          balance_after: after.toString(),
          request_id: requestId ? String(requestId) : null,
          created_at: at,
        });
        return { ok: true, balanceAfter: after.toString() };
      });
      return run();
    },

    listCreditEntries(voucherId, limit = 50) {
      return stmts.listCreditEntries.all(String(voucherId), Number(limit));
    },

    revokeVoucher(voucherId) {
      return stmts.revokeVoucher.run(String(voucherId)).changes === 1;
    },

    countVouchers() {
      return Number(stmts.countVouchers.get().n);
    },

    // ---------------------------------------------------------------------
    // ANM lane replay guard
    // ---------------------------------------------------------------------
    putAnmPayment(row) {
      stmts.putAnmPayment.run({
        txid: String(row.txid),
        payer: row.payer ? String(row.payer) : null,
        amount_nanm: BigInt(row.amountNanm || 0).toString(),
        resource: row.resource ? String(row.resource) : null,
        status: String(row.status || 'submitted'),
        created_at: Number(row.createdAt || nowSec()),
      });
      return true;
    },
    getAnmPayment(txid) { return stmts.getAnmPayment.get(String(txid)) || null; },
    setAnmPaymentStatus(txid, status) {
      return stmts.setAnmPaymentStatus.run({
        txid: String(txid), status: String(status),
        settled_at: status === 'settled' ? nowSec() : null,
      }).changes === 1;
    },
    countAnmSettled() { return Number(stmts.countAnmPayments.get().n); },

    // ---------------------------------------------------------------------
    // ANM 402 Scan directory
    // ---------------------------------------------------------------------
    putScanService(row) {
      const at = nowSec();
      stmts.putScanService.run({
        service_id: String(row.serviceId),
        url: String(row.url),
        host: String(row.host),
        name: row.name ? String(row.name) : null,
        description: row.description ? String(row.description) : null,
        provider: row.provider ? String(row.provider) : null,
        contact: row.contact ? String(row.contact) : null,
        http_method: String(row.httpMethod || 'GET'),
        price_nanm: row.priceNanm ? String(row.priceNanm) : null,
        price_display: row.priceDisplay ? String(row.priceDisplay) : null,
        pay_to: row.payTo ? String(row.payTo) : null,
        network: row.network ? String(row.network) : null,
        asset: row.asset ? String(row.asset) : null,
        category: row.category ? String(row.category) : null,
        verified: row.verified ? 1 : 0,
        status: String(row.status || 'pending'),
        last_probe_at: row.lastProbeAt || null,
        last_ok_at: row.lastOkAt || null,
        probe_detail: row.probeDetail ? String(row.probeDetail) : null,
        submitted_by: row.submittedBy ? String(row.submittedBy) : null,
        created_at: at,
        updated_at: at,
      });
      return true;
    },
    getScanServiceByUrl(url) { return stmts.getScanServiceByUrl.get(String(url)) || null; },
    getScanService(id) { return stmts.getScanService.get(String(id)) || null; },
    updateScanProbe(row) {
      return stmts.updateScanProbe.run({
        service_id: String(row.serviceId),
        verified: row.verified ? 1 : 0,
        status: String(row.status),
        last_probe_at: Number(row.lastProbeAt || nowSec()),
        last_ok_at: row.lastOkAt || null,
        probe_detail: row.probeDetail ? String(row.probeDetail).slice(0, 500) : null,
        fail_count: Number(row.failCount || 0),
        price_nanm: row.priceNanm ? String(row.priceNanm) : null,
        price_display: row.priceDisplay ? String(row.priceDisplay) : null,
        pay_to: row.payTo ? String(row.payTo) : null,
        network: row.network ? String(row.network) : null,
        asset: row.asset ? String(row.asset) : null,
        name: row.name ? String(row.name) : null,
        updated_at: nowSec(),
      }).changes === 1;
    },
    listScanServices({ status = null, limit = 100, offset = 0 } = {}) {
      return stmts.listScanServices.all({ status, limit: Number(limit), offset: Number(offset) });
    },
    countScanServices() {
      const r = stmts.countScanServices.get();
      return { total: Number(r.n || 0), live: Number(r.live || 0) };
    },
    countScanSubmissionsSince(client, sinceSec) {
      return Number(stmts.countScanByHostSince.get(String(client), Number(sinceSec)).n);
    },
    staleScanServices(olderThanSec, limit = 20) {
      return stmts.staleScanServices.all(Number(olderThanSec), Number(limit));
    },

    // ---------------------------------------------------------------------
    // Adoption bounty claims
    // ---------------------------------------------------------------------
    putBountyClaim(row) {
      const at = nowSec();
      try {
        stmts.putBountyClaim.run({
          claim_id: String(row.claimId),
          service_id: row.serviceId ? String(row.serviceId) : null,
          url: String(row.url),
          host: String(row.host),
          payout_address: String(row.payoutAddress),
          amount_usd: String(row.amountUsd),
          amount_nanm: BigInt(row.amountNanm).toString(),
          rate_usd_anm: String(row.rateUsdAnm),
          status: String(row.status || 'pending'),
          reason: row.reason ? String(row.reason) : null,
          created_at: at,
          updated_at: at,
        });
        return { ok: true };
      } catch (e) {
        // The partial unique index on host is the anti-farming guard: one
        // open claim per host, enforced by the DATABASE rather than by a
        // check-then-insert race.
        if (/UNIQUE/i.test(e.message)) return { ok: false, reason: 'duplicate_host_claim' };
        throw e;
      }
    },
    getBountyClaim(id) { return stmts.getBountyClaim.get(String(id)) || null; },
    getBountyClaimByHost(host) { return stmts.getBountyClaimByHost.get(String(host)) || null; },
    setBountyStatus({ claimId, status, reason, payoutTxid }) {
      return stmts.setBountyStatus.run({
        claim_id: String(claimId), status: String(status),
        reason: reason ? String(reason) : null,
        payout_txid: payoutTxid ? String(payoutTxid) : null,
        updated_at: nowSec(),
      }).changes === 1;
    },
    listBountyClaims({ status = null, limit = 100 } = {}) {
      return stmts.listBountyClaims.all({ status, limit: Number(limit) });
    },
    /** Total nANM already promised to claims that are not yet paid out. */
    reservedBountyNanm() {
      const r = stmts.sumReservedBounty.get();
      return { nanm: BigInt(r.total || 0), count: Number(r.n || 0) };
    },
    countAwardedBounties() { return Number(stmts.countPaidBounty.get().n); },

    // ---------------------------------------------------------------------
    // Block-reward share leases
    // ---------------------------------------------------------------------
    putLease(row) {
      stmts.putLease.run({
        lease_id: String(row.leaseId),
        buyer_address: String(row.buyerAddress),
        share_bps: Number(row.shareBps),
        start_height: Number(row.startHeight),
        end_height: Number(row.endHeight),
        paid_usd: String(row.paidUsd),
        quoted_nanm: BigInt(row.quotedNanm).toString(),
        rate_usd_anm: String(row.rateUsdAnm),
        created_at: nowSec(),
      });
      return true;
    },
    getLease(id) { return stmts.getLease.get(String(id)) || null; },
    listActiveLeases(atHeight) { return stmts.listActiveLeases.all(Number(atHeight)); },

    // ---------------------------------------------------------------------
    // Notarised forecasts
    // ---------------------------------------------------------------------
    recordExecSpend(r) { return stmts.recordExecSpend.run(r); },
    execSpentToday(day) {
      const r = stmts.execSpentToday.get(String(day)) || {};
      return { total: Number(r.total || 0), calls: Number(r.n || 0) };
    },

    putIndexSnapshot({ harvestedAt, counts, records }) {
      return stmts.putIndexSnapshot.run({
        harvested_at: Math.floor(harvestedAt / 1000),
        counts_json: JSON.stringify(counts),
        records_json: JSON.stringify(records),
      });
    },
    getIndexSnapshot() {
      const row = stmts.getIndexSnapshot.get();
      if (!row) return null;
      try {
        return { harvestedAt: row.harvested_at * 1000, counts: JSON.parse(row.counts_json), records: JSON.parse(row.records_json) };
      } catch {
        return null;   // a corrupt snapshot is a cache miss, not a crash
      }
    },

    /**
     * Record one observation of a segment's aggregates.
     *
     * Rate-limited per segment: a segment queried a hundred times an hour must
     * not produce a hundred rows, because that is a request log wearing a
     * time-series costume — every row identical, and the "trend" it yields is
     * index-harvest noise. Returns whether the write happened so a caller can
     * be honest about the density of its own series.
     */
    recordMarketSnapshot({ segmentKey, segment, at, stats, minIntervalMs = 3600_000, keep = 500 }) {
      const nowS = Math.floor(at / 1000);
      const prev = stmts.lastMarketSnapshotAt.get(String(segmentKey));
      if (prev && prev.at && (nowS - prev.at) * 1000 < minIntervalMs) return { recorded: false, reason: 'rate_limited' };
      stmts.putMarketSnapshot.run({
        segment_key: String(segmentKey),
        segment: segment === null || segment === undefined ? null : String(segment),
        observed_at: nowS,
        stats_json: JSON.stringify(stats),
      });
      stmts.pruneMarketHistory.run({ segment_key: String(segmentKey), keep });
      return { recorded: true };
    },

    /** Newest first. A corrupt row is skipped, not fatal — history is a bonus. */
    marketHistory(segmentKey, limit = 500) {
      const rows = stmts.marketHistory.all(String(segmentKey), Math.max(1, Number(limit) || 500));
      const out = [];
      for (const r of rows) {
        try { out.push(Object.assign(JSON.parse(r.stats_json), { at: r.observed_at * 1000 })); }
        catch { /* a corrupt row is one lost observation, not a failed call */ }
      }
      return out;
    },

    /** Never throws: losing a bookkeeping row must not fail a paid request. */
    recordRemoteSettlement(r) {
      return stmts.putRemoteSettlement.run({
        settled_at: Math.floor((r.settledAt || Date.now()) / 1000),
        product: String(r.product),
        resource: r.resource === undefined ? null : r.resource,
        payer: r.payer || null,
        tx: r.tx || null,
        network: r.network || null,
        asset: r.asset || null,
        amount_atomic: String(r.amountAtomic),
        price_usd: r.priceUsd === undefined ? null : String(r.priceUsd),
        facilitator: String(r.facilitator || 'remote'),
      });
    },
    listRemoteSettlements(limit = 50) { return stmts.listRemoteSettlements.all(Math.max(1, Number(limit) || 50)); },
    remoteRevenue(sinceMs) { return stmts.remoteRevenue.all(Math.floor((sinceMs || 0) / 1000)); },

    putProbe(p) { return stmts.putProbe.run(p); },
    getProbe(key) { return stmts.getProbe.get(String(key)) || null; },
    allProbes() { return stmts.allProbes.all(); },
    probeStats() {
      const rows = stmts.probeStats.all();
      const out = {};
      for (const r of rows) out[r.outcome] = r.n;
      const f = stmts.probeFreshness.get() || {};
      return { by_outcome: out, total: f.total || 0, oldest_probed_at: f.oldest || null, newest_probed_at: f.newest || null };
    },

    putForecast(f) {
      stmts.putForecast.run({
        forecast_id: String(f.forecastId),
        commitment: String(f.commitment),
        blob_id: f.blobId ? String(f.blobId) : null,
        question: String(f.question),
        market_id: f.marketId ? String(f.marketId) : null,
        market_slug: f.marketSlug ? String(f.marketSlug) : null,
        market_price: f.marketPrice === null || f.marketPrice === undefined ? null : String(f.marketPrice),
        model_prob: String(f.modelProb),
        model_reasoning: f.modelReasoning ? String(f.modelReasoning).slice(0, 2000) : null,
        model_name: f.modelName ? String(f.modelName) : null,
        head_height: f.headHeight === null || f.headHeight === undefined ? null : Number(f.headHeight),
        head_hash: f.headHash ? String(f.headHash) : null,
        end_date: f.endDate ? String(f.endDate) : null,
        created_at: Number(f.createdAt || nowSec()),
      });
      return true;
    },
    getForecast(commitment) { return stmts.getForecast.get(String(commitment)) || null; },
    openForecasts(limit = 25) { return stmts.openForecasts.all(Number(limit)); },
    resolveForecast({ forecastId, outcome, brierModel, brierMarket, at }) {
      return stmts.resolveForecast.run({
        forecast_id: String(forecastId),
        resolved_at: Number(at || nowSec()),
        resolved_outcome: String(outcome),
        brier_model: brierModel === null || brierModel === undefined ? null : String(brierModel),
        brier_market: brierMarket === null || brierMarket === undefined ? null : String(brierMarket),
      }).changes === 1;
    },
    forecastStats() { return stmts.forecastStats.get(); },
    recentForecasts(limit = 20) { return stmts.recentForecasts.all(Number(limit)); },
    /**
     * Basis points already sold across every lease overlapping this window.
     * OVERSUBSCRIPTION MUST BE IMPOSSIBLE: the caller adds the requested bps
     * to this and refuses the sale if the total exceeds the configured
     * ceiling. Read inside the same transaction as the insert by the caller.
     */
    overlappingLeaseBps({ startHeight, endHeight }) {
      return Number(stmts.sumOverlappingBps.get({
        start_height: Number(startHeight), end_height: Number(endHeight),
      }).bps || 0);
    },
    /** Sell a lease only if it fits under the ceiling — checked and written
     *  in ONE transaction, so two concurrent buyers cannot both fit. */
    sellLeaseIfRoom({ maxBps, lease }) {
      const run = db.transaction(() => {
        const sold = Number(stmts.sumOverlappingBps.get({
          start_height: Number(lease.startHeight), end_height: Number(lease.endHeight),
        }).bps || 0);
        const want = Number(lease.shareBps);
        if (sold + want > Number(maxBps)) {
          return { ok: false, reason: 'oversubscribed', soldBps: sold, availableBps: Math.max(0, Number(maxBps) - sold) };
        }
        stmts.putLease.run({
          lease_id: String(lease.leaseId),
          buyer_address: String(lease.buyerAddress),
          share_bps: want,
          start_height: Number(lease.startHeight),
          end_height: Number(lease.endHeight),
          paid_usd: String(lease.paidUsd),
          quoted_nanm: BigInt(lease.quotedNanm).toString(),
          rate_usd_anm: String(lease.rateUsdAnm),
          created_at: nowSec(),
        });
        return { ok: true, soldBpsAfter: sold + want };
      });
      return run();
    },

    /** Only fully-spent, expired vouchers are removable — a voucher with a
     *  balance is somebody's money and is never pruned by a timer. */
    pruneVouchers() {
      return stmts.pruneVouchers.run(nowSec()).changes;
    },

    // ------------------------------------------------------------ paid crawl
    // Registration is free and needs no account, so every one of these is
    // reachable without a payment. The only thing money touches is the pass.

    putCrawlSite(site) {
      const at = nowSec();
      stmts.putSite.run({
        domain: String(site.domain).toLowerCase(),
        price_usd: String(site.priceUsd),
        free_per_day: Number(site.freePerDay),
        unknown_policy: String(site.unknownPolicy),
        rate_threshold: Number(site.rateThreshold),
        operator_share_bps: Number(site.operatorShareBps),
        payout_address: site.payoutAddress ? String(site.payoutAddress) : null,
        payout_network: site.payoutNetwork ? String(site.payoutNetwork) : null,
        allow_ua_json: JSON.stringify(site.allowUa || []),
        free_paths_json: JSON.stringify(site.freePaths || []),
        enabled: site.enabled === false ? 0 : 1,
        verify_token: String(site.verifyToken),
        contact: site.contact ? String(site.contact) : null,
        created_at: at,
        updated_at: at,
      });
      return stmts.getSite.get(String(site.domain).toLowerCase());
    },
    getCrawlSite(domain) { return stmts.getSite.get(String(domain || '').toLowerCase()) || null; },
    listCrawlSites(limit = 100) { return stmts.listSites.all(Number(limit)); },
    markCrawlSiteVerified(domain) {
      return stmts.markSiteVerified.run({ domain: String(domain).toLowerCase(), at: nowSec() }).changes === 1;
    },
    deleteCrawlSite(domain) { return stmts.deleteSite.run(String(domain).toLowerCase()).changes === 1; },

    crawlUsage(domain, clientKey, day) {
      const row = stmts.getUsage.get(String(domain).toLowerCase(), String(clientKey), String(day));
      return row ? Number(row.used) : 0;
    },
    /**
     * Consume one unit of grace and return the count AFTER this call, in ONE
     * transaction. Read-then-write would let two concurrent requests from the
     * same crawler both observe "99 used" and both pass free; the UPSERT plus
     * transaction makes the counter authoritative.
     */
    bumpCrawlUsage(domain, clientKey, day) {
      const run = db.transaction(() => {
        stmts.bumpUsage.run({
          domain: String(domain).toLowerCase(), client_key: String(clientKey), day: String(day), at: nowSec(),
        });
        return Number(stmts.getUsage.get(String(domain).toLowerCase(), String(clientKey), String(day)).used);
      });
      return run();
    },
    pruneCrawlUsage(beforeDay) { return stmts.pruneUsage.run(String(beforeDay)).changes; },

    putCrawlPass(pass) {
      stmts.putPass.run({
        pass_id: String(pass.passId),
        token_hash: String(pass.tokenHash),
        domain: String(pass.domain).toLowerCase(),
        requests_total: Number(pass.requestsTotal),
        price_usd: String(pass.priceUsd),
        paid_usd: String(pass.paidUsd),
        payer: pass.payer ? String(pass.payer) : null,
        payment_fingerprint: pass.paymentFingerprint ? String(pass.paymentFingerprint) : null,
        issued_at: Number(pass.issuedAt || nowSec()),
        expires_at: Number(pass.expiresAt),
      });
      return true;
    },
    getCrawlPass(tokenHash) { return stmts.getPassByHash.get(String(tokenHash)) || null; },

    /**
     * Spend one request from a pass AND write the earnings row, atomically.
     *
     * Both halves must be one transaction: a spend without an event is
     * revenue the site owner is never credited for, and an event without a
     * spend is a request the payer is charged for twice. The UPDATE carries
     * its own guard (requests_used < requests_total AND not expired), so an
     * exhausted or expired pass changes zero rows and the caller learns the
     * spend failed rather than silently serving a free page.
     */
    spendCrawlPass({ tokenHash, domain, actor, operator, kind, path, priceUsd, operatorShareBps }) {
      const at = nowSec();
      const run = db.transaction(() => {
        const changed = stmts.spendPass.run({ token_hash: String(tokenHash), at }).changes;
        if (changed !== 1) return { ok: false, reason: 'pass_exhausted_or_expired' };
        const row = stmts.getPassByHash.get(String(tokenHash));
        const price = Number(priceUsd);
        const bps = Number(operatorShareBps);
        const operatorUsd = (price * bps) / 10000;
        const gatewayUsd = price - operatorUsd;
        stmts.addCrawlEvent.run({
          domain: String(domain).toLowerCase(),
          pass_id: row ? row.pass_id : null,
          actor: actor ? String(actor) : null,
          operator: operator ? String(operator) : null,
          kind: String(kind || 'unknown'),
          path: path ? String(path).slice(0, 512) : null,
          price_usd: price.toFixed(6),
          operator_usd: operatorUsd.toFixed(6),
          gateway_usd: gatewayUsd.toFixed(6),
          at,
        });
        return {
          ok: true,
          remaining: Number(row.requests_total) - Number(row.requests_used),
          operatorUsd: operatorUsd.toFixed(6),
        };
      });
      return run();
    },
    pruneCrawlPasses() { return stmts.prunePasses.run(nowSec()).changes; },

    crawlEarnings(domain, sinceSec = 0) {
      return stmts.earningsFor.get(String(domain).toLowerCase(), Number(sinceSec));
    },
    crawlTopActors(domain, sinceSec = 0, limit = 10) {
      return stmts.topActorsFor.all(String(domain).toLowerCase(), Number(sinceSec), Number(limit));
    },

    seeUnknownUa(userAgent) {
      const ua = String(userAgent || '').slice(0, 512);
      if (!ua) return false;
      const uaHash = crypto.createHash('sha256').update(ua).digest('hex').slice(0, 32);
      stmts.seeUnknownUa.run({ ua_hash: uaHash, user_agent: ua, at: nowSec() });
      return uaHash;
    },
    untriagedUserAgents(limit = 20) { return stmts.untriagedUa.all(Number(limit)); },
    setUaProposal({ uaHash, proposal, servedBy }) {
      return stmts.setUaProposal.run({
        ua_hash: String(uaHash),
        proposal_json: JSON.stringify(proposal || null),
        served_by: servedBy ? String(servedBy) : null,
        at: nowSec(),
      }).changes === 1;
    },
    listUaProposals(limit = 50) { return stmts.listUaProposals.all(Number(limit)); },

    ping() {
      db.prepare('SELECT 1').get();
      return true;
    },

    close() {
      db.close();
    },
  };
}

module.exports = { createGatewayStore };
