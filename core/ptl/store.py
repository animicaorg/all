"""PTL persistent storage using SQLite."""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Iterable, Optional

from core.ptl.model import PtlEntry, ReplicationReceipt, TxStatus

log = logging.getLogger("animica.ptl.store")


class PtlStore:
    """Durable storage for the Pending Transaction Ledger."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: Optional[sqlite3.Connection] = None
        self._init_db()

    def _init_db(self) -> None:
        """Initialize database schema."""
        conn = self._get_conn()
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS ptl_transactions (
                txid BLOB PRIMARY KEY,
                tx_bytes BLOB NOT NULL,
                status TEXT NOT NULL,
                received_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                origin TEXT NOT NULL,
                fee INTEGER NOT NULL DEFAULT 0,
                size INTEGER NOT NULL DEFAULT 0,
                nonce INTEGER,
                sender BLOB,
                reject_reason TEXT,
                included_height INTEGER,
                finalized_height INTEGER,
                expire_at REAL
            );

            CREATE INDEX IF NOT EXISTS idx_status ON ptl_transactions(status);
            CREATE INDEX IF NOT EXISTS idx_received_at ON ptl_transactions(received_at);
            CREATE INDEX IF NOT EXISTS idx_updated_at ON ptl_transactions(updated_at);
            CREATE INDEX IF NOT EXISTS idx_expire_at ON ptl_transactions(expire_at);
            CREATE INDEX IF NOT EXISTS idx_sender_nonce ON ptl_transactions(sender, nonce);

            CREATE TABLE IF NOT EXISTS ptl_receipts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                txid BLOB NOT NULL,
                peer_id TEXT NOT NULL,
                conn_id TEXT,
                timestamp REAL NOT NULL,
                first_seen_ts REAL NOT NULL,
                last_update_ts REAL NOT NULL,
                status TEXT NOT NULL,
                reason TEXT,
                FOREIGN KEY (txid) REFERENCES ptl_transactions(txid) ON DELETE CASCADE,
                UNIQUE(txid, peer_id, conn_id)
            );

            CREATE INDEX IF NOT EXISTS idx_receipts_txid ON ptl_receipts(txid);
            CREATE INDEX IF NOT EXISTS idx_receipts_peer ON ptl_receipts(peer_id);
            
            CREATE TABLE IF NOT EXISTS ptl_quarantine (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                quarantine_ts REAL NOT NULL,
                reason TEXT NOT NULL,
                data TEXT
            );
            """
        )
        conn.commit()
        receipt_count = conn.execute("SELECT COUNT(*) FROM ptl_receipts").fetchone()[0]
        tx_count = conn.execute("SELECT COUNT(*) FROM ptl_transactions").fetchone()[0]
        log.info("PTL store initialized", extra={
            "db_path": str(self.db_path),
            "transactions": tx_count,
            "receipts": receipt_count,
        })

    def _get_conn(self) -> sqlite3.Connection:
        """Get database connection."""
        if self._conn is None:
            self._conn = sqlite3.connect(str(self.db_path))
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def close(self) -> None:
        """Close database connection."""
        if self._conn:
            self._conn.close()
            self._conn = None

    def add(self, entry: PtlEntry) -> None:
        """Add a new transaction entry."""
        conn = self._get_conn()
        conn.execute(
            """
            INSERT OR REPLACE INTO ptl_transactions
            (txid, tx_bytes, status, received_at, updated_at, origin, fee, size,
             nonce, sender, reject_reason, included_height, finalized_height, expire_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                entry.txid,
                entry.tx_bytes,
                entry.status.value,
                entry.received_at,
                entry.updated_at,
                entry.origin,
                entry.fee,
                entry.size,
                entry.nonce,
                entry.sender,
                entry.reject_reason,
                entry.included_height,
                entry.finalized_height,
                entry.expire_at,
            ),
        )
        conn.commit()
        log.debug("PTL entry added", extra={"txid": entry.txid.hex()[:16]})

    def get(self, txid: bytes) -> Optional[PtlEntry]:
        """Get transaction by ID."""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM ptl_transactions WHERE txid = ?", (txid,)
        ).fetchone()
        if not row:
            return None

        receipts = self._get_receipts(txid)
        return self._row_to_entry(row, receipts)

    def update_status(
        self, txid: bytes, status: TxStatus, updated_at: float, **kwargs
    ) -> bool:
        """Update transaction status and optional fields."""
        conn = self._get_conn()
        updates = ["status = ?", "updated_at = ?"]
        values: list = [status.value, updated_at]

        for key, value in kwargs.items():
            if key in {
                "reject_reason",
                "included_height",
                "finalized_height",
                "expire_at",
            }:
                updates.append(f"{key} = ?")
                values.append(value)

        values.append(txid)
        query = f"UPDATE ptl_transactions SET {', '.join(updates)} WHERE txid = ?"
        cursor = conn.execute(query, values)
        conn.commit()
        return cursor.rowcount > 0

    def add_receipt(self, receipt: ReplicationReceipt, conn_id: Optional[str] = None) -> None:
        """Add a replication receipt with deduplication.
        
        If a receipt from the same peer (and optional conn_id) already exists,
        updates the existing receipt instead of creating a duplicate.
        """
        conn = self._get_conn()
        now = time.time()
        
        try:
            # Try to insert new receipt
            conn.execute(
                """
                INSERT INTO ptl_receipts 
                (txid, peer_id, conn_id, timestamp, first_seen_ts, last_update_ts, status, reason)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(txid, peer_id, conn_id) 
                DO UPDATE SET 
                    timestamp = excluded.timestamp,
                    last_update_ts = excluded.last_update_ts,
                    status = excluded.status,
                    reason = excluded.reason
                """,
                (
                    receipt.txid,
                    receipt.peer_id,
                    conn_id,
                    receipt.timestamp,
                    now,
                    now,
                    receipt.status,
                    receipt.reason,
                ),
            )
            conn.commit()
            log.debug("PTL receipt added", extra={
                "txid": receipt.txid.hex()[:16],
                "peer": receipt.peer_id[:16],
                "status": receipt.status,
            })
        except sqlite3.Error as e:
            log.warning("Failed to add receipt, quarantining", extra={
                "error": str(e),
                "txid": receipt.txid.hex()[:16],
            })
            self._quarantine_receipt(receipt, str(e))
    
    def _quarantine_receipt(self, receipt: ReplicationReceipt, reason: str) -> None:
        """Quarantine a corrupted receipt for later analysis."""
        conn = self._get_conn()
        try:
            data = json.dumps({
                "txid": receipt.txid.hex(),
                "peer_id": receipt.peer_id,
                "timestamp": receipt.timestamp,
                "status": receipt.status,
                "reason": receipt.reason,
            })
            conn.execute(
                "INSERT INTO ptl_quarantine (quarantine_ts, reason, data) VALUES (?, ?, ?)",
                (time.time(), reason, data),
            )
            conn.commit()
        except Exception as e:
            log.error("Failed to quarantine receipt", exc_info=e)

    def _get_receipts(self, txid: bytes) -> list[ReplicationReceipt]:
        """Get all receipts for a transaction."""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM ptl_receipts WHERE txid = ? ORDER BY first_seen_ts",
            (txid,),
        ).fetchall()
        return [
            ReplicationReceipt(
                peer_id=row["peer_id"],
                txid=txid,
                timestamp=row["timestamp"],
                status=row["status"],
                reason=row["reason"],
            )
            for row in rows
        ]
    
    def compact_receipts(self, older_than: float) -> int:
        """Compact old receipts for finalized transactions.
        
        Removes old receipts for transactions that have been finalized
        to keep the database size manageable. Only the most recent
        receipt per peer is kept for finalized transactions.
        """
        conn = self._get_conn()
        # Delete old receipts for finalized transactions (keep latest per peer)
        cursor = conn.execute(
            """
            DELETE FROM ptl_receipts
            WHERE txid IN (
                SELECT txid FROM ptl_transactions 
                WHERE status = ? AND finalized_height IS NOT NULL
            )
            AND last_update_ts < ?
            AND id NOT IN (
                SELECT MAX(id) FROM ptl_receipts
                WHERE txid IN (
                    SELECT txid FROM ptl_transactions 
                    WHERE status = ? AND finalized_height IS NOT NULL
                )
                GROUP BY txid, peer_id
            )
            """,
            (TxStatus.FINALIZED.value, older_than, TxStatus.FINALIZED.value),
        )
        conn.commit()
        return cursor.rowcount

    def list_by_status(
        self, status: TxStatus, limit: int = 100, offset: int = 0
    ) -> list[PtlEntry]:
        """List transactions by status."""
        conn = self._get_conn()
        rows = conn.execute(
            """
            SELECT * FROM ptl_transactions
            WHERE status = ?
            ORDER BY received_at DESC
            LIMIT ? OFFSET ?
            """,
            (status.value, limit, offset),
        ).fetchall()
        return [self._row_to_entry(row, self._get_receipts(row["txid"])) for row in rows]

    def list_pending(self, limit: int = 100) -> list[PtlEntry]:
        """List all pending (non-terminal) transactions."""
        conn = self._get_conn()
        terminal = [
            TxStatus.INCLUDED.value,
            TxStatus.FINALIZED.value,
            TxStatus.REJECTED.value,
            TxStatus.EXPIRED.value,
        ]
        placeholders = ",".join("?" * len(terminal))
        rows = conn.execute(
            f"""
            SELECT * FROM ptl_transactions
            WHERE status NOT IN ({placeholders})
            ORDER BY fee DESC, received_at ASC
            LIMIT ?
            """,
            (*terminal, limit),
        ).fetchall()
        return [self._row_to_entry(row, self._get_receipts(row["txid"])) for row in rows]

    def list_for_mining(self, limit: int = 1000) -> list[PtlEntry]:
        """List transactions ready for mining (ATTESTED or better)."""
        conn = self._get_conn()
        rows = conn.execute(
            """
            SELECT * FROM ptl_transactions
            WHERE status IN (?, ?, ?)
            ORDER BY fee DESC, size ASC, received_at ASC
            LIMIT ?
            """,
            (
                TxStatus.ATTESTED.value,
                TxStatus.REPLICATING.value,
                TxStatus.ANNOUNCED.value,
                limit,
            ),
        ).fetchall()
        return [self._row_to_entry(row, self._get_receipts(row["txid"])) for row in rows]

    def mark_expired(self, now: float) -> int:
        """Mark transactions as expired based on expire_at."""
        conn = self._get_conn()
        cursor = conn.execute(
            """
            UPDATE ptl_transactions
            SET status = ?, updated_at = ?
            WHERE expire_at IS NOT NULL AND expire_at <= ?
              AND status NOT IN (?, ?, ?)
            """,
            (
                TxStatus.EXPIRED.value,
                now,
                now,
                TxStatus.INCLUDED.value,
                TxStatus.FINALIZED.value,
                TxStatus.REJECTED.value,
            ),
        )
        conn.commit()
        return cursor.rowcount

    def prune_terminal(self, older_than: float) -> int:
        """Prune terminal transactions older than timestamp."""
        conn = self._get_conn()
        cursor = conn.execute(
            """
            DELETE FROM ptl_transactions
            WHERE status IN (?, ?, ?, ?)
              AND updated_at < ?
            """,
            (
                TxStatus.INCLUDED.value,
                TxStatus.FINALIZED.value,
                TxStatus.REJECTED.value,
                TxStatus.EXPIRED.value,
                older_than,
            ),
        )
        conn.commit()
        return cursor.rowcount

    def get_stats(self) -> dict:
        """Get PTL statistics."""
        conn = self._get_conn()
        stats = {}
        for status in TxStatus:
            count = conn.execute(
                "SELECT COUNT(*) FROM ptl_transactions WHERE status = ?",
                (status.value,),
            ).fetchone()[0]
            stats[status.value] = count
        return stats

    def _row_to_entry(
        self, row: sqlite3.Row, receipts: list[ReplicationReceipt]
    ) -> PtlEntry:
        """Convert database row to PtlEntry."""
        return PtlEntry(
            txid=row["txid"],
            tx_bytes=row["tx_bytes"],
            status=TxStatus(row["status"]),
            received_at=row["received_at"],
            updated_at=row["updated_at"],
            origin=row["origin"],
            fee=row["fee"],
            size=row["size"],
            nonce=row["nonce"],
            sender=row["sender"],
            receipts=receipts,
            reject_reason=row["reject_reason"],
            included_height=row["included_height"],
            finalized_height=row["finalized_height"],
            expire_at=row["expire_at"],
        )


__all__ = ["PtlStore"]
