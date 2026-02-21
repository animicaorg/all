/*
 AICF Protocol Extension Schema
 ------------------------------
 GPU Contributor Redistribution Protocol
 
 Extends the base AICF schema with:
 - Protocol parameters and configuration
 - GPU worker registration and management  
 - Job specifications for training/eval tasks
 - Work submissions with proof commitments
 - Challenge and verification system
 - Credit accounting for epochs
 - Claim tracking and payout management
 - Model release tracking
 - Inflow deposits and revenue attribution
 
 Bump PRAGMA user_version on breaking changes.
*/

PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

BEGIN TRANSACTION;

-- ---------------------------------------------------------------------
-- Protocol parameters
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS protocol_params (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at INTEGER NOT NULL
);

-- Default parameters
INSERT OR IGNORE INTO protocol_params(key, value, updated_at) VALUES 
    ('epoch_length_blocks', '1000', CAST(strftime('%s','now') AS INTEGER)),
    ('challenge_window_blocks', '100', CAST(strftime('%s','now') AS INTEGER)),
    ('min_stake', '1000000000', CAST(strftime('%s','now') AS INTEGER)),
    ('max_workers', '1000', CAST(strftime('%s','now') AS INTEGER)),
    ('reward_split_gpu_workers_bp', '7000', CAST(strftime('%s','now') AS INTEGER)),
    ('reward_split_treasury_bp', '2000', CAST(strftime('%s','now') AS INTEGER)),
    ('reward_split_dev_bp', '500', CAST(strftime('%s','now') AS INTEGER)),
    ('reward_split_burn_bp', '500', CAST(strftime('%s','now') AS INTEGER)),
    ('verify_policy', 'mvp_challenge_window', CAST(strftime('%s','now') AS INTEGER));

-- ---------------------------------------------------------------------
-- GPU Workers registry
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS gpu_workers (
    worker_id        TEXT PRIMARY KEY,           -- deterministic worker ID
    address          TEXT NOT NULL,              -- payout address
    pubkey           TEXT,                       -- verification public key
    display_name     TEXT,
    metadata_json    TEXT,                       -- JSON: capabilities, hardware info
    stake_tx_hash    TEXT,                       -- optional stake transaction
    stake_amount     INTEGER NOT NULL DEFAULT 0 CHECK (stake_amount >= 0),
    status           TEXT NOT NULL DEFAULT 'INACTIVE'
                       CHECK (status IN ('INACTIVE','ACTIVE','JAILED','BANNED')),
    region           TEXT,
    created_at       INTEGER NOT NULL,
    updated_at       INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_gpu_workers_status ON gpu_workers(status);
CREATE INDEX IF NOT EXISTS idx_gpu_workers_address ON gpu_workers(address);

CREATE TRIGGER IF NOT EXISTS trg_gpu_workers_timestamps_ins
AFTER INSERT ON gpu_workers
FOR EACH ROW
BEGIN
  UPDATE gpu_workers
  SET
    created_at = COALESCE(NEW.created_at, CAST(strftime('%s','now') AS INTEGER)),
    updated_at = COALESCE(NEW.updated_at, CAST(strftime('%s','now') AS INTEGER))
  WHERE rowid = NEW.rowid;
END;

CREATE TRIGGER IF NOT EXISTS trg_gpu_workers_updated_at_upd
AFTER UPDATE ON gpu_workers
FOR EACH ROW
WHEN NEW.updated_at = OLD.updated_at
BEGIN
  UPDATE gpu_workers
  SET updated_at = CAST(strftime('%s','now') AS INTEGER)
  WHERE rowid = NEW.rowid;
END;

-- ---------------------------------------------------------------------
-- Training/Eval Jobs
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS training_jobs (
    job_id           TEXT PRIMARY KEY,
    spec_hash        TEXT NOT NULL,              -- commitment to job specification
    dataset_commit   TEXT,                       -- dataset commitment
    job_type         TEXT NOT NULL CHECK (job_type IN ('TRAINING','EVAL','FINETUNE')),
    difficulty       INTEGER NOT NULL DEFAULT 1,
    reward_weight    INTEGER NOT NULL DEFAULT 100,
    created_at       INTEGER NOT NULL,
    expires_at       INTEGER,
    creator          TEXT,                       -- admin/governance address
    status           TEXT NOT NULL DEFAULT 'OPEN'
                       CHECK (status IN ('OPEN','ASSIGNED','COMPLETED','EXPIRED','CANCELLED'))
);

CREATE INDEX IF NOT EXISTS idx_training_jobs_status ON training_jobs(status);
CREATE INDEX IF NOT EXISTS idx_training_jobs_type ON training_jobs(job_type);
CREATE INDEX IF NOT EXISTS idx_training_jobs_expires ON training_jobs(expires_at);

-- ---------------------------------------------------------------------
-- Work Submissions
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS work_submissions (
    submission_id     TEXT PRIMARY KEY,
    job_id            TEXT NOT NULL REFERENCES training_jobs(job_id) ON DELETE CASCADE,
    worker_id         TEXT NOT NULL REFERENCES gpu_workers(worker_id) ON DELETE CASCADE,
    artifact_commit   TEXT NOT NULL,            -- commitment to output artifacts (model delta, etc.)
    metrics_json      TEXT,                     -- JSON: evaluation metrics
    proof_commit      TEXT,                     -- commitment to verification proof data
    status            TEXT NOT NULL DEFAULT 'PENDING'
                        CHECK (status IN ('PENDING','ACCEPTED','REJECTED','CHALLENGED')),
    posted_at         INTEGER NOT NULL,
    challenge_deadline INTEGER NOT NULL,        -- block height deadline
    verified_by       TEXT,                     -- optional verifier attestation
    rejection_reason  TEXT,
    credits_awarded   INTEGER NOT NULL DEFAULT 0 CHECK (credits_awarded >= 0)
);

CREATE INDEX IF NOT EXISTS idx_submissions_job ON work_submissions(job_id);
CREATE INDEX IF NOT EXISTS idx_submissions_worker ON work_submissions(worker_id);
CREATE INDEX IF NOT EXISTS idx_submissions_status ON work_submissions(status);
CREATE INDEX IF NOT EXISTS idx_submissions_deadline ON work_submissions(challenge_deadline);

-- ---------------------------------------------------------------------
-- Challenges
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS submission_challenges (
    challenge_id         TEXT PRIMARY KEY,
    submission_id        TEXT NOT NULL REFERENCES work_submissions(submission_id) ON DELETE CASCADE,
    challenger_address   TEXT NOT NULL,
    challenge_data_commit TEXT NOT NULL,       -- commitment to challenge evidence
    status               TEXT NOT NULL DEFAULT 'OPEN'
                           CHECK (status IN ('OPEN','RESOLVED_VALID','RESOLVED_INVALID','EXPIRED')),
    posted_at            INTEGER NOT NULL,
    resolved_at          INTEGER,
    resolution_commit    TEXT,
    notes                TEXT
);

CREATE INDEX IF NOT EXISTS idx_challenges_submission ON submission_challenges(submission_id);
CREATE INDEX IF NOT EXISTS idx_challenges_status ON submission_challenges(status);

-- ---------------------------------------------------------------------
-- Epoch Credits (per worker per epoch)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS epoch_credits (
    epoch_id    INTEGER NOT NULL,
    worker_id   TEXT NOT NULL REFERENCES gpu_workers(worker_id) ON DELETE CASCADE,
    credits     TEXT NOT NULL DEFAULT '0',       -- u256 as decimal string
    PRIMARY KEY (epoch_id, worker_id)
);

CREATE INDEX IF NOT EXISTS idx_epoch_credits_worker ON epoch_credits(worker_id);

-- ---------------------------------------------------------------------
-- Protocol Epochs
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS protocol_epochs (
    epoch_id           INTEGER PRIMARY KEY,
    start_height       INTEGER NOT NULL,
    end_height         INTEGER,
    inflow_total       TEXT NOT NULL DEFAULT '0',       -- u256 as decimal string
    inflow_ena         TEXT NOT NULL DEFAULT '0',       -- u256 from ENA payments
    inflow_other       TEXT NOT NULL DEFAULT '0',       -- u256 from other sources
    inflow_for_workers TEXT NOT NULL DEFAULT '0',       -- u256 after split
    total_credits      TEXT NOT NULL DEFAULT '0',       -- u256 total credits issued
    finalized          INTEGER NOT NULL DEFAULT 0 CHECK (finalized IN (0,1)),
    finalized_at       INTEGER,
    merkle_root        TEXT                             -- optional for large claim sets
);

CREATE INDEX IF NOT EXISTS idx_protocol_epochs_finalized ON protocol_epochs(finalized);

-- ---------------------------------------------------------------------
-- Claims tracking
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS worker_claims (
    claim_id      TEXT PRIMARY KEY,
    epoch_id      INTEGER NOT NULL REFERENCES protocol_epochs(epoch_id) ON DELETE CASCADE,
    worker_id     TEXT NOT NULL REFERENCES gpu_workers(worker_id) ON DELETE CASCADE,
    amount        TEXT NOT NULL,                -- u256 as decimal string
    merkle_proof  TEXT,                         -- optional merkle proof JSON
    claimed_at    INTEGER NOT NULL,
    tx_hash       TEXT,
    status        TEXT NOT NULL DEFAULT 'PENDING'
                    CHECK (status IN ('PENDING','PAID','FAILED')),
    UNIQUE(epoch_id, worker_id)                 -- one claim per worker per epoch
);

CREATE INDEX IF NOT EXISTS idx_worker_claims_worker ON worker_claims(worker_id);
CREATE INDEX IF NOT EXISTS idx_worker_claims_status ON worker_claims(status);

-- ---------------------------------------------------------------------
-- AICF Inflows (deposits from ENA and other sources)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS aicf_inflows (
    inflow_id     TEXT PRIMARY KEY,
    source        TEXT NOT NULL,                -- 'ena', 'other', etc.
    amount        TEXT NOT NULL,                -- u256 as decimal string
    tx_hash       TEXT,
    block_height  INTEGER,
    epoch_id      INTEGER,
    recorded_at   INTEGER NOT NULL,
    metadata_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_inflows_source ON aicf_inflows(source);
CREATE INDEX IF NOT EXISTS idx_inflows_epoch ON aicf_inflows(epoch_id);
CREATE INDEX IF NOT EXISTS idx_inflows_tx ON aicf_inflows(tx_hash);

-- ---------------------------------------------------------------------
-- Model Releases
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS model_releases (
    release_id          TEXT PRIMARY KEY,
    base_model          TEXT NOT NULL,
    delta_commit        TEXT NOT NULL,          -- commitment to model delta/LoRA
    dataset_commit      TEXT,
    eval_metrics_json   TEXT,                   -- JSON: loss, perplexity, etc.
    produced_from_epochs TEXT,                  -- JSON array of epoch IDs
    approved_by         TEXT,                   -- governance/admin address
    timestamp           INTEGER NOT NULL,
    version             TEXT,
    notes               TEXT
);

CREATE INDEX IF NOT EXISTS idx_model_releases_base ON model_releases(base_model);
CREATE INDEX IF NOT EXISTS idx_model_releases_timestamp ON model_releases(timestamp DESC);

COMMIT;
/*
 AICF Credits Extension - Migration 0002
 ---------------------------------------
 Adds state tracking for AICF credits minted from mining rewards.
 
 Tables:
 - aicf_credit_totals: Global AICF credit accounting
 - aicf_miner_credits: Per-miner credit balances
 - aicf_pool_credits: Per-pool credit balances (optional)
 - aicf_credit_ledger: Immutable event log for all credit operations
 
 All credit amounts stored as TEXT (u256 decimal string) for consistency.
*/

PRAGMA foreign_keys = ON;

BEGIN TRANSACTION;

-- ---------------------------------------------------------------------
-- Global AICF credit totals
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS aicf_credit_totals (
    id               INTEGER PRIMARY KEY CHECK (id = 1), -- singleton row
    balance_total    TEXT NOT NULL DEFAULT '0',    -- total credits available
    minted_total     TEXT NOT NULL DEFAULT '0',    -- total credits minted from mining
    spent_total      TEXT NOT NULL DEFAULT '0',    -- total credits spent on jobs
    last_update_height INTEGER,                     -- last block that updated credits
    last_update_hash TEXT,                          -- last block hash
    updated_at       INTEGER NOT NULL
);

-- Initialize singleton row
INSERT OR IGNORE INTO aicf_credit_totals(id, balance_total, minted_total, spent_total, updated_at)
VALUES (1, '0', '0', '0', CAST(strftime('%s','now') AS INTEGER));

-- ---------------------------------------------------------------------
-- Per-miner credit balances
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS aicf_miner_credits (
    miner_address    TEXT PRIMARY KEY,             -- 32-byte hex address
    balance          TEXT NOT NULL DEFAULT '0',    -- available credits
    lifetime_earned  TEXT NOT NULL DEFAULT '0',    -- total credits ever earned
    lifetime_spent   TEXT NOT NULL DEFAULT '0',    -- total credits ever spent
    last_mint_height INTEGER,                       -- last block height that minted
    last_mint_hash   TEXT,                          -- last block hash that minted
    created_at       INTEGER NOT NULL,
    updated_at       INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_miner_credits_balance ON aicf_miner_credits(balance);
CREATE INDEX IF NOT EXISTS idx_miner_credits_earned ON aicf_miner_credits(lifetime_earned DESC);

-- Timestamp triggers
CREATE TRIGGER IF NOT EXISTS trg_miner_credits_timestamps_ins
AFTER INSERT ON aicf_miner_credits
FOR EACH ROW
BEGIN
  UPDATE aicf_miner_credits
  SET
    created_at = COALESCE(NEW.created_at, CAST(strftime('%s','now') AS INTEGER)),
    updated_at = COALESCE(NEW.updated_at, CAST(strftime('%s','now') AS INTEGER))
  WHERE rowid = NEW.rowid;
END;

CREATE TRIGGER IF NOT EXISTS trg_miner_credits_updated_at_upd
AFTER UPDATE ON aicf_miner_credits
FOR EACH ROW
WHEN NEW.updated_at = OLD.updated_at
BEGIN
  UPDATE aicf_miner_credits
  SET updated_at = CAST(strftime('%s','now') AS INTEGER)
  WHERE rowid = NEW.rowid;
END;

-- ---------------------------------------------------------------------
-- Per-pool credit balances (optional for pool mining)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS aicf_pool_credits (
    pool_address     TEXT PRIMARY KEY,             -- pool operator address
    balance          TEXT NOT NULL DEFAULT '0',    -- available credits
    lifetime_earned  TEXT NOT NULL DEFAULT '0',    -- total credits ever earned
    lifetime_spent   TEXT NOT NULL DEFAULT '0',    -- total credits ever spent
    last_mint_height INTEGER,                       -- last block height that minted
    last_mint_hash   TEXT,                          -- last block hash that minted
    created_at       INTEGER NOT NULL,
    updated_at       INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_pool_credits_balance ON aicf_pool_credits(balance);
CREATE INDEX IF NOT EXISTS idx_pool_credits_earned ON aicf_pool_credits(lifetime_earned DESC);

-- Timestamp triggers
CREATE TRIGGER IF NOT EXISTS trg_pool_credits_timestamps_ins
AFTER INSERT ON aicf_pool_credits
FOR EACH ROW
BEGIN
  UPDATE aicf_pool_credits
  SET
    created_at = COALESCE(NEW.created_at, CAST(strftime('%s','now') AS INTEGER)),
    updated_at = COALESCE(NEW.updated_at, CAST(strftime('%s','now') AS INTEGER))
  WHERE rowid = NEW.rowid;
END;

CREATE TRIGGER IF NOT EXISTS trg_pool_credits_updated_at_upd
AFTER UPDATE ON aicf_pool_credits
FOR EACH ROW
WHEN NEW.updated_at = OLD.updated_at
BEGIN
  UPDATE aicf_pool_credits
  SET updated_at = CAST(strftime('%s','now') AS INTEGER)
  WHERE rowid = NEW.rowid;
END;

-- ---------------------------------------------------------------------
-- AICF Credit Ledger (immutable event log)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS aicf_credit_ledger (
    ledger_id        TEXT PRIMARY KEY,             -- deterministic ID (sha256 of event data)
    event_type       TEXT NOT NULL                 -- 'credit_minted' | 'credit_spent'
                       CHECK (event_type IN ('credit_minted', 'credit_spent')),
    block_height     INTEGER NOT NULL,
    block_hash       TEXT NOT NULL,
    amount           TEXT NOT NULL,                -- credit amount (u256 decimal)
    source           TEXT,                         -- 'reward' | 'fees' | 'share' (for minted)
    miner_address    TEXT,                         -- for credit_minted events
    pool_address     TEXT,                         -- for pool credits (optional)
    job_id           TEXT,                         -- for credit_spent events
    recipients_json  TEXT,                         -- JSON array of recipients for spent events
    timestamp        INTEGER NOT NULL,
    metadata_json    TEXT                          -- additional event data
);

CREATE INDEX IF NOT EXISTS idx_credit_ledger_type ON aicf_credit_ledger(event_type);
CREATE INDEX IF NOT EXISTS idx_credit_ledger_height ON aicf_credit_ledger(block_height DESC);
CREATE INDEX IF NOT EXISTS idx_credit_ledger_miner ON aicf_credit_ledger(miner_address);
CREATE INDEX IF NOT EXISTS idx_credit_ledger_job ON aicf_credit_ledger(job_id);
CREATE INDEX IF NOT EXISTS idx_credit_ledger_timestamp ON aicf_credit_ledger(timestamp DESC);

COMMIT;
