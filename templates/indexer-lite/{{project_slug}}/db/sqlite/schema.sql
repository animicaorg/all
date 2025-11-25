-- SQLite schema for {{ project_slug }} Indexer Lite
-- Focus: canonical blocks, transactions, logs, addresses, and PoIES metrics.

-- -------------- Pragmas (applied by the first connection) ------------------
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
PRAGMA foreign_keys=ON;
PRAGMA temp_store=MEMORY;

-- -------------- Meta -------------------------------------------------------
CREATE TABLE IF NOT EXISTS meta (
  key           TEXT PRIMARY KEY,
  value         TEXT NOT NULL,
  updated_at    DATETIME NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
) STRICT;

INSERT OR IGNORE INTO meta(key, value) VALUES ('schema_version','1');

-- -------------- Chain state / tips ----------------------------------------
CREATE TABLE IF NOT EXISTS chain_tips (
  name         TEXT PRIMARY KEY,              -- e.g. 'canonical'
  number       INTEGER NOT NULL,              -- latest known block number
  hash         TEXT NOT NULL,                 -- 0x-prefixed
  updated_at   DATETIME NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
) STRICT;

-- -------------- Blocks -----------------------------------------------------
CREATE TABLE IF NOT EXISTS blocks (
  number            INTEGER PRIMARY KEY,      -- canonical height
  hash              TEXT    NOT NULL UNIQUE,  -- 0x...
  parent_hash       TEXT    NOT NULL,         -- 0x...
  timestamp         INTEGER NOT NULL,         -- seconds since epoch
  miner             TEXT,                     -- coinbase / producer
  tx_count          INTEGER NOT NULL DEFAULT 0,
  gas_used          TEXT,                     -- big-int as text if applicable
  gas_limit         TEXT,

  -- consensus envelope (best-effort / optional)
  gamma             INTEGER,                  -- Γ (control variable)
  psi               INTEGER,                  -- ψ (accept pressure)
  participation     REAL,                     -- 0..1 if provided
  mix_json          TEXT,                     -- JSON map of mix components
  randomness_json   TEXT,                     -- alt JSON if node exposes it

  -- ingest metadata
  ingested_at       DATETIME NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  UNIQUE(number, hash),
  CHECK (mix_json IS NULL OR json_valid(mix_json)),
  CHECK (randomness_json IS NULL OR json_valid(randomness_json)),
  CHECK (participation IS NULL OR (participation >= 0.0 AND participation <= 1.0))
) STRICT WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS idx_blocks_hash ON blocks(hash);
CREATE INDEX IF NOT EXISTS idx_blocks_timestamp ON blocks(timestamp);
CREATE INDEX IF NOT EXISTS idx_blocks_miner ON blocks(miner);

-- -------------- Per-block PoIES scores ------------------------------------
-- Derived indicators computed by the indexer (see indexer.poies).
CREATE TABLE IF NOT EXISTS poies_scores (
  block_number      INTEGER PRIMARY KEY REFERENCES blocks(number) ON DELETE CASCADE,
  mix_entropy       REAL,
  lateness_seconds  REAL,
  tags              TEXT,               -- comma-separated notes; small & human-friendly
  computed_at       DATETIME NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
) STRICT WITHOUT ROWID;

-- -------------- Transactions ----------------------------------------------
CREATE TABLE IF NOT EXISTS txs (
  hash              TEXT PRIMARY KEY,            -- 0x...
  block_number      INTEGER NOT NULL REFERENCES blocks(number) ON DELETE CASCADE,
  tx_index          INTEGER NOT NULL,            -- index within block
  from_addr         TEXT NOT NULL,
  to_addr           TEXT,                        -- can be NULL for contract creation
  value             TEXT,                        -- big-int as text
  fee_paid          TEXT,                        -- big-int as text if available
  nonce             TEXT,                        -- big-int as text
  gas_limit         TEXT,
  gas_used          TEXT,
  method_sig        TEXT,                        -- optional decoded method (e.g., "transfer(address,uint256)")
  input             BLOB,                        -- raw input payload
  status            INTEGER,                     -- 1 success, 0 fail (if available)
  created_contract  TEXT,                        -- address if this was a create
  ingested_at       DATETIME NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  UNIQUE(block_number, tx_index)
) STRICT;

CREATE INDEX IF NOT EXISTS idx_txs_block ON txs(block_number, tx_index);
CREATE INDEX IF NOT EXISTS idx_txs_from ON txs(from_addr);
CREATE INDEX IF NOT EXISTS idx_txs_to ON txs(to_addr);
CREATE INDEX IF NOT EXISTS idx_txs_method ON txs(method_sig);

-- -------------- Logs / Events ---------------------------------------------
CREATE TABLE IF NOT EXISTS logs (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  tx_hash           TEXT NOT NULL REFERENCES txs(hash) ON DELETE CASCADE,
  log_index         INTEGER NOT NULL,       -- index within tx receipt
  address           TEXT NOT NULL,          -- emitting contract
  topic0            TEXT,                   -- 0x...
  topic1            TEXT,
  topic2            TEXT,
  topic3            TEXT,
  data              BLOB,
  UNIQUE(tx_hash, log_index)
) STRICT;

CREATE INDEX IF NOT EXISTS idx_logs_addr ON logs(address);
CREATE INDEX IF NOT EXISTS idx_logs_topic0 ON logs(topic0);
CREATE INDEX IF NOT EXISTS idx_logs_tx ON logs(tx_hash);

-- -------------- Addresses (light catalog) ----------------------------------
CREATE TABLE IF NOT EXISTS addresses (
  address           TEXT PRIMARY KEY,
  first_seen_block  INTEGER,
  last_seen_block   INTEGER,
  tx_out            INTEGER NOT NULL DEFAULT 0,   -- sent count
  tx_in             INTEGER NOT NULL DEFAULT 0,   -- received count
  updated_at        DATETIME NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
) STRICT WITHOUT ROWID;

-- Helper triggers to maintain addresses table from txs inserts.
CREATE TRIGGER IF NOT EXISTS trg_addresses_from_ins
AFTER INSERT ON txs
BEGIN
  INSERT INTO addresses(address, first_seen_block, last_seen_block, tx_out, tx_in)
  VALUES (NEW.from_addr, NEW.block_number, NEW.block_number, 1, 0)
  ON CONFLICT(address) DO UPDATE SET
    last_seen_block = MAX(last_seen_block, NEW.block_number),
    tx_out = tx_out + 1,
    updated_at = (strftime('%Y-%m-%dT%H:%M:%fZ','now'));
END;

CREATE TRIGGER IF NOT EXISTS trg_addresses_to_ins
AFTER INSERT ON txs
WHEN NEW.to_addr IS NOT NULL
BEGIN
  INSERT INTO addresses(address, first_seen_block, last_seen_block, tx_out, tx_in)
  VALUES (NEW.to_addr, NEW.block_number, NEW.block_number, 0, 1)
  ON CONFLICT(address) DO UPDATE SET
    last_seen_block = MAX(last_seen_block, NEW.block_number),
    tx_in = tx_in + 1,
    updated_at = (strftime('%Y-%m-%dT%H:%M:%fZ','now'));
END;

-- -------------- Checkpoints / Cursors -------------------------------------
-- Generic key-value for incremental ingestion (e.g., last scanned block).
CREATE TABLE IF NOT EXISTS cursors (
  name          TEXT PRIMARY KEY,            -- e.g., 'ingest_blocks', 'ingest_logs'
  value         TEXT NOT NULL,               -- arbitrary string/number
  updated_at    DATETIME NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
) STRICT WITHOUT ROWID;

-- -------------- Views ------------------------------------------------------
-- Lightweight block overview.
CREATE VIEW IF NOT EXISTS v_blocks_basic AS
SELECT
  number,
  hash,
  parent_hash,
  timestamp,
  miner,
  tx_count,
  gamma,
  psi,
  participation
FROM blocks
ORDER BY number DESC;

-- Recent producer share (over last 10,000 blocks by default; tweak in queries).
CREATE VIEW IF NOT EXISTS v_producer_counts AS
SELECT miner AS producer, COUNT(*) AS blocks
FROM blocks
GROUP BY miner
ORDER BY blocks DESC;

-- Join transactions to blocks for convenience.
CREATE VIEW IF NOT EXISTS v_txs_enriched AS
SELECT
  t.hash,
  t.block_number,
  b.timestamp AS block_timestamp,
  t.tx_index,
  t.from_addr,
  t.to_addr,
  t.value,
  t.fee_paid,
  t.method_sig,
  t.status
FROM txs t
JOIN blocks b ON b.number = t.block_number;

-- -------------- Seed values ------------------------------------------------
INSERT OR IGNORE INTO chain_tips(name, number, hash)
VALUES ('canonical', -1, '0x00');

-- End of schema
