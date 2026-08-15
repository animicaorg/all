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
  status              TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','refunded','resolved')),
  created_at          INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_incidents_status ON incidents(status);
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
  db.exec(SCHEMA);

  const stmts = {
    putIdem: db.prepare(`INSERT OR IGNORE INTO idempotency
      (idem_key, payment_fingerprint, resource, response_status, content_type, content_encoding, body, oversize, settlement_header, settlement_tx, created_at)
      VALUES (@idem_key, @payment_fingerprint, @resource, @response_status, @content_type, @content_encoding, @body, @oversize, @settlement_header, @settlement_tx, @created_at)`),
    getIdem: db.prepare('SELECT * FROM idempotency WHERE idem_key = ? AND payment_fingerprint = ?'),
    pruneIdem: db.prepare('DELETE FROM idempotency WHERE created_at < ?'),
    addIncident: db.prepare(`INSERT INTO incidents
      (incident_id, payment_id, payment_fingerprint, settlement_tx, payer, resource, amount, network, kind, error, receipt_json, status, created_at)
      VALUES (@incident_id, @payment_id, @payment_fingerprint, @settlement_tx, @payer, @resource, @amount, @network, @kind, @error, @receipt_json, 'open', @created_at)`),
    listIncidents: db.prepare('SELECT * FROM incidents WHERE status = ? ORDER BY created_at DESC LIMIT ?'),
    listAllIncidents: db.prepare('SELECT * FROM incidents ORDER BY created_at DESC LIMIT ?'),
    getIncident: db.prepare('SELECT * FROM incidents WHERE incident_id = ?'),
    setIncidentStatus: db.prepare('UPDATE incidents SET status = @status WHERE incident_id = @incident_id'),
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

    addIncident({ paymentId, paymentFingerprint, settlementTx, payer, resource, amount, network, kind, error, receipt }) {
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
