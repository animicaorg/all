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
  };

  return {
    db, // tests/CLI only

    /**
     * Store a delivered outcome. First writer wins; returns true when this
     * call stored the row (false = someone beat us — harmless).
     */
    putIdempotent({ idemKey, paymentFingerprint, resource, status, contentType, contentEncoding, body, settlementHeader, settlementTx }) {
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
