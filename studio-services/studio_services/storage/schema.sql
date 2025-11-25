-- Animica Studio Services — SQLite schema
-- Idempotent DDL for: artifacts, verifications, queue, rate_counters
PRAGMA foreign_keys = ON;

-- -----------------------------------------------------------------------------
-- artifacts: content-addressed, write-once blobs (e.g., manifests, ABIs, code)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS artifacts (
    content_hash     TEXT PRIMARY KEY,                         -- 0x… lowercase hex
    size             INTEGER NOT NULL CHECK (size >= 0),
    mime             TEXT,                                     -- optional MIME type
    filename         TEXT,                                     -- optional original filename hint
    storage_backend  TEXT NOT NULL DEFAULT 'fs',               -- 'fs' | 's3' | etc.
    storage_locator  TEXT NOT NULL,                            -- path or object key
    created_at       INTEGER NOT NULL DEFAULT (strftime('%s','now'))
);
CREATE INDEX IF NOT EXISTS idx_artifacts_created_at ON artifacts(created_at);

-- -----------------------------------------------------------------------------
-- verifications: source/manifest recompile & match results for contracts
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS verifications (
    id              TEXT PRIMARY KEY,                          -- deterministic job id
    address         TEXT NOT NULL,                             -- bech32m or hex (validated at API)
    code_hash       TEXT NOT NULL,                             -- expected 0x… hash
    manifest_hash   TEXT,                                      -- 0x… hash of manifest (if provided)
    source_artifact TEXT,                                      -- FK → artifacts.content_hash
    status          TEXT NOT NULL CHECK (
                        status IN ('queued','running','ok','error')
                     ),
    reason          TEXT,                                      -- error/details (nullable)
    tx_hash         TEXT,                                      -- optional 0x… deployment tx
    created_at      INTEGER NOT NULL DEFAULT (strftime('%s','now')),
    updated_at      INTEGER NOT NULL DEFAULT (strftime('%s','now')),
    FOREIGN KEY (source_artifact) REFERENCES artifacts(content_hash) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_verif_addr       ON verifications(address);
CREATE INDEX IF NOT EXISTS idx_verif_status     ON verifications(status);
CREATE INDEX IF NOT EXISTS idx_verif_code_hash  ON verifications(code_hash);

-- Keep updated_at fresh on any update
CREATE TRIGGER IF NOT EXISTS verifications_mtime
AFTER UPDATE ON verifications
FOR EACH ROW
BEGIN
  UPDATE verifications SET updated_at = (strftime('%s','now')) WHERE id = OLD.id;
END;

-- -----------------------------------------------------------------------------
-- queue: generic FIFO with leases & priorities (used by verify worker, etc.)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS queue (
    id               TEXT PRIMARY KEY,                         -- deterministic job id
    topic            TEXT NOT NULL,                            -- e.g., 'verify'
    state            TEXT NOT NULL CHECK (
                        state IN ('queued','leased','done','error')
                     ),
    payload          BLOB NOT NULL,                            -- msgspec/json payload
    priority         INTEGER NOT NULL DEFAULT 0,               -- higher first
    retry            INTEGER NOT NULL DEFAULT 0,               -- attempt counter
    lease_owner      TEXT,                                     -- worker id
    lease_expires_at INTEGER,                                  -- unix seconds
    created_at       INTEGER NOT NULL DEFAULT (strftime('%s','now')),
    updated_at       INTEGER NOT NULL DEFAULT (strftime('%s','now'))
);
CREATE INDEX IF NOT EXISTS idx_queue_state_topic_prio
    ON queue(state, topic, priority DESC, created_at ASC);
CREATE INDEX IF NOT EXISTS idx_queue_lease
    ON queue(state, lease_expires_at);
CREATE INDEX IF NOT EXISTS idx_queue_topic
    ON queue(topic);

CREATE TRIGGER IF NOT EXISTS queue_mtime
AFTER UPDATE ON queue
FOR EACH ROW
BEGIN
  UPDATE queue SET updated_at = (strftime('%s','now')) WHERE id = OLD.id;
END;

-- -----------------------------------------------------------------------------
-- rate_counters: sliding window counters for per-key/per-route rate limits
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS rate_counters (
    key           TEXT NOT NULL,                               -- API key or IP bucket
    route         TEXT NOT NULL,                               -- route/method identifier
    window_start  INTEGER NOT NULL,                            -- window start (unix seconds)
    count         INTEGER NOT NULL DEFAULT 0 CHECK (count >= 0),
    PRIMARY KEY (key, route, window_start)
);
CREATE INDEX IF NOT EXISTS idx_rate_counters_window ON rate_counters(window_start);
