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
BEFORE INSERT ON aicf_miner_credits
FOR EACH ROW
BEGIN
  SELECT CASE
    WHEN NEW.created_at IS NULL THEN NEW.created_at := CAST(strftime('%s','now') AS INTEGER)
    ELSE NULL END;
  SELECT CASE
    WHEN NEW.updated_at IS NULL THEN NEW.updated_at := CAST(strftime('%s','now') AS INTEGER)
    ELSE NULL END;
END;

CREATE TRIGGER IF NOT EXISTS trg_miner_credits_updated_at_upd
BEFORE UPDATE ON aicf_miner_credits
FOR EACH ROW
BEGIN
  SELECT NEW.updated_at := CAST(strftime('%s','now') AS INTEGER);
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
BEFORE INSERT ON aicf_pool_credits
FOR EACH ROW
BEGIN
  SELECT CASE
    WHEN NEW.created_at IS NULL THEN NEW.created_at := CAST(strftime('%s','now') AS INTEGER)
    ELSE NULL END;
  SELECT CASE
    WHEN NEW.updated_at IS NULL THEN NEW.updated_at := CAST(strftime('%s','now') AS INTEGER)
    ELSE NULL END;
END;

CREATE TRIGGER IF NOT EXISTS trg_pool_credits_updated_at_upd
BEFORE UPDATE ON aicf_pool_credits
FOR EACH ROW
BEGIN
  SELECT NEW.updated_at := CAST(strftime('%s','now') AS INTEGER);
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
