/*
 Migration: 0001_init
 --------------------
 Baseline schema for AICF:
 - Providers / Jobs / Leases / Proof-Claims / Epochs / Payouts / Balances / Slashes
 - Time units: INTEGER unix-epoch seconds
 - Money units: INTEGER atomic units
 Apply with PRAGMA foreign_keys=ON; Use WAL if desired.

 This mirrors schema version 1.
*/

BEGIN TRANSACTION;

PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------------
-- Meta / schema version
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

INSERT OR IGNORE INTO meta(key, value) VALUES ('schema', '1');

-- ---------------------------------------------------------------------
-- Providers registry
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS providers (
    provider_id        TEXT PRIMARY KEY,
    display_name       TEXT,
    ai_enabled         INTEGER NOT NULL DEFAULT 0 CHECK (ai_enabled IN (0,1)),
    quantum_enabled    INTEGER NOT NULL DEFAULT 0 CHECK (quantum_enabled IN (0,1)),
    stake_amount       INTEGER NOT NULL DEFAULT 0 CHECK (stake_amount >= 0),
    status             TEXT NOT NULL DEFAULT 'INACTIVE'
                         CHECK (status IN ('INACTIVE','ACTIVE','JAILED','COOLDOWN','BANNED')),
    endpoint_api       TEXT,
    endpoint_attest    TEXT,
    region             TEXT,
    allowlisted        INTEGER NOT NULL DEFAULT 1 CHECK (allowlisted IN (0,1)),
    denylisted         INTEGER NOT NULL DEFAULT 0 CHECK (denylisted IN (0,1)),
    health_score       REAL  NOT NULL DEFAULT 0.0 CHECK (health_score >= 0.0 AND health_score <= 1.0),
    last_heartbeat     INTEGER,
    jail_until         INTEGER,
    cooldown_until     INTEGER,
    attestation_cert   BLOB,
    capabilities_json  TEXT,
    created_at         INTEGER NOT NULL,
    updated_at         INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_providers_status ON providers(status);
CREATE INDEX IF NOT EXISTS idx_providers_health ON providers(health_score);
CREATE INDEX IF NOT EXISTS idx_providers_region ON providers(region);
CREATE INDEX IF NOT EXISTS idx_providers_heartbeat ON providers(last_heartbeat);

CREATE TRIGGER IF NOT EXISTS trg_providers_set_timestamps_ins
BEFORE INSERT ON providers
FOR EACH ROW
BEGIN
  SELECT CASE
    WHEN NEW.created_at IS NULL THEN NEW.created_at := CAST(strftime('%s','now') AS INTEGER)
    ELSE NULL END;
  SELECT CASE
    WHEN NEW.updated_at IS NULL THEN NEW.updated_at := CAST(strftime('%s','now') AS INTEGER)
    ELSE NULL END;
END;

CREATE TRIGGER IF NOT EXISTS trg_providers_set_updated_at_upd
BEFORE UPDATE ON providers
FOR EACH ROW
BEGIN
  SELECT NEW.updated_at := CAST(strftime('%s','now') AS INTEGER);
END;

-- ---------------------------------------------------------------------
-- Jobs queue
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS jobs (
    job_id               TEXT PRIMARY KEY,
    kind                 TEXT NOT NULL CHECK (kind IN ('AI','QUANTUM')),
    requester            TEXT,
    fee_amount           INTEGER NOT NULL DEFAULT 0 CHECK (fee_amount >= 0),
    size_bytes           INTEGER NOT NULL DEFAULT 0 CHECK (size_bytes >= 0),
    priority             INTEGER NOT NULL DEFAULT 0,
    tier                 TEXT DEFAULT 'standard',
    status               TEXT NOT NULL DEFAULT 'QUEUED'
                           CHECK (status IN ('QUEUED','ASSIGNED','RUNNING','COMPLETED','FAILED','EXPIRED','CANCELLED')),
    spec_json            TEXT NOT NULL,
    queue_at             INTEGER NOT NULL,
    assigned_at          INTEGER,
    completed_at         INTEGER,
    failed_reason        TEXT,
    assigned_provider_id TEXT REFERENCES providers(provider_id) ON DELETE SET NULL,
    lease_id             TEXT,
    nullifier            TEXT UNIQUE
);

CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_kind ON jobs(kind);
CREATE INDEX IF NOT EXISTS idx_jobs_priority ON jobs(priority DESC, queue_at ASC);
CREATE INDEX IF NOT EXISTS idx_jobs_assigned_provider ON jobs(assigned_provider_id);
CREATE INDEX IF NOT EXISTS idx_jobs_queue_time ON jobs(queue_at);

-- ---------------------------------------------------------------------
-- Leases
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS leases (
    lease_id     TEXT PRIMARY KEY,
    job_id       TEXT NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
    provider_id  TEXT NOT NULL REFERENCES providers(provider_id) ON DELETE CASCADE,
    status       TEXT NOT NULL DEFAULT 'ACTIVE'
                   CHECK (status IN ('ACTIVE','RENEWED','EXPIRED','CANCELLED','LOST')),
    start_at     INTEGER NOT NULL,
    expires_at   INTEGER NOT NULL,
    renewals     INTEGER NOT NULL DEFAULT 0 CHECK (renewals >= 0),
    cancelled_at INTEGER,
    lost_reason  TEXT
);

CREATE INDEX IF NOT EXISTS idx_leases_provider ON leases(provider_id, status);
CREATE INDEX IF NOT EXISTS idx_leases_expiry ON leases(expires_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_leases_active_job
ON leases(job_id)
WHERE status IN ('ACTIVE','RENEWED');

-- ---------------------------------------------------------------------
-- Proof claims
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS proof_claims (
    claim_id      TEXT PRIMARY KEY,
    job_id        TEXT NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
    provider_id   TEXT NOT NULL REFERENCES providers(provider_id) ON DELETE CASCADE,
    block_height  INTEGER NOT NULL,
    nullifier     TEXT NOT NULL UNIQUE,
    units_ai      INTEGER NOT NULL DEFAULT 0 CHECK (units_ai >= 0),
    units_quantum INTEGER NOT NULL DEFAULT 0 CHECK (units_quantum >= 0),
    qos           REAL    CHECK (qos >= 0.0 AND qos <= 1.0),
    traps_ratio   REAL    CHECK (traps_ratio >= 0.0 AND traps_ratio <= 1.0),
    latency_ms    INTEGER CHECK (latency_ms >= 0),
    accepted      INTEGER NOT NULL DEFAULT 0 CHECK (accepted IN (0,1)),
    reason        TEXT,
    created_at    INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_claims_height ON proof_claims(block_height);
CREATE INDEX IF NOT EXISTS idx_claims_provider ON proof_claims(provider_id);

-- ---------------------------------------------------------------------
-- Epochs & payouts
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS epochs (
    epoch_id      INTEGER PRIMARY KEY,
    start_height  INTEGER NOT NULL,
    end_height    INTEGER,
    cap_fund      INTEGER NOT NULL DEFAULT 0 CHECK (cap_fund >= 0),
    total_payouts INTEGER NOT NULL DEFAULT 0 CHECK (total_payouts >= 0),
    settled       INTEGER NOT NULL DEFAULT 0 CHECK (settled IN (0,1)),
    settled_at    INTEGER
);

CREATE TABLE IF NOT EXISTS payouts (
    payout_id        TEXT PRIMARY KEY,
    job_id           TEXT NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
    provider_id      TEXT NOT NULL REFERENCES providers(provider_id) ON DELETE CASCADE,
    epoch_id         INTEGER NOT NULL REFERENCES epochs(epoch_id) ON DELETE CASCADE,
    amount_total     INTEGER NOT NULL CHECK (amount_total >= 0),
    amount_provider  INTEGER NOT NULL CHECK (amount_provider >= 0),
    amount_treasury  INTEGER NOT NULL CHECK (amount_treasury >= 0),
    amount_miner     INTEGER NOT NULL CHECK (amount_miner >= 0),
    status           TEXT NOT NULL DEFAULT 'PENDING'
                       CHECK (status IN ('PENDING','SETTLED','CANCELLED','REVERSED')),
    settled          INTEGER NOT NULL DEFAULT 0 CHECK (settled IN (0,1)),
    settled_at       INTEGER,
    tx_hash          TEXT,
    note             TEXT,
    created_at       INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_payouts_provider ON payouts(provider_id, status);
CREATE INDEX IF NOT EXISTS idx_payouts_epoch ON payouts(epoch_id, status);
CREATE INDEX IF NOT EXISTS idx_payouts_settled ON payouts(settled, settled_at);

-- ---------------------------------------------------------------------
-- Provider balances / escrows
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS provider_balances (
    provider_id  TEXT PRIMARY KEY REFERENCES providers(provider_id) ON DELETE CASCADE,
    balance      INTEGER NOT NULL DEFAULT 0 CHECK (balance >= 0),
    escrow       INTEGER NOT NULL DEFAULT 0 CHECK (escrow >= 0),
    locked_until INTEGER
);

-- ---------------------------------------------------------------------
-- Slashing events
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS slashes (
    slash_id       TEXT PRIMARY KEY,
    provider_id    TEXT NOT NULL REFERENCES providers(provider_id) ON DELETE CASCADE,
    reason_code    TEXT NOT NULL,
    magnitude      INTEGER NOT NULL CHECK (magnitude >= 0),
    penalty_amount INTEGER NOT NULL DEFAULT 0 CHECK (penalty_amount >= 0),
    at_height      INTEGER NOT NULL,
    jail_until     INTEGER,
    created_at     INTEGER NOT NULL,
    notes          TEXT
);

CREATE INDEX IF NOT EXISTS idx_slashes_provider ON slashes(provider_id, created_at DESC);

COMMIT;

-- Optionally set user_version for SQLite-aware clients
PRAGMA user_version = 1;
