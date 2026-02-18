"""
Database layer for ENA service.

Tracks:
- Used transaction hashes (replay protection)
- Credit balances (deposit mode)
- Request history (audit logs)
"""

import logging
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, List, Dict, Any

logger = logging.getLogger(__name__)


@dataclass
class UsedTransaction:
    """Record of a used transaction."""
    tx_hash: str
    payer: str
    amount: int
    used_at: float
    request_id: str


@dataclass
class CreditBalance:
    """Credit balance for an address."""
    address: str
    balance: int
    updated_at: float


@dataclass
class RequestLog:
    """Request log entry."""
    request_id: str
    payer: str
    model: str
    mode: str
    tx_hash: Optional[str]
    amount_paid: int
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    timestamp: float
    success: bool
    error: Optional[str]


class Database:
    """Database for ENA service."""
    
    def __init__(self, db_path: str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
    
    def _init_db(self):
        """Initialize database schema."""
        with self._connect() as conn:
            cursor = conn.cursor()
            
            # Used transactions table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS used_transactions (
                    tx_hash TEXT PRIMARY KEY,
                    payer TEXT NOT NULL,
                    amount INTEGER NOT NULL,
                    used_at REAL NOT NULL,
                    request_id TEXT NOT NULL
                )
            """)
            
            # Create index on payer for efficient lookups
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_used_tx_payer 
                ON used_transactions(payer)
            """)
            
            # Credit balances table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS credit_balances (
                    address TEXT PRIMARY KEY,
                    balance INTEGER NOT NULL,
                    updated_at REAL NOT NULL
                )
            """)
            
            # Request logs table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS request_logs (
                    request_id TEXT PRIMARY KEY,
                    payer TEXT NOT NULL,
                    model TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    tx_hash TEXT,
                    amount_paid INTEGER NOT NULL,
                    prompt_tokens INTEGER NOT NULL,
                    completion_tokens INTEGER NOT NULL,
                    total_tokens INTEGER NOT NULL,
                    timestamp REAL NOT NULL,
                    success INTEGER NOT NULL,
                    error TEXT
                )
            """)
            
            # Create index on payer and timestamp
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_request_logs_payer 
                ON request_logs(payer, timestamp)
            """)
            
            conn.commit()
    
    @contextmanager
    def _connect(self):
        """Context manager for database connections."""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()
    
    def is_transaction_used(self, tx_hash: str) -> bool:
        """Check if a transaction hash has been used."""
        with self._connect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT 1 FROM used_transactions WHERE tx_hash = ?",
                (tx_hash,)
            )
            return cursor.fetchone() is not None
    
    def mark_transaction_used(
        self,
        tx_hash: str,
        payer: str,
        amount: int,
        request_id: str,
    ):
        """Mark a transaction as used."""
        with self._connect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO used_transactions 
                (tx_hash, payer, amount, used_at, request_id)
                VALUES (?, ?, ?, ?, ?)
                """,
                (tx_hash, payer, amount, time.time(), request_id)
            )
            conn.commit()
    
    def get_credit_balance(self, address: str) -> int:
        """Get credit balance for an address."""
        with self._connect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT balance FROM credit_balances WHERE address = ?",
                (address,)
            )
            row = cursor.fetchone()
            return row["balance"] if row else 0
    
    def add_credits(self, address: str, amount: int):
        """Add credits to an address."""
        with self._connect() as conn:
            cursor = conn.cursor()
            
            # Get current balance
            cursor.execute(
                "SELECT balance FROM credit_balances WHERE address = ?",
                (address,)
            )
            row = cursor.fetchone()
            
            if row:
                # Update existing balance
                new_balance = row["balance"] + amount
                cursor.execute(
                    """
                    UPDATE credit_balances 
                    SET balance = ?, updated_at = ?
                    WHERE address = ?
                    """,
                    (new_balance, time.time(), address)
                )
            else:
                # Insert new balance
                cursor.execute(
                    """
                    INSERT INTO credit_balances 
                    (address, balance, updated_at)
                    VALUES (?, ?, ?)
                    """,
                    (address, amount, time.time())
                )
            
            conn.commit()
    
    def deduct_credits(self, address: str, amount: int) -> bool:
        """
        Deduct credits from an address.
        
        Returns:
            True if successful, False if insufficient balance
        """
        with self._connect() as conn:
            cursor = conn.cursor()
            
            # Get current balance
            cursor.execute(
                "SELECT balance FROM credit_balances WHERE address = ?",
                (address,)
            )
            row = cursor.fetchone()
            
            if not row or row["balance"] < amount:
                return False
            
            # Update balance
            new_balance = row["balance"] - amount
            cursor.execute(
                """
                UPDATE credit_balances 
                SET balance = ?, updated_at = ?
                WHERE address = ?
                """,
                (new_balance, time.time(), address)
            )
            conn.commit()
            return True
    
    def log_request(
        self,
        request_id: str,
        payer: str,
        model: str,
        mode: str,
        tx_hash: Optional[str],
        amount_paid: int,
        prompt_tokens: int,
        completion_tokens: int,
        total_tokens: int,
        success: bool,
        error: Optional[str] = None,
    ):
        """Log a request."""
        with self._connect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO request_logs 
                (request_id, payer, model, mode, tx_hash, amount_paid,
                 prompt_tokens, completion_tokens, total_tokens,
                 timestamp, success, error)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    request_id, payer, model, mode, tx_hash, amount_paid,
                    prompt_tokens, completion_tokens, total_tokens,
                    time.time(), 1 if success else 0, error
                )
            )
            conn.commit()
    
    def get_request_history(
        self,
        payer: Optional[str] = None,
        limit: int = 100,
    ) -> List[RequestLog]:
        """Get request history."""
        with self._connect() as conn:
            cursor = conn.cursor()
            
            if payer:
                cursor.execute(
                    """
                    SELECT * FROM request_logs 
                    WHERE payer = ?
                    ORDER BY timestamp DESC
                    LIMIT ?
                    """,
                    (payer, limit)
                )
            else:
                cursor.execute(
                    """
                    SELECT * FROM request_logs 
                    ORDER BY timestamp DESC
                    LIMIT ?
                    """,
                    (limit,)
                )
            
            rows = cursor.fetchall()
            return [
                RequestLog(
                    request_id=row["request_id"],
                    payer=row["payer"],
                    model=row["model"],
                    mode=row["mode"],
                    tx_hash=row["tx_hash"],
                    amount_paid=row["amount_paid"],
                    prompt_tokens=row["prompt_tokens"],
                    completion_tokens=row["completion_tokens"],
                    total_tokens=row["total_tokens"],
                    timestamp=row["timestamp"],
                    success=bool(row["success"]),
                    error=row["error"],
                )
                for row in rows
            ]
