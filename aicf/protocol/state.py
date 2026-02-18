"""
AICF Protocol State Management
===============================

Manages protocol state including workers, jobs, submissions, epochs, credits, and claims.
Provides transaction-safe operations with SQLite persistence.
"""

from __future__ import annotations

import contextlib
import json
import sqlite3
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

from aicf.errors import AICFError


class ProtocolStateError(AICFError):
    """Protocol state error."""


class NotFoundError(ProtocolStateError):
    """Resource not found."""


class ConflictError(ProtocolStateError):
    """State conflict (duplicate ID, etc.)."""


def _now_s() -> int:
    return int(time.time())


@dataclass
class GPUWorker:
    """GPU worker registration."""
    worker_id: str
    address: str
    pubkey: Optional[str] = None
    display_name: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    stake_tx_hash: Optional[str] = None
    stake_amount: int = 0
    status: str = "INACTIVE"
    region: Optional[str] = None
    created_at: Optional[int] = None
    updated_at: Optional[int] = None


@dataclass
class TrainingJob:
    """Training/eval job specification."""
    job_id: str
    spec_hash: str
    dataset_commit: Optional[str] = None
    job_type: str = "TRAINING"
    difficulty: int = 1
    reward_weight: int = 100
    created_at: Optional[int] = None
    expires_at: Optional[int] = None
    creator: Optional[str] = None
    status: str = "OPEN"


@dataclass
class WorkSubmission:
    """Work submission with proofs."""
    submission_id: str
    job_id: str
    worker_id: str
    artifact_commit: str
    metrics: Optional[Dict[str, Any]] = None
    proof_commit: Optional[str] = None
    status: str = "PENDING"
    posted_at: Optional[int] = None
    challenge_deadline: Optional[int] = None
    verified_by: Optional[str] = None
    rejection_reason: Optional[str] = None
    credits_awarded: int = 0


@dataclass
class SubmissionChallenge:
    """Challenge to a work submission."""
    challenge_id: str
    submission_id: str
    challenger_address: str
    challenge_data_commit: str
    status: str = "OPEN"
    posted_at: Optional[int] = None
    resolved_at: Optional[int] = None
    resolution_commit: Optional[str] = None
    notes: Optional[str] = None


@dataclass
class ProtocolEpoch:
    """Protocol epoch accounting."""
    epoch_id: int
    start_height: int
    end_height: Optional[int] = None
    inflow_total: str = "0"
    inflow_ena: str = "0"
    inflow_other: str = "0"
    inflow_for_workers: str = "0"
    total_credits: str = "0"
    finalized: bool = False
    finalized_at: Optional[int] = None
    merkle_root: Optional[str] = None


@dataclass
class WorkerClaim:
    """Worker claim for epoch rewards."""
    claim_id: str
    epoch_id: int
    worker_id: str
    amount: str
    merkle_proof: Optional[str] = None
    claimed_at: Optional[int] = None
    tx_hash: Optional[str] = None
    status: str = "PENDING"


@dataclass
class AICFInflow:
    """AICF inflow tracking."""
    inflow_id: str
    source: str
    amount: str
    tx_hash: Optional[str] = None
    block_height: Optional[int] = None
    epoch_id: Optional[int] = None
    recorded_at: Optional[int] = None
    metadata: Optional[Dict[str, Any]] = None


@dataclass
class ModelRelease:
    """Model release tracking."""
    release_id: str
    base_model: str
    delta_commit: str
    dataset_commit: Optional[str] = None
    eval_metrics: Optional[Dict[str, Any]] = None
    produced_from_epochs: Optional[List[int]] = None
    approved_by: Optional[str] = None
    timestamp: Optional[int] = None
    version: Optional[str] = None
    notes: Optional[str] = None


@dataclass
class AICFCreditTotals:
    """Global AICF credit accounting."""
    balance_total: str = "0"
    minted_total: str = "0"
    spent_total: str = "0"
    last_update_height: Optional[int] = None
    last_update_hash: Optional[str] = None
    updated_at: Optional[int] = None


@dataclass
class MinerCredits:
    """Per-miner AICF credit balance."""
    miner_address: str
    balance: str = "0"
    lifetime_earned: str = "0"
    lifetime_spent: str = "0"
    last_mint_height: Optional[int] = None
    last_mint_hash: Optional[str] = None
    created_at: Optional[int] = None
    updated_at: Optional[int] = None


@dataclass
class PoolCredits:
    """Per-pool AICF credit balance."""
    pool_address: str
    balance: str = "0"
    lifetime_earned: str = "0"
    lifetime_spent: str = "0"
    last_mint_height: Optional[int] = None
    last_mint_hash: Optional[str] = None
    created_at: Optional[int] = None
    updated_at: Optional[int] = None


@dataclass
class CreditLedgerEntry:
    """AICF credit ledger event."""
    ledger_id: str
    event_type: str  # 'credit_minted' | 'credit_spent'
    block_height: int
    block_hash: str
    amount: str
    source: Optional[str] = None  # 'reward' | 'fees' | 'share'
    miner_address: Optional[str] = None
    pool_address: Optional[str] = None
    job_id: Optional[str] = None
    recipients_json: Optional[str] = None
    timestamp: Optional[int] = None
    metadata: Optional[Dict[str, Any]] = None


class ProtocolState:
    """
    Protocol state manager with SQLite persistence.
    
    Provides CRUD operations for all protocol entities with
    transaction safety and schema migration.
    """

    def __init__(self, db_path: str):
        """
        Initialize protocol state.
        
        Args:
            db_path: Path to SQLite database file
        """
        self.db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None
        self._tx_depth = 0
        self._ensure_db()

    def _ensure_db(self):
        """Ensure database exists and schema is up to date."""
        path = Path(self.db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        
        # Load protocol schema
        schema_path = Path(__file__).parent.parent / "db" / "schema_protocol.sql"
        if schema_path.exists():
            conn.executescript(schema_path.read_text())
        
        conn.commit()
        conn.close()

    @contextlib.contextmanager
    def _get_conn(self) -> Iterator[sqlite3.Connection]:
        """Get database connection with context manager."""
        if self._conn is None:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            try:
                yield conn
            finally:
                conn.close()
        else:
            yield self._conn

    @contextlib.contextmanager
    def tx(self) -> Iterator[None]:
        """Transaction context manager."""
        with self._get_conn() as conn:
            if self._tx_depth == 0:
                conn.execute("BEGIN")
            self._tx_depth += 1
            self._conn = conn
            try:
                yield
                self._tx_depth -= 1
                if self._tx_depth == 0:
                    conn.commit()
                    self._conn = None
            except Exception:
                self._tx_depth = 0
                conn.rollback()
                self._conn = None
                raise

    # --- GPU Workers ---

    def register_worker(self, worker: GPUWorker) -> str:
        """Register a new GPU worker."""
        with self.tx():
            with self._get_conn() as conn:
                now = _now_s()
                conn.execute(
                    """
                    INSERT INTO gpu_workers (
                        worker_id, address, pubkey, display_name, metadata_json,
                        stake_tx_hash, stake_amount, status, region,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        worker.worker_id,
                        worker.address,
                        worker.pubkey,
                        worker.display_name,
                        json.dumps(worker.metadata) if worker.metadata else None,
                        worker.stake_tx_hash,
                        worker.stake_amount,
                        worker.status,
                        worker.region,
                        worker.created_at or now,
                        worker.updated_at or now,
                    ),
                )
        return worker.worker_id

    def update_worker(self, worker_id: str, **fields) -> None:
        """Update worker fields."""
        if not fields:
            return
        
        with self.tx():
            with self._get_conn() as conn:
                # Build SET clause
                set_parts = []
                values = []
                for k, v in fields.items():
                    if k == "metadata":
                        set_parts.append("metadata_json = ?")
                        values.append(json.dumps(v) if v else None)
                    else:
                        set_parts.append(f"{k} = ?")
                        values.append(v)
                
                values.append(worker_id)
                sql = f"UPDATE gpu_workers SET {', '.join(set_parts)} WHERE worker_id = ?"
                conn.execute(sql, values)

    def get_worker(self, worker_id: str) -> GPUWorker:
        """Get worker by ID."""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM gpu_workers WHERE worker_id = ?",
                (worker_id,)
            ).fetchone()
            
            if not row:
                raise NotFoundError(f"Worker not found: {worker_id}")
            
            return GPUWorker(
                worker_id=row["worker_id"],
                address=row["address"],
                pubkey=row["pubkey"],
                display_name=row["display_name"],
                metadata=json.loads(row["metadata_json"]) if row["metadata_json"] else None,
                stake_tx_hash=row["stake_tx_hash"],
                stake_amount=row["stake_amount"],
                status=row["status"],
                region=row["region"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )

    def list_workers(
        self,
        status: Optional[str] = None,
        offset: int = 0,
        limit: int = 100
    ) -> List[GPUWorker]:
        """List workers with optional filtering."""
        with self._get_conn() as conn:
            if status:
                rows = conn.execute(
                    "SELECT * FROM gpu_workers WHERE status = ? ORDER BY created_at DESC LIMIT ? OFFSET ?",
                    (status, limit, offset)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM gpu_workers ORDER BY created_at DESC LIMIT ? OFFSET ?",
                    (limit, offset)
                ).fetchall()
            
            workers = []
            for row in rows:
                workers.append(GPUWorker(
                    worker_id=row["worker_id"],
                    address=row["address"],
                    pubkey=row["pubkey"],
                    display_name=row["display_name"],
                    metadata=json.loads(row["metadata_json"]) if row["metadata_json"] else None,
                    stake_tx_hash=row["stake_tx_hash"],
                    stake_amount=row["stake_amount"],
                    status=row["status"],
                    region=row["region"],
                    created_at=row["created_at"],
                    updated_at=row["updated_at"],
                ))
            
            return workers

    # --- Training Jobs ---

    def create_job(self, job: TrainingJob) -> str:
        """Create a new training job."""
        with self.tx():
            with self._get_conn() as conn:
                now = _now_s()
                conn.execute(
                    """
                    INSERT INTO training_jobs (
                        job_id, spec_hash, dataset_commit, job_type, difficulty,
                        reward_weight, created_at, expires_at, creator, status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        job.job_id,
                        job.spec_hash,
                        job.dataset_commit,
                        job.job_type,
                        job.difficulty,
                        job.reward_weight,
                        job.created_at or now,
                        job.expires_at,
                        job.creator,
                        job.status,
                    ),
                )
        return job.job_id

    def get_job(self, job_id: str) -> TrainingJob:
        """Get job by ID."""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM training_jobs WHERE job_id = ?",
                (job_id,)
            ).fetchone()
            
            if not row:
                raise NotFoundError(f"Job not found: {job_id}")
            
            return TrainingJob(
                job_id=row["job_id"],
                spec_hash=row["spec_hash"],
                dataset_commit=row["dataset_commit"],
                job_type=row["job_type"],
                difficulty=row["difficulty"],
                reward_weight=row["reward_weight"],
                created_at=row["created_at"],
                expires_at=row["expires_at"],
                creator=row["creator"],
                status=row["status"],
            )

    def list_jobs(
        self,
        status: Optional[str] = None,
        job_type: Optional[str] = None,
        offset: int = 0,
        limit: int = 100
    ) -> List[TrainingJob]:
        """List jobs with optional filtering."""
        with self._get_conn() as conn:
            conditions = []
            params: List[Any] = []
            
            if status:
                conditions.append("status = ?")
                params.append(status)
            if job_type:
                conditions.append("job_type = ?")
                params.append(job_type)
            
            where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
            sql = f"SELECT * FROM training_jobs {where} ORDER BY created_at DESC LIMIT ? OFFSET ?"
            params.extend([limit, offset])
            
            rows = conn.execute(sql, params).fetchall()
            
            jobs = []
            for row in rows:
                jobs.append(TrainingJob(
                    job_id=row["job_id"],
                    spec_hash=row["spec_hash"],
                    dataset_commit=row["dataset_commit"],
                    job_type=row["job_type"],
                    difficulty=row["difficulty"],
                    reward_weight=row["reward_weight"],
                    created_at=row["created_at"],
                    expires_at=row["expires_at"],
                    creator=row["creator"],
                    status=row["status"],
                ))
            
            return jobs

    # --- Work Submissions ---

    def submit_work(self, submission: WorkSubmission) -> str:
        """Submit work for a job."""
        with self.tx():
            with self._get_conn() as conn:
                now = _now_s()
                conn.execute(
                    """
                    INSERT INTO work_submissions (
                        submission_id, job_id, worker_id, artifact_commit,
                        metrics_json, proof_commit, status, posted_at,
                        challenge_deadline, verified_by, rejection_reason, credits_awarded
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        submission.submission_id,
                        submission.job_id,
                        submission.worker_id,
                        submission.artifact_commit,
                        json.dumps(submission.metrics) if submission.metrics else None,
                        submission.proof_commit,
                        submission.status,
                        submission.posted_at or now,
                        submission.challenge_deadline,
                        submission.verified_by,
                        submission.rejection_reason,
                        submission.credits_awarded,
                    ),
                )
        return submission.submission_id

    def get_submission(self, submission_id: str) -> WorkSubmission:
        """Get submission by ID."""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM work_submissions WHERE submission_id = ?",
                (submission_id,)
            ).fetchone()
            
            if not row:
                raise NotFoundError(f"Submission not found: {submission_id}")
            
            return WorkSubmission(
                submission_id=row["submission_id"],
                job_id=row["job_id"],
                worker_id=row["worker_id"],
                artifact_commit=row["artifact_commit"],
                metrics=json.loads(row["metrics_json"]) if row["metrics_json"] else None,
                proof_commit=row["proof_commit"],
                status=row["status"],
                posted_at=row["posted_at"],
                challenge_deadline=row["challenge_deadline"],
                verified_by=row["verified_by"],
                rejection_reason=row["rejection_reason"],
                credits_awarded=row["credits_awarded"],
            )

    def update_submission(self, submission_id: str, **fields) -> None:
        """Update submission fields."""
        if not fields:
            return
        
        with self.tx():
            with self._get_conn() as conn:
                set_parts = []
                values = []
                for k, v in fields.items():
                    if k == "metrics":
                        set_parts.append("metrics_json = ?")
                        values.append(json.dumps(v) if v else None)
                    else:
                        set_parts.append(f"{k} = ?")
                        values.append(v)
                
                values.append(submission_id)
                sql = f"UPDATE work_submissions SET {', '.join(set_parts)} WHERE submission_id = ?"
                conn.execute(sql, values)

    # --- Challenges ---

    def create_challenge(self, challenge: SubmissionChallenge) -> str:
        """Create a submission challenge."""
        with self.tx():
            with self._get_conn() as conn:
                now = _now_s()
                conn.execute(
                    """
                    INSERT INTO submission_challenges (
                        challenge_id, submission_id, challenger_address,
                        challenge_data_commit, status, posted_at, resolved_at,
                        resolution_commit, notes
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        challenge.challenge_id,
                        challenge.submission_id,
                        challenge.challenger_address,
                        challenge.challenge_data_commit,
                        challenge.status,
                        challenge.posted_at or now,
                        challenge.resolved_at,
                        challenge.resolution_commit,
                        challenge.notes,
                    ),
                )
        return challenge.challenge_id

    def resolve_challenge(
        self,
        challenge_id: str,
        status: str,
        resolution_commit: Optional[str] = None
    ) -> None:
        """Resolve a challenge."""
        with self.tx():
            with self._get_conn() as conn:
                now = _now_s()
                conn.execute(
                    """
                    UPDATE submission_challenges
                    SET status = ?, resolved_at = ?, resolution_commit = ?
                    WHERE challenge_id = ?
                    """,
                    (status, now, resolution_commit, challenge_id),
                )

    # --- Epochs ---

    def get_or_create_epoch(self, epoch_id: int, start_height: int) -> ProtocolEpoch:
        """Get or create an epoch."""
        with self.tx():
            with self._get_conn() as conn:
                row = conn.execute(
                    "SELECT * FROM protocol_epochs WHERE epoch_id = ?",
                    (epoch_id,)
                ).fetchone()
                
                if row:
                    return ProtocolEpoch(
                        epoch_id=row["epoch_id"],
                        start_height=row["start_height"],
                        end_height=row["end_height"],
                        inflow_total=row["inflow_total"],
                        inflow_ena=row["inflow_ena"],
                        inflow_other=row["inflow_other"],
                        inflow_for_workers=row["inflow_for_workers"],
                        total_credits=row["total_credits"],
                        finalized=bool(row["finalized"]),
                        finalized_at=row["finalized_at"],
                        merkle_root=row["merkle_root"],
                    )
                
                # Create new epoch
                conn.execute(
                    """
                    INSERT INTO protocol_epochs (
                        epoch_id, start_height
                    ) VALUES (?, ?)
                    """,
                    (epoch_id, start_height),
                )
                
                return ProtocolEpoch(
                    epoch_id=epoch_id,
                    start_height=start_height,
                )

    def update_epoch(self, epoch_id: int, **fields) -> None:
        """Update epoch fields."""
        if not fields:
            return
        
        with self.tx():
            with self._get_conn() as conn:
                set_parts = []
                values = []
                for k, v in fields.items():
                    set_parts.append(f"{k} = ?")
                    values.append(v)
                
                values.append(epoch_id)
                sql = f"UPDATE protocol_epochs SET {', '.join(set_parts)} WHERE epoch_id = ?"
                conn.execute(sql, values)

    def get_epoch(self, epoch_id: int) -> ProtocolEpoch:
        """Get epoch by ID."""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM protocol_epochs WHERE epoch_id = ?",
                (epoch_id,)
            ).fetchone()
            
            if not row:
                raise NotFoundError(f"Epoch not found: {epoch_id}")
            
            return ProtocolEpoch(
                epoch_id=row["epoch_id"],
                start_height=row["start_height"],
                end_height=row["end_height"],
                inflow_total=row["inflow_total"],
                inflow_ena=row["inflow_ena"],
                inflow_other=row["inflow_other"],
                inflow_for_workers=row["inflow_for_workers"],
                total_credits=row["total_credits"],
                finalized=bool(row["finalized"]),
                finalized_at=row["finalized_at"],
                merkle_root=row["merkle_root"],
            )

    # --- Credits ---

    def add_credits(self, epoch_id: int, worker_id: str, credits: str) -> None:
        """Add credits to a worker for an epoch."""
        with self.tx():
            with self._get_conn() as conn:
                # Get current credits
                row = conn.execute(
                    "SELECT credits FROM epoch_credits WHERE epoch_id = ? AND worker_id = ?",
                    (epoch_id, worker_id)
                ).fetchone()
                
                if row:
                    # Add to existing
                    current = int(row["credits"])
                    new_total = str(current + int(credits))
                    conn.execute(
                        "UPDATE epoch_credits SET credits = ? WHERE epoch_id = ? AND worker_id = ?",
                        (new_total, epoch_id, worker_id)
                    )
                else:
                    # Insert new
                    conn.execute(
                        "INSERT INTO epoch_credits (epoch_id, worker_id, credits) VALUES (?, ?, ?)",
                        (epoch_id, worker_id, credits)
                    )

    def get_worker_credits(self, epoch_id: int, worker_id: str) -> str:
        """Get worker credits for an epoch."""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT credits FROM epoch_credits WHERE epoch_id = ? AND worker_id = ?",
                (epoch_id, worker_id)
            ).fetchone()
            
            return row["credits"] if row else "0"

    def get_epoch_credits(self, epoch_id: int) -> Dict[str, str]:
        """Get all credits for an epoch."""
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT worker_id, credits FROM epoch_credits WHERE epoch_id = ?",
                (epoch_id,)
            ).fetchall()
            
            return {row["worker_id"]: row["credits"] for row in rows}

    # --- Claims ---

    def create_claim(self, claim: WorkerClaim) -> str:
        """Create a worker claim."""
        with self.tx():
            with self._get_conn() as conn:
                now = _now_s()
                conn.execute(
                    """
                    INSERT INTO worker_claims (
                        claim_id, epoch_id, worker_id, amount, merkle_proof,
                        claimed_at, tx_hash, status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        claim.claim_id,
                        claim.epoch_id,
                        claim.worker_id,
                        claim.amount,
                        claim.merkle_proof,
                        claim.claimed_at or now,
                        claim.tx_hash,
                        claim.status,
                    ),
                )
        return claim.claim_id

    def update_claim(self, claim_id: str, **fields) -> None:
        """Update claim fields."""
        if not fields:
            return
        
        with self.tx():
            with self._get_conn() as conn:
                set_parts = []
                values = []
                for k, v in fields.items():
                    set_parts.append(f"{k} = ?")
                    values.append(v)
                
                values.append(claim_id)
                sql = f"UPDATE worker_claims SET {', '.join(set_parts)} WHERE claim_id = ?"
                conn.execute(sql, values)

    # --- Inflows ---

    def record_inflow(self, inflow: AICFInflow) -> str:
        """Record an AICF inflow."""
        with self.tx():
            with self._get_conn() as conn:
                now = _now_s()
                conn.execute(
                    """
                    INSERT INTO aicf_inflows (
                        inflow_id, source, amount, tx_hash, block_height,
                        epoch_id, recorded_at, metadata_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        inflow.inflow_id,
                        inflow.source,
                        inflow.amount,
                        inflow.tx_hash,
                        inflow.block_height,
                        inflow.epoch_id,
                        inflow.recorded_at or now,
                        json.dumps(inflow.metadata) if inflow.metadata else None,
                    ),
                )
        return inflow.inflow_id

    def get_epoch_inflows(self, epoch_id: int) -> List[AICFInflow]:
        """Get all inflows for an epoch."""
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM aicf_inflows WHERE epoch_id = ?",
                (epoch_id,)
            ).fetchall()
            
            inflows = []
            for row in rows:
                inflows.append(AICFInflow(
                    inflow_id=row["inflow_id"],
                    source=row["source"],
                    amount=row["amount"],
                    tx_hash=row["tx_hash"],
                    block_height=row["block_height"],
                    epoch_id=row["epoch_id"],
                    recorded_at=row["recorded_at"],
                    metadata=json.loads(row["metadata_json"]) if row["metadata_json"] else None,
                ))
            
            return inflows

    # --- Model Releases ---

    def create_model_release(self, release: ModelRelease) -> str:
        """Create a model release."""
        with self.tx():
            with self._get_conn() as conn:
                now = _now_s()
                conn.execute(
                    """
                    INSERT INTO model_releases (
                        release_id, base_model, delta_commit, dataset_commit,
                        eval_metrics_json, produced_from_epochs, approved_by,
                        timestamp, version, notes
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        release.release_id,
                        release.base_model,
                        release.delta_commit,
                        release.dataset_commit,
                        json.dumps(release.eval_metrics) if release.eval_metrics else None,
                        json.dumps(release.produced_from_epochs) if release.produced_from_epochs else None,
                        release.approved_by,
                        release.timestamp or now,
                        release.version,
                        release.notes,
                    ),
                )
        return release.release_id

    def get_model_release(self, release_id: str) -> ModelRelease:
        """Get model release by ID."""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM model_releases WHERE release_id = ?",
                (release_id,)
            ).fetchone()
            
            if not row:
                raise NotFoundError(f"Model release not found: {release_id}")
            
            return ModelRelease(
                release_id=row["release_id"],
                base_model=row["base_model"],
                delta_commit=row["delta_commit"],
                dataset_commit=row["dataset_commit"],
                eval_metrics=json.loads(row["eval_metrics_json"]) if row["eval_metrics_json"] else None,
                produced_from_epochs=json.loads(row["produced_from_epochs"]) if row["produced_from_epochs"] else None,
                approved_by=row["approved_by"],
                timestamp=row["timestamp"],
                version=row["version"],
                notes=row["notes"],
            )

    # --- Parameters ---

    def get_param(self, key: str, default: Optional[str] = None) -> Optional[str]:
        """Get a protocol parameter."""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT value FROM protocol_params WHERE key = ?",
                (key,)
            ).fetchone()
            
            return row["value"] if row else default

    def set_param(self, key: str, value: str) -> None:
        """Set a protocol parameter."""
        with self.tx():
            with self._get_conn() as conn:
                now = _now_s()
                conn.execute(
                    """
                    INSERT OR REPLACE INTO protocol_params (key, value, updated_at)
                    VALUES (?, ?, ?)
                    """,
                    (key, value, now),
                )

    def get_all_params(self) -> Dict[str, str]:
        """Get all protocol parameters."""
        with self._get_conn() as conn:
            rows = conn.execute("SELECT key, value FROM protocol_params").fetchall()
            return {row["key"]: row["value"] for row in rows}

    # --- AICF Credit Tracking ---

    def get_aicf_totals(self) -> AICFCreditTotals:
        """Get global AICF credit totals."""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM aicf_credit_totals WHERE id = 1"
            ).fetchone()
            
            if not row:
                # Initialize if missing
                now = _now_s()
                conn.execute(
                    """
                    INSERT INTO aicf_credit_totals(id, balance_total, minted_total, spent_total, updated_at)
                    VALUES (1, '0', '0', '0', ?)
                    """,
                    (now,)
                )
                conn.commit()
                return AICFCreditTotals(updated_at=now)
            
            return AICFCreditTotals(
                balance_total=row["balance_total"],
                minted_total=row["minted_total"],
                spent_total=row["spent_total"],
                last_update_height=row["last_update_height"],
                last_update_hash=row["last_update_hash"],
                updated_at=row["updated_at"],
            )

    def update_aicf_totals(
        self,
        *,
        balance_delta: int = 0,
        minted_delta: int = 0,
        spent_delta: int = 0,
        block_height: Optional[int] = None,
        block_hash: Optional[str] = None,
    ) -> None:
        """
        Update global AICF credit totals.
        
        Args:
            balance_delta: Amount to add to balance_total (can be negative for spending)
            minted_delta: Amount to add to minted_total
            spent_delta: Amount to add to spent_total
            block_height: Current block height
            block_hash: Current block hash
        """
        with self.tx():
            with self._get_conn() as conn:
                now = _now_s()
                
                # Get current values
                row = conn.execute(
                    "SELECT balance_total, minted_total, spent_total FROM aicf_credit_totals WHERE id = 1"
                ).fetchone()
                
                if not row:
                    # Initialize
                    new_balance = str(max(0, balance_delta))
                    new_minted = str(max(0, minted_delta))
                    new_spent = str(max(0, spent_delta))
                else:
                    new_balance = str(int(row["balance_total"]) + balance_delta)
                    new_minted = str(int(row["minted_total"]) + minted_delta)
                    new_spent = str(int(row["spent_total"]) + spent_delta)
                
                # Ensure non-negative
                assert int(new_balance) >= 0, "Balance cannot be negative"
                assert int(new_minted) >= 0, "Minted total cannot be negative"
                assert int(new_spent) >= 0, "Spent total cannot be negative"
                
                conn.execute(
                    """
                    INSERT OR REPLACE INTO aicf_credit_totals(
                        id, balance_total, minted_total, spent_total,
                        last_update_height, last_update_hash, updated_at
                    ) VALUES (1, ?, ?, ?, ?, ?, ?)
                    """,
                    (new_balance, new_minted, new_spent, block_height, block_hash, now),
                )

    def get_miner_credits(self, miner_address: str) -> MinerCredits:
        """Get AICF credit balance for a miner."""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM aicf_miner_credits WHERE miner_address = ?",
                (miner_address,)
            ).fetchone()
            
            if not row:
                return MinerCredits(miner_address=miner_address)
            
            return MinerCredits(
                miner_address=row["miner_address"],
                balance=row["balance"],
                lifetime_earned=row["lifetime_earned"],
                lifetime_spent=row["lifetime_spent"],
                last_mint_height=row["last_mint_height"],
                last_mint_hash=row["last_mint_hash"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )

    def add_miner_credits(
        self,
        miner_address: str,
        amount: int,
        block_height: int,
        block_hash: str,
    ) -> None:
        """
        Add AICF credits to a miner's balance.
        
        Args:
            miner_address: Miner address (hex string)
            amount: Credits to add (must be positive)
            block_height: Block height where credits were minted
            block_hash: Block hash where credits were minted
        """
        assert amount > 0, "Amount must be positive"
        
        with self.tx():
            with self._get_conn() as conn:
                now = _now_s()
                
                # Check if miner exists
                row = conn.execute(
                    "SELECT balance, lifetime_earned FROM aicf_miner_credits WHERE miner_address = ?",
                    (miner_address,)
                ).fetchone()
                
                if row:
                    # Update existing
                    new_balance = str(int(row["balance"]) + amount)
                    new_earned = str(int(row["lifetime_earned"]) + amount)
                    conn.execute(
                        """
                        UPDATE aicf_miner_credits
                        SET balance = ?, lifetime_earned = ?,
                            last_mint_height = ?, last_mint_hash = ?, updated_at = ?
                        WHERE miner_address = ?
                        """,
                        (new_balance, new_earned, block_height, block_hash, now, miner_address),
                    )
                else:
                    # Insert new
                    conn.execute(
                        """
                        INSERT INTO aicf_miner_credits(
                            miner_address, balance, lifetime_earned, lifetime_spent,
                            last_mint_height, last_mint_hash, created_at, updated_at
                        ) VALUES (?, ?, ?, '0', ?, ?, ?, ?)
                        """,
                        (miner_address, str(amount), str(amount), block_height, block_hash, now, now),
                    )

    def spend_miner_credits(
        self,
        miner_address: str,
        amount: int,
    ) -> None:
        """
        Spend AICF credits from a miner's balance.
        
        Args:
            miner_address: Miner address
            amount: Credits to spend (must be positive)
        
        Raises:
            ValueError: If insufficient balance
        """
        assert amount > 0, "Amount must be positive"
        
        with self.tx():
            with self._get_conn() as conn:
                row = conn.execute(
                    "SELECT balance, lifetime_spent FROM aicf_miner_credits WHERE miner_address = ?",
                    (miner_address,)
                ).fetchone()
                
                if not row:
                    raise ValueError(f"Miner not found: {miner_address}")
                
                current_balance = int(row["balance"])
                if current_balance < amount:
                    raise ValueError(f"Insufficient balance: {current_balance} < {amount}")
                
                new_balance = str(current_balance - amount)
                new_spent = str(int(row["lifetime_spent"]) + amount)
                
                conn.execute(
                    """
                    UPDATE aicf_miner_credits
                    SET balance = ?, lifetime_spent = ?
                    WHERE miner_address = ?
                    """,
                    (new_balance, new_spent, miner_address),
                )

    def log_credit_event(
        self,
        ledger_id: str,
        event_type: str,
        block_height: int,
        block_hash: str,
        amount: str,
        *,
        source: Optional[str] = None,
        miner_address: Optional[str] = None,
        pool_address: Optional[str] = None,
        job_id: Optional[str] = None,
        recipients: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Log a credit event to the immutable ledger.
        
        Args:
            ledger_id: Deterministic event ID (sha256 of event data)
            event_type: 'credit_minted' or 'credit_spent'
            block_height: Block height
            block_hash: Block hash
            amount: Credit amount (u256 decimal string)
            source: For minted events: 'reward', 'fees', 'share'
            miner_address: For minted events
            pool_address: For pool credits
            job_id: For spent events
            recipients: For spent events (list of addresses)
            metadata: Additional event data
        """
        with self.tx():
            with self._get_conn() as conn:
                now = _now_s()
                
                recipients_json = json.dumps(recipients) if recipients else None
                metadata_json = json.dumps(metadata) if metadata else None
                
                conn.execute(
                    """
                    INSERT OR IGNORE INTO aicf_credit_ledger(
                        ledger_id, event_type, block_height, block_hash, amount,
                        source, miner_address, pool_address, job_id, recipients_json,
                        timestamp, metadata_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        ledger_id, event_type, block_height, block_hash, amount,
                        source, miner_address, pool_address, job_id, recipients_json,
                        now, metadata_json,
                    ),
                )

    def get_credit_ledger(
        self,
        *,
        event_type: Optional[str] = None,
        miner_address: Optional[str] = None,
        job_id: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[CreditLedgerEntry]:
        """
        Query credit ledger events.
        
        Args:
            event_type: Filter by event type
            miner_address: Filter by miner address
            job_id: Filter by job ID
            limit: Max results
            offset: Pagination offset
        
        Returns:
            List of ledger entries
        """
        with self._get_conn() as conn:
            query = "SELECT * FROM aicf_credit_ledger WHERE 1=1"
            params: List[Any] = []
            
            if event_type:
                query += " AND event_type = ?"
                params.append(event_type)
            if miner_address:
                query += " AND miner_address = ?"
                params.append(miner_address)
            if job_id:
                query += " AND job_id = ?"
                params.append(job_id)
            
            query += " ORDER BY block_height DESC, timestamp DESC LIMIT ? OFFSET ?"
            params.extend([limit, offset])
            
            rows = conn.execute(query, params).fetchall()
            
            return [
                CreditLedgerEntry(
                    ledger_id=row["ledger_id"],
                    event_type=row["event_type"],
                    block_height=row["block_height"],
                    block_hash=row["block_hash"],
                    amount=row["amount"],
                    source=row["source"],
                    miner_address=row["miner_address"],
                    pool_address=row["pool_address"],
                    job_id=row["job_id"],
                    recipients_json=row["recipients_json"],
                    timestamp=row["timestamp"],
                    metadata=json.loads(row["metadata_json"]) if row["metadata_json"] else None,
                )
                for row in rows
            ]
