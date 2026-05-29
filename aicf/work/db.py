"""
aicf.work.db
------------

SQLite storage for the work layer. Mirrors the TypeScript Prisma schema
(``apps/chat-animica/prisma/schema.prisma``) row-for-row. UNIQUE indexes
are the load-bearing pieces of the design:

  - ``UNIQUE(idempotency_key)`` on work_job → create-job idempotency
  - ``UNIQUE(result_id)``       on work_payout → no double payout
  - ``UNIQUE(job_id, worker_id, result_hash)`` on work_result → submit idempotency
  - ``UNIQUE(wallet_address, machine_id)`` on work_worker → upsert by identity

These constraints are the truth source; the service layer relies on
SQLite raising IntegrityError to short-circuit duplicate operations.

Path resolution follows ``aicf.db.default_base_dir()`` so it slots into
the same directory tree as the rest of AICF (``~/.animica/aicf/`` by
default; ``$AICF_DB_DIR`` overrides). Tests pass ``":memory:"`` directly.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Iterable, Union

from aicf.db import default_base_dir

PathLike = Union[str, Path]


SCHEMA_SQL = """
PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;
PRAGMA user_version = 1;

BEGIN TRANSACTION;

CREATE TABLE IF NOT EXISTS work_job (
    id                    TEXT PRIMARY KEY,
    idempotency_key       TEXT UNIQUE,
    title                 TEXT NOT NULL,
    description           TEXT,
    prompt                TEXT NOT NULL,
    job_type              TEXT NOT NULL,
    status                TEXT NOT NULL DEFAULT 'submitted',
    creator_user_id       TEXT,
    creator_wallet        TEXT,
    reward_amount_anm     TEXT NOT NULL,        -- decimal string
    required_capabilities TEXT NOT NULL DEFAULT '[]',  -- JSON array
    priority              INTEGER NOT NULL DEFAULT 0,
    max_workers           INTEGER NOT NULL DEFAULT 1,
    verification_mode     TEXT NOT NULL DEFAULT 'user_acceptance',
    result_visibility     TEXT NOT NULL DEFAULT 'private',
    metadata_json         TEXT,
    created_at            INTEGER NOT NULL,    -- unix epoch ms
    updated_at            INTEGER NOT NULL,
    expires_at            INTEGER
);
CREATE INDEX IF NOT EXISTS idx_work_job_status      ON work_job(status);
CREATE INDEX IF NOT EXISTS idx_work_job_jobtype     ON work_job(job_type);
CREATE INDEX IF NOT EXISTS idx_work_job_creator     ON work_job(creator_wallet);
CREATE INDEX IF NOT EXISTS idx_work_job_priority    ON work_job(priority DESC, created_at);

CREATE TABLE IF NOT EXISTS work_task (
    id                    TEXT PRIMARY KEY,
    job_id                TEXT NOT NULL REFERENCES work_job(id) ON DELETE CASCADE,
    title                 TEXT NOT NULL,
    description           TEXT,
    task_type             TEXT NOT NULL,
    status                TEXT NOT NULL DEFAULT 'open',
    depends_on_task_ids   TEXT NOT NULL DEFAULT '[]',
    assigned_worker_id    TEXT,
    required_capabilities TEXT NOT NULL DEFAULT '[]',
    reward_amount_anm     TEXT NOT NULL,
    result_id             TEXT,
    created_at            INTEGER NOT NULL,
    updated_at            INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_work_task_job        ON work_task(job_id);
CREATE INDEX IF NOT EXISTS idx_work_task_status     ON work_task(status);

CREATE TABLE IF NOT EXISTS work_worker (
    id                  TEXT PRIMARY KEY,
    wallet_address      TEXT NOT NULL,
    display_name        TEXT,
    machine_id          TEXT NOT NULL,
    device_type         TEXT NOT NULL DEFAULT 'cpu',
    hardware_summary    TEXT,
    capabilities        TEXT NOT NULL DEFAULT '[]',
    status              TEXT NOT NULL DEFAULT 'idle',
    reputation_score    INTEGER NOT NULL DEFAULT 0,
    completed_jobs      INTEGER NOT NULL DEFAULT 0,
    failed_jobs         INTEGER NOT NULL DEFAULT 0,
    total_earned_anm    TEXT NOT NULL DEFAULT '0',
    last_seen_at        INTEGER NOT NULL,
    created_at          INTEGER NOT NULL,
    UNIQUE(wallet_address, machine_id)
);
CREATE INDEX IF NOT EXISTS idx_work_worker_status   ON work_worker(status);
CREATE INDEX IF NOT EXISTS idx_work_worker_wallet   ON work_worker(wallet_address);

CREATE TABLE IF NOT EXISTS work_claim (
    id                TEXT PRIMARY KEY,
    job_id            TEXT NOT NULL REFERENCES work_job(id) ON DELETE CASCADE,
    task_id           TEXT REFERENCES work_task(id) ON DELETE SET NULL,
    worker_id         TEXT NOT NULL REFERENCES work_worker(id),
    status            TEXT NOT NULL DEFAULT 'active',
    claimed_at        INTEGER NOT NULL,
    heartbeat_at      INTEGER NOT NULL,
    lease_expires_at  INTEGER NOT NULL,
    released_at       INTEGER
);
CREATE INDEX IF NOT EXISTS idx_work_claim_job       ON work_claim(job_id);
CREATE INDEX IF NOT EXISTS idx_work_claim_task      ON work_claim(task_id);
CREATE INDEX IF NOT EXISTS idx_work_claim_worker    ON work_claim(worker_id);
CREATE INDEX IF NOT EXISTS idx_work_claim_active    ON work_claim(status, lease_expires_at);

CREATE TABLE IF NOT EXISTS work_result (
    id            TEXT PRIMARY KEY,
    job_id        TEXT NOT NULL REFERENCES work_job(id) ON DELETE CASCADE,
    task_id       TEXT REFERENCES work_task(id) ON DELETE SET NULL,
    worker_id     TEXT NOT NULL REFERENCES work_worker(id),
    output_text   TEXT,
    output_json   TEXT,
    artifact_urls TEXT NOT NULL DEFAULT '[]',
    result_hash   TEXT NOT NULL,
    logs_hash     TEXT,
    model_info    TEXT,
    runtime_info  TEXT,
    submitted_at  INTEGER NOT NULL,
    UNIQUE(job_id, worker_id, result_hash)
);
CREATE INDEX IF NOT EXISTS idx_work_result_job      ON work_result(job_id);
CREATE INDEX IF NOT EXISTS idx_work_result_task     ON work_result(task_id);

CREATE TABLE IF NOT EXISTS work_verification (
    id                  TEXT PRIMARY KEY,
    job_id              TEXT NOT NULL REFERENCES work_job(id) ON DELETE CASCADE,
    task_id             TEXT REFERENCES work_task(id) ON DELETE SET NULL,
    result_id           TEXT NOT NULL REFERENCES work_result(id) ON DELETE CASCADE,
    verifier_worker_id  TEXT REFERENCES work_worker(id),
    mode                TEXT NOT NULL,
    score               REAL,
    verdict             TEXT NOT NULL,
    notes               TEXT,
    test_output         TEXT,
    created_at          INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_work_verif_result    ON work_verification(result_id);
CREATE INDEX IF NOT EXISTS idx_work_verif_verdict   ON work_verification(verdict);

CREATE TABLE IF NOT EXISTS work_payout (
    id              TEXT PRIMARY KEY,
    job_id          TEXT NOT NULL REFERENCES work_job(id) ON DELETE CASCADE,
    task_id         TEXT REFERENCES work_task(id) ON DELETE SET NULL,
    worker_id       TEXT NOT NULL REFERENCES work_worker(id),
    result_id       TEXT NOT NULL UNIQUE REFERENCES work_result(id) ON DELETE CASCADE,
    amount_anm      TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending',
    aicf_claim_id   TEXT,
    tx_hash         TEXT,
    explorer_url    TEXT,
    failure_reason  TEXT,
    created_at      INTEGER NOT NULL,
    paid_at         INTEGER
);
CREATE INDEX IF NOT EXISTS idx_work_payout_status   ON work_payout(status);
CREATE INDEX IF NOT EXISTS idx_work_payout_worker   ON work_payout(worker_id);

CREATE TABLE IF NOT EXISTS work_audit_log (
    id            TEXT PRIMARY KEY,
    actor_type    TEXT NOT NULL,
    actor_id      TEXT,
    action        TEXT NOT NULL,
    target_type   TEXT NOT NULL,
    target_id     TEXT NOT NULL,
    metadata_json TEXT,
    created_at    INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_work_audit_target    ON work_audit_log(target_type, target_id);
CREATE INDEX IF NOT EXISTS idx_work_audit_action    ON work_audit_log(action);

COMMIT;
"""


def default_db_path() -> Path:
    """Return the default on-disk path for the work-layer SQLite file."""
    base = default_base_dir()
    base.mkdir(parents=True, exist_ok=True)
    return base / "work.sqlite3"


def connect(path: PathLike | None = None) -> sqlite3.Connection:
    """Open (or create) a connection to the work-layer DB.

    Passes ``isolation_level=None`` so callers manage transactions
    explicitly with ``BEGIN`` / ``COMMIT`` — services use savepoints
    to keep the failure model simple.

    ``":memory:"`` is honored for tests. File paths trigger schema
    initialisation on first connect.
    """
    target = ":memory:" if path == ":memory:" else (str(path) if path else str(default_db_path()))
    conn = sqlite3.connect(target, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    # Foreign keys must be enabled per connection.
    conn.execute("PRAGMA foreign_keys = ON")
    init_schema(conn)
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    """Idempotently create the schema. Safe to call on every connect."""
    conn.executescript(SCHEMA_SQL)


def list_tables(conn: sqlite3.Connection) -> list[str]:
    """Used by tests + the operator dashboard to verify migration."""
    return [
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'work_%' ORDER BY name"
        )
    ]


__all__ = ["connect", "init_schema", "default_db_path", "list_tables", "SCHEMA_SQL"]
