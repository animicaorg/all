"""
Database layer for ENA service.

Tracks:
- Used transaction hashes (replay protection)
- Credit balances (deposit mode)
- Request history (audit logs)
- Training jobs
- Published checkpoints
"""

import json
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


@dataclass
class TrainingJob:
    """Training job tracked by the ENA service."""

    job_id: str
    payer: str
    model: str
    plan: Dict[str, Any]
    budget: int
    spent: int
    status: str
    progress: int
    message: Optional[str]
    created_at: float
    updated_at: float
    checkpoint_version: Optional[str]
    output_dir: Optional[str]


@dataclass
class CheckpointRecord:
    """Checkpoint bundle tracked by the ENA service."""

    version: str
    job_id: str
    model: str
    epoch: int
    size_bytes: int
    path: str
    metadata: Dict[str, Any]
    published_at: float


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

            # Training jobs table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS training_jobs (
                    job_id TEXT PRIMARY KEY,
                    payer TEXT NOT NULL,
                    model TEXT NOT NULL,
                    plan_json TEXT NOT NULL,
                    budget INTEGER NOT NULL,
                    spent INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    progress INTEGER NOT NULL,
                    message TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    checkpoint_version TEXT,
                    output_dir TEXT
                )
            """)

            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_training_jobs_status
                ON training_jobs(status, created_at)
            """)

            # Checkpoints table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS checkpoints (
                    version TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL,
                    model TEXT NOT NULL,
                    epoch INTEGER NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    path TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    published_at REAL NOT NULL
                )
            """)

            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_checkpoints_model
                ON checkpoints(model, published_at)
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

    def create_training_job(
        self,
        job_id: str,
        payer: str,
        model: str,
        plan: Dict[str, Any],
        budget: int,
        status: str = "pending",
        progress: int = 0,
        message: Optional[str] = None,
        output_dir: Optional[str] = None,
    ) -> None:
        """Create a new tracked training job."""
        now = time.time()
        with self._connect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO training_jobs
                (job_id, payer, model, plan_json, budget, spent, status, progress,
                 message, created_at, updated_at, checkpoint_version, output_dir)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    payer,
                    model,
                    json.dumps(plan),
                    budget,
                    0,
                    status,
                    progress,
                    message,
                    now,
                    now,
                    None,
                    output_dir,
                ),
            )
            conn.commit()

    def update_training_job(
        self,
        job_id: str,
        *,
        status: Optional[str] = None,
        progress: Optional[int] = None,
        spent: Optional[int] = None,
        message: Optional[str] = None,
        checkpoint_version: Optional[str] = None,
        output_dir: Optional[str] = None,
    ) -> bool:
        """Update mutable training job fields."""
        updates: list[str] = []
        values: list[Any] = []

        if status is not None:
            updates.append("status = ?")
            values.append(status)
        if progress is not None:
            updates.append("progress = ?")
            values.append(progress)
        if spent is not None:
            updates.append("spent = ?")
            values.append(spent)
        if message is not None:
            updates.append("message = ?")
            values.append(message)
        if checkpoint_version is not None:
            updates.append("checkpoint_version = ?")
            values.append(checkpoint_version)
        if output_dir is not None:
            updates.append("output_dir = ?")
            values.append(output_dir)

        updates.append("updated_at = ?")
        values.append(time.time())
        values.append(job_id)

        with self._connect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"UPDATE training_jobs SET {', '.join(updates)} WHERE job_id = ?",
                tuple(values),
            )
            conn.commit()
            return cursor.rowcount > 0

    def get_training_job(self, job_id: str) -> Optional[TrainingJob]:
        """Fetch a single training job."""
        with self._connect() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM training_jobs WHERE job_id = ?", (job_id,))
            row = cursor.fetchone()
            return self._row_to_training_job(row) if row else None

    def list_training_jobs(
        self,
        *,
        status: Optional[str] = None,
        limit: int = 20,
    ) -> List[TrainingJob]:
        """List tracked training jobs."""
        with self._connect() as conn:
            cursor = conn.cursor()
            if status:
                cursor.execute(
                    """
                    SELECT * FROM training_jobs
                    WHERE status = ?
                    ORDER BY created_at DESC
                    LIMIT ?
                    """,
                    (status, limit),
                )
            else:
                cursor.execute(
                    """
                    SELECT * FROM training_jobs
                    ORDER BY created_at DESC
                    LIMIT ?
                    """,
                    (limit,),
                )
            rows = cursor.fetchall()
            return [self._row_to_training_job(row) for row in rows]

    def save_checkpoint(
        self,
        *,
        version: str,
        job_id: str,
        model: str,
        epoch: int,
        size_bytes: int,
        path: str,
        metadata: Dict[str, Any],
    ) -> None:
        """Persist checkpoint metadata."""
        with self._connect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT OR REPLACE INTO checkpoints
                (version, job_id, model, epoch, size_bytes, path, metadata_json, published_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    version,
                    job_id,
                    model,
                    epoch,
                    size_bytes,
                    path,
                    json.dumps(metadata),
                    time.time(),
                ),
            )
            conn.commit()

    def get_checkpoint(self, version: str) -> Optional[CheckpointRecord]:
        """Fetch a checkpoint by version."""
        with self._connect() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM checkpoints WHERE version = ?", (version,))
            row = cursor.fetchone()
            return self._row_to_checkpoint(row) if row else None

    def get_checkpoint_for_job(self, job_id: str) -> Optional[CheckpointRecord]:
        """Fetch the latest checkpoint generated for a job."""
        with self._connect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT * FROM checkpoints
                WHERE job_id = ?
                ORDER BY published_at DESC
                LIMIT 1
                """,
                (job_id,),
            )
            row = cursor.fetchone()
            return self._row_to_checkpoint(row) if row else None

    def list_checkpoints(
        self,
        *,
        model: Optional[str] = None,
        limit: int = 20,
    ) -> List[CheckpointRecord]:
        """List checkpoint bundles."""
        with self._connect() as conn:
            cursor = conn.cursor()
            if model:
                cursor.execute(
                    """
                    SELECT * FROM checkpoints
                    WHERE model = ?
                    ORDER BY published_at DESC
                    LIMIT ?
                    """,
                    (model, limit),
                )
            else:
                cursor.execute(
                    """
                    SELECT * FROM checkpoints
                    ORDER BY published_at DESC
                    LIMIT ?
                    """,
                    (limit,),
                )
            rows = cursor.fetchall()
            return [self._row_to_checkpoint(row) for row in rows]

    def _row_to_training_job(self, row: sqlite3.Row) -> TrainingJob:
        return TrainingJob(
            job_id=row["job_id"],
            payer=row["payer"],
            model=row["model"],
            plan=json.loads(row["plan_json"]),
            budget=row["budget"],
            spent=row["spent"],
            status=row["status"],
            progress=row["progress"],
            message=row["message"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            checkpoint_version=row["checkpoint_version"],
            output_dir=row["output_dir"],
        )

    def _row_to_checkpoint(self, row: sqlite3.Row) -> CheckpointRecord:
        return CheckpointRecord(
            version=row["version"],
            job_id=row["job_id"],
            model=row["model"],
            epoch=row["epoch"],
            size_bytes=row["size_bytes"],
            path=row["path"],
            metadata=json.loads(row["metadata_json"]),
            published_at=row["published_at"],
        )
