"""
Database connection and schema management for the mining pool.

Uses SQLite by default with support for migrations.
All monetary values stored as INTEGER (base units).
"""

from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Generator, Optional

from .models import BlockState, PayoutState


class PoolDatabase:
    """
    Mining pool database with SQLite backend.

    Handles connection management, schema creation, and migrations.
    """

    def __init__(self, db_path: str, *, logger: Optional[logging.Logger] = None) -> None:
        self._db_path = db_path
        self._log = logger or logging.getLogger("animica.pool.db")
        self._conn: Optional[sqlite3.Connection] = None

    def connect(self) -> None:
        """Open database connection and ensure schema exists."""
        if self._conn is not None:
            return

        # Ensure parent directory exists
        db_file = Path(self._db_path)
        if db_file.parent and not db_file.parent.exists():
            db_file.parent.mkdir(parents=True, exist_ok=True)

        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row  # Access columns by name
        self._log.info(f"Connected to pool database at {self._db_path}")

        # Enable foreign keys
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.commit()

        # Create schema if needed
        self._ensure_schema()

    def close(self) -> None:
        """Close database connection."""
        if self._conn:
            self._conn.close()
            self._conn = None
            self._log.info("Pool database connection closed")

    @contextmanager
    def transaction(self) -> Generator[sqlite3.Connection, None, None]:
        """Context manager for database transactions."""
        if not self._conn:
            raise RuntimeError("Database not connected")

        try:
            yield self._conn
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    def execute(self, query: str, params: tuple = ()) -> sqlite3.Cursor:
        """Execute a query and return cursor."""
        if not self._conn:
            raise RuntimeError("Database not connected")
        return self._conn.execute(query, params)

    def fetchone(self, query: str, params: tuple = ()) -> Optional[sqlite3.Row]:
        """Execute query and fetch one row."""
        cursor = self.execute(query, params)
        return cursor.fetchone()

    def fetchall(self, query: str, params: tuple = ()) -> list[sqlite3.Row]:
        """Execute query and fetch all rows."""
        cursor = self.execute(query, params)
        return cursor.fetchall()

    def commit(self) -> None:
        """Commit current transaction."""
        if self._conn:
            self._conn.commit()

    def _ensure_schema(self) -> None:
        """Create database schema if it doesn't exist."""
        # Check if migrations table exists
        cursor = self._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='_migrations'"
        )
        if not cursor.fetchone():
            self._log.info("Initializing pool database schema")
            self._create_initial_schema()
        else:
            # Run any pending migrations
            self._run_migrations()

    def _create_initial_schema(self) -> None:
        """Create initial database schema."""
        schema_sql = """
        -- Migrations tracking
        CREATE TABLE _migrations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        -- Miners
        CREATE TABLE miners (
            id TEXT PRIMARY KEY,  -- UUID as string
            payout_address TEXT NOT NULL UNIQUE,
            created_at TIMESTAMP NOT NULL,
            last_seen_at TIMESTAMP NOT NULL,
            settings_json TEXT
        );
        CREATE INDEX idx_miners_payout ON miners(payout_address);
        CREATE INDEX idx_miners_last_seen ON miners(last_seen_at);

        -- Workers
        CREATE TABLE workers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            miner_id TEXT NOT NULL,
            name TEXT NOT NULL,
            connected_at TIMESTAMP NOT NULL,
            last_seen_at TIMESTAMP NOT NULL,
            ip TEXT,
            user_agent TEXT,
            FOREIGN KEY (miner_id) REFERENCES miners(id) ON DELETE CASCADE
        );
        CREATE INDEX idx_workers_miner ON workers(miner_id);
        CREATE INDEX idx_workers_last_seen ON workers(last_seen_at);

        -- Shares
        CREATE TABLE shares (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            miner_id TEXT NOT NULL,
            worker_id INTEGER,
            height INTEGER NOT NULL,
            job_id TEXT NOT NULL,
            difficulty REAL NOT NULL,
            work INTEGER NOT NULL,  -- Integer work weight
            accepted INTEGER NOT NULL,  -- Boolean as 0/1
            reason TEXT,
            created_at TIMESTAMP NOT NULL,
            FOREIGN KEY (miner_id) REFERENCES miners(id) ON DELETE CASCADE,
            FOREIGN KEY (worker_id) REFERENCES workers(id) ON DELETE SET NULL
        );
        CREATE INDEX idx_shares_height ON shares(height);
        CREATE INDEX idx_shares_miner_time ON shares(miner_id, created_at);
        CREATE INDEX idx_shares_created ON shares(created_at);
        CREATE INDEX idx_shares_accepted ON shares(accepted);
        
        -- Note: Duplicate share prevention is handled in application layer
        -- via share_validator's in-memory cache for better performance

        -- Blocks
        CREATE TABLE blocks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            height INTEGER NOT NULL,
            hash TEXT NOT NULL UNIQUE,
            prev_hash TEXT NOT NULL,
            found_at TIMESTAMP NOT NULL,
            finder_miner_id TEXT NOT NULL,
            state TEXT NOT NULL,
            network_difficulty REAL NOT NULL,
            target TEXT NOT NULL,
            coinbase_value INTEGER NOT NULL,
            confirmations INTEGER DEFAULT 0,
            orphaned INTEGER DEFAULT 0,
            payout_txid TEXT,
            pplns_window_start_share_id INTEGER,
            pplns_window_end_share_id INTEGER,
            metadata_json TEXT,
            FOREIGN KEY (finder_miner_id) REFERENCES miners(id),
            FOREIGN KEY (pplns_window_start_share_id) REFERENCES shares(id),
            FOREIGN KEY (pplns_window_end_share_id) REFERENCES shares(id)
        );
        CREATE INDEX idx_blocks_height ON blocks(height);
        CREATE INDEX idx_blocks_state ON blocks(state);
        CREATE INDEX idx_blocks_found_at ON blocks(found_at);

        -- Balances
        CREATE TABLE balances (
            payout_address TEXT PRIMARY KEY,
            immature INTEGER DEFAULT 0,
            mature INTEGER DEFAULT 0,
            paid_total INTEGER DEFAULT 0,
            updated_at TIMESTAMP NOT NULL
        );

        -- Payouts
        CREATE TABLE payouts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TIMESTAMP NOT NULL,
            mode TEXT NOT NULL,
            state TEXT NOT NULL,
            txid TEXT,
            total_amount INTEGER NOT NULL,
            fee_amount INTEGER NOT NULL,
            metadata_json TEXT
        );
        CREATE INDEX idx_payouts_state ON payouts(state);
        CREATE INDEX idx_payouts_created ON payouts(created_at);

        -- Payout Items
        CREATE TABLE payout_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            payout_id INTEGER NOT NULL,
            payout_address TEXT NOT NULL,
            amount INTEGER NOT NULL,
            block_id INTEGER,
            details_json TEXT,
            FOREIGN KEY (payout_id) REFERENCES payouts(id) ON DELETE CASCADE,
            FOREIGN KEY (block_id) REFERENCES blocks(id) ON DELETE SET NULL
        );
        CREATE INDEX idx_payout_items_payout ON payout_items(payout_id);
        CREATE INDEX idx_payout_items_address ON payout_items(payout_address);
        """

        self._conn.executescript(schema_sql)
        self._conn.commit()

        # Mark initial migration as applied
        self._conn.execute(
            "INSERT INTO _migrations (name) VALUES (?)", ("initial_schema",)
        )
        self._conn.commit()

        self._log.info("Pool database schema created successfully")

    def _run_migrations(self) -> None:
        """Run any pending migrations."""
        # Get list of applied migrations
        cursor = self._conn.execute("SELECT name FROM _migrations")
        applied = {row[0] for row in cursor.fetchall()}

        # Define migrations
        migrations = [
            # ("migration_name", "SQL or function"),
        ]

        for name, migration_sql in migrations:
            if name not in applied:
                self._log.info(f"Applying migration: {name}")
                self._conn.executescript(migration_sql)
                self._conn.execute("INSERT INTO _migrations (name) VALUES (?)", (name,))
                self._conn.commit()

    def get_schema_version(self) -> int:
        """Get current schema version (number of applied migrations)."""
        if not self._conn:
            return 0

        cursor = self._conn.execute("SELECT COUNT(*) FROM _migrations")
        row = cursor.fetchone()
        return row[0] if row else 0
