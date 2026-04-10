"""
mempool2.storage - Persistent SQLite Storage
============================================

Crash-safe transaction storage with efficient indexing and querying.
Uses WAL mode for durability and concurrent reads.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from pathlib import Path
from typing import Iterator, Optional

from coretx import TxEnvelope, TxId
from coretx.canonical import decode_tx_envelope, encode_tx_envelope

from .types import FeeStats, MempoolEntry, MempoolStats, TxSource

__all__ = ["MempoolStorage"]

log = logging.getLogger(__name__)


class MempoolStorage:
    """
    Persistent mempool storage backed by SQLite.
    
    Features:
    - WAL mode for crash safety
    - Efficient indexes for common queries
    - Transaction iteration by fee rate
    - Atomic operations
    """
    
    SCHEMA = """
    CREATE TABLE IF NOT EXISTS transactions (
        txid BLOB PRIMARY KEY,
        envelope_bytes BLOB NOT NULL,
        arrival_time REAL NOT NULL,
        fee_rate INTEGER NOT NULL,
        sender BLOB NOT NULL,
        nonce INTEGER NOT NULL,
        source TEXT NOT NULL,
        peer_id TEXT,
        gas_limit INTEGER NOT NULL,
        fee INTEGER NOT NULL,
        value INTEGER NOT NULL
    );
    
    CREATE INDEX IF NOT EXISTS idx_sender ON transactions(sender, nonce);
    CREATE INDEX IF NOT EXISTS idx_fee_rate ON transactions(fee_rate DESC, arrival_time ASC);
    CREATE INDEX IF NOT EXISTS idx_arrival ON transactions(arrival_time ASC);
    CREATE INDEX IF NOT EXISTS idx_nonce ON transactions(nonce);
    """
    
    def __init__(self, db_path: str | Path):
        """
        Initialize storage.
        
        Args:
            db_path: Path to SQLite database file
        """
        self.db_path = Path(db_path)
        self.conn: Optional[sqlite3.Connection] = None
        self._open()
    
    def _open(self) -> None:
        """Open database connection and initialize schema"""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        
        self.conn = sqlite3.connect(
            str(self.db_path),
            isolation_level=None,  # autocommit mode
            check_same_thread=False,  # allow multi-thread access
        )
        
        # Enable WAL mode for better concurrency and durability
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")  # Fast but safe
        self.conn.execute("PRAGMA cache_size=-64000")  # 64MB cache
        
        # Create schema
        self.conn.executescript(self.SCHEMA)
        self.conn.commit()
        
        log.info(f"Opened mempool storage at {self.db_path}")
    
    def close(self) -> None:
        """Close database connection"""
        if self.conn:
            self.conn.close()
            self.conn = None
            log.info("Closed mempool storage")
    
    def add_tx(self, entry: MempoolEntry) -> bool:
        """
        Add transaction to storage.
        
        Args:
            entry: Mempool entry to add
            
        Returns:
            True if added, False if already exists
        """
        try:
            envelope_bytes = encode_tx_envelope(entry.envelope)
            
            self.conn.execute(
                """
                INSERT INTO transactions (
                    txid, envelope_bytes, arrival_time, fee_rate,
                    sender, nonce, source, peer_id,
                    gas_limit, fee, value
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry.txid.bytes32,
                    envelope_bytes,
                    entry.arrival_time,
                    entry.fee_rate,
                    entry.sender,
                    entry.nonce,
                    entry.source.value,
                    entry.peer_id,
                    entry.gas_limit,
                    entry.fee,
                    entry.envelope.body.value,
                ),
            )
            self.conn.commit()
            log.debug(f"Added tx {entry.txid.hex()} to storage")
            return True
            
        except sqlite3.IntegrityError:
            # Already exists
            log.debug(f"Tx {entry.txid.hex()} already in storage")
            return False
    
    def remove_tx(self, txid: TxId) -> bool:
        """
        Remove transaction from storage.
        
        Args:
            txid: Transaction ID to remove
            
        Returns:
            True if removed, False if not found
        """
        cursor = self.conn.execute(
            "DELETE FROM transactions WHERE txid = ?",
            (txid.bytes32,),
        )
        self.conn.commit()
        
        removed = cursor.rowcount > 0
        if removed:
            log.debug(f"Removed tx {txid.hex()} from storage")
        return removed
    
    def get_tx(self, txid: TxId) -> Optional[MempoolEntry]:
        """
        Retrieve transaction by ID.
        
        Args:
            txid: Transaction ID to retrieve
            
        Returns:
            MempoolEntry if found, None otherwise
        """
        row = self.conn.execute(
            """
            SELECT envelope_bytes, arrival_time, fee_rate, source, peer_id
            FROM transactions
            WHERE txid = ?
            """,
            (txid.bytes32,),
        ).fetchone()
        
        if not row:
            return None
        
        envelope_bytes, arrival_time, fee_rate, source_str, peer_id = row
        envelope = decode_tx_envelope(envelope_bytes)
        
        return MempoolEntry(
            envelope=envelope,
            arrival_time=arrival_time,
            fee_rate=fee_rate,
            source=TxSource(source_str),
            peer_id=peer_id,
        )
    
    def has_tx(self, txid: TxId) -> bool:
        """Check if transaction exists"""
        row = self.conn.execute(
            "SELECT 1 FROM transactions WHERE txid = ? LIMIT 1",
            (txid.bytes32,),
        ).fetchone()
        return row is not None
    
    def list_txs(self, limit: Optional[int] = None) -> list[MempoolEntry]:
        """
        List all transactions.
        
        Args:
            limit: Optional maximum number to return
            
        Returns:
            List of mempool entries
        """
        query = """
            SELECT envelope_bytes, arrival_time, fee_rate, source, peer_id
            FROM transactions
            ORDER BY fee_rate DESC, arrival_time ASC
        """
        
        if limit:
            query += f" LIMIT {limit}"
        
        rows = self.conn.execute(query).fetchall()
        
        entries = []
        for row in rows:
            envelope_bytes, arrival_time, fee_rate, source_str, peer_id = row
            envelope = decode_tx_envelope(envelope_bytes)
            entries.append(
                MempoolEntry(
                    envelope=envelope,
                    arrival_time=arrival_time,
                    fee_rate=fee_rate,
                    source=TxSource(source_str),
                    peer_id=peer_id,
                )
            )
        
        return entries
    
    def iter_by_fee(self, descending: bool = True) -> Iterator[MempoolEntry]:
        """
        Iterate transactions ordered by fee rate.
        
        Args:
            descending: If True, highest fee first. If False, lowest first.
            
        Yields:
            MempoolEntry in fee order
        """
        order = "DESC" if descending else "ASC"
        query = f"""
            SELECT envelope_bytes, arrival_time, fee_rate, source, peer_id
            FROM transactions
            ORDER BY fee_rate {order}, arrival_time ASC
        """
        
        cursor = self.conn.execute(query)
        
        for row in cursor:
            envelope_bytes, arrival_time, fee_rate, source_str, peer_id = row
            envelope = decode_tx_envelope(envelope_bytes)
            yield MempoolEntry(
                envelope=envelope,
                arrival_time=arrival_time,
                fee_rate=fee_rate,
                source=TxSource(source_str),
                peer_id=peer_id,
            )
    
    def get_sender_txs(self, sender: bytes) -> list[MempoolEntry]:
        """
        Get all transactions from a specific sender.
        
        Args:
            sender: Sender address (32 bytes)
            
        Returns:
            List of entries, sorted by nonce ascending
        """
        rows = self.conn.execute(
            """
            SELECT envelope_bytes, arrival_time, fee_rate, source, peer_id
            FROM transactions
            WHERE sender = ?
            ORDER BY nonce ASC
            """,
            (sender,),
        ).fetchall()
        
        entries = []
        for row in rows:
            envelope_bytes, arrival_time, fee_rate, source_str, peer_id = row
            envelope = decode_tx_envelope(envelope_bytes)
            entries.append(
                MempoolEntry(
                    envelope=envelope,
                    arrival_time=arrival_time,
                    fee_rate=fee_rate,
                    source=TxSource(source_str),
                    peer_id=peer_id,
                )
            )
        
        return entries
    
    def get_sender_nonces(self, sender: bytes) -> set[int]:
        """
        Get set of nonces for a sender.
        
        Args:
            sender: Sender address (32 bytes)
            
        Returns:
            Set of nonces
        """
        rows = self.conn.execute(
            "SELECT nonce FROM transactions WHERE sender = ?",
            (sender,),
        ).fetchall()
        
        return {row[0] for row in rows}
    
    def get_sender_pending_debits(self, sender: bytes) -> int:
        """
        Calculate total pending debits (value + fee) for a sender.
        
        Args:
            sender: Sender address (32 bytes)
            
        Returns:
            Total debits in wei
        """
        row = self.conn.execute(
            "SELECT SUM(value + fee) FROM transactions WHERE sender = ?",
            (sender,),
        ).fetchone()
        
        return row[0] or 0
    
    def get_stats(self) -> MempoolStats:
        """
        Calculate mempool statistics.
        
        Returns:
            MempoolStats snapshot
        """
        # Count and bytes
        row = self.conn.execute(
            "SELECT COUNT(*), SUM(LENGTH(envelope_bytes)) FROM transactions"
        ).fetchone()
        tx_count = row[0] or 0
        total_bytes = row[1] or 0
        
        # Unique senders
        row = self.conn.execute(
            "SELECT COUNT(DISTINCT sender) FROM transactions"
        ).fetchone()
        unique_senders = row[0] or 0
        
        # Fee stats
        fee_stats = FeeStats()
        if tx_count > 0:
            rows = self.conn.execute(
                """
                SELECT 
                    MIN(fee_rate), 
                    MAX(fee_rate), 
                    AVG(fee_rate)
                FROM transactions
                """
            ).fetchone()
            
            fee_stats.min_fee_rate = rows[0] or 0
            fee_stats.max_fee_rate = rows[1] or 0
            fee_stats.mean_fee_rate = int(rows[2] or 0)
            
            # Median (approximate)
            median_row = self.conn.execute(
                """
                SELECT fee_rate 
                FROM transactions 
                ORDER BY fee_rate 
                LIMIT 1 OFFSET ?
                """,
                (tx_count // 2,),
            ).fetchone()
            if median_row:
                fee_stats.median_fee_rate = median_row[0]
        
        return MempoolStats(
            tx_count=tx_count,
            total_bytes=total_bytes,
            unique_senders=unique_senders,
            fee_stats=fee_stats,
        )
    
    def clear(self) -> int:
        """
        Clear all transactions from storage.
        
        Returns:
            Number of transactions removed
        """
        cursor = self.conn.execute("DELETE FROM transactions")
        self.conn.commit()
        count = cursor.rowcount
        log.info(f"Cleared {count} transactions from storage")
        return count
    
    def __enter__(self):
        """Context manager entry"""
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit"""
        self.close()
