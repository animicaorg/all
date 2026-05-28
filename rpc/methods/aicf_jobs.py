"""
aicf.* job-submission RPC methods.

Implements the inference-job API the `distributed-aicf` provider in
ai/agent_runtime/src/agent_runtime/aicf_client.py expects to find on the
node. Methods registered:

    aicf.estimateJobCost
    aicf.submitInferenceJob
    aicf.streamJob
    aicf.jobStatus
    aicf.settleJob
    aicf.workerRegister
    aicf.workerStatus
    aicf.workerClaimNextJob
    aicf.workerSubmitResult

This is a single-process, in-memory implementation of the protocol — it
keeps the job lifecycle correct end-to-end (estimate → submit → stream →
settle) so `animica chat` works, and supports external workers registering
and claiming jobs. If no external worker claims a job within a short
grace period, a built-in local fallback completes it with a stub
response. The stub clearly identifies itself so it isn't mistaken for
real inference output.

State is reset on every node restart by design — distributed jobs are not
yet persisted to chain state. Long-term, the queue/storage and economics
modules under aicf/queue/* and aicf/economics/* will replace this; for
now they provide the helpers we use here (pricing, job id derivation).
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional

from rpc.errors import InternalError, InvalidParams
from rpc.methods import method

log = logging.getLogger("animica.rpc.aicf_jobs")


# ---------------------------------------------------------------------------
# Treasury address & settlement config
# ---------------------------------------------------------------------------

# Where user → AICF-pool transfers go on-chain. Env-overridable so each
# deployment can rotate. The default is a documented well-known burn-style
# address per chain_id; protocol can later sweep it into the AICF state pool.
_DEFAULT_TREASURY_BY_CHAIN: Dict[int, str] = {
    # Mainnet treasury — set by the operator. User-funded inference jobs
    # transfer their estimated cost into this address; the protocol will
    # later sweep it into the AICF state pool for epoch payouts. Operators
    # can override per-node via ANIMICA_AICF_TREASURY_ADDRESS.
    1: "anim1zqpf6a5hvup7kggxdutt3tgswz4aa9rwlpsz8ywf73m4l5cmzhfk7pcnh6kgt",
}
_TREASURY_ENV = "ANIMICA_AICF_TREASURY_ADDRESS"

# Should submitInferenceJob require a valid signed payment txn that we
# successfully broadcast to the mempool? Default off so node startups
# without mining/peering still serve the chat protocol — flip to "1" in
# production to enforce on-chain settlement strictly.
_REQUIRE_SETTLEMENT_ENV = "ANIMICA_AICF_REQUIRE_SETTLEMENT"


def _treasury_address_for(chain_id: int) -> str:
    override = os.environ.get(_TREASURY_ENV, "").strip()
    if override:
        return override
    if chain_id in _DEFAULT_TREASURY_BY_CHAIN:
        return _DEFAULT_TREASURY_BY_CHAIN[chain_id]
    # Derive a deterministic placeholder for unknown chains. SHA3-256 of
    # a label is the same algorithm the constants above use; this keeps
    # them reproducible without ever needing a private key.
    digest = hashlib.sha3_256(f"aicf-treasury-chain-{chain_id}".encode()).digest()
    try:
        from pq.py.address import encode_address  # type: ignore
        return encode_address(digest, alg_id=0x1001)
    except Exception:
        # Last-resort hex form if bech32 encoder isn't reachable. The
        # tx-body builder accepts hex too.
        return "0x" + digest.hex()


def _require_settlement() -> bool:
    raw = os.environ.get(_REQUIRE_SETTLEMENT_ENV, "0").strip().lower()
    return raw in {"1", "true", "yes", "on"}


# ---------------------------------------------------------------------------
# Pricing & defaults
# ---------------------------------------------------------------------------

_TIER_PRICE_PER_KTOK: Dict[str, float] = {
    # Cost in ANM per 1k tokens (prompt + max output combined).
    # Deliberately small — these defaults exist so estimateJobCost is
    # never zero; real pricing should come from chain-anchored policy
    # once the on-chain pricing schedule is wired up.
    "free":     0.0001,
    "standard": 0.001,
    "premium":  0.01,
    "elite":    0.05,
}
_DEFAULT_TIER = "standard"
_TIER_LATENCY_MS: Dict[str, int] = {
    "free": 6000,
    "standard": 3000,
    "premium": 1500,
    "elite": 800,
}

# How long to wait for an external worker to claim a job before the local
# fallback kicks in. Keeps chat usable on a fresh node with no providers
# while still giving real miners a fair window to receive the notify,
# load the model, run inference, and submit results back. Override with
# ANIMICA_AICF_WORKER_CLAIM_GRACE_S to favor stubs on a no-miner node.
_WORKER_CLAIM_GRACE_S = float(
    os.environ.get("ANIMICA_AICF_WORKER_CLAIM_GRACE_S", "30.0")
)
# How long a worker lease is valid; if a worker claims and goes silent,
# the job becomes reclaimable after this many seconds. The default is
# generous so a miner downloading multi-GB model weights on its first
# inference job does not have its lease reclaimed mid-download. Override
# with ANIMICA_AICF_WORKER_LEASE_S to enforce stricter SLAs.
_WORKER_LEASE_S = float(
    os.environ.get("ANIMICA_AICF_WORKER_LEASE_S", "600.0")
)
# Race replication factor: how many distinct workers can claim the same
# job concurrently. First valid worker submit wins (and is paid); losers
# get a clean "lost_race" reply. K=1 reverts to the pre-race assignment
# behavior. Sensible defaults assume tiers have cheap per-token pricing
# so K=3 fanout is affordable for chat latency wins.
_REPLICAS_DEFAULT = max(1, int(
    os.environ.get("ANIMICA_AICF_REPLICAS", "3") or "3"
))


# ---------------------------------------------------------------------------
# In-memory state
# ---------------------------------------------------------------------------


@dataclass
class _JobRecord:
    job_id: str
    spec: Dict[str, Any]
    payment: Dict[str, Any]
    estimated_cost: float
    tier: str
    state: str = "pending"          # pending → claimed → completed | failed
    text: str = ""
    provider_id: str = ""
    created_at: float = field(default_factory=time.time)
    claimed_at: Optional[float] = None
    completed_at: Optional[float] = None
    # `claim_owner` / `claim_expires_at` reflect the MOST RECENT claim and
    # are kept for backward-compat logging. The authoritative list of
    # active claims (for K-way race replication) lives in `claims`.
    claim_owner: Optional[str] = None
    claim_expires_at: Optional[float] = None
    error: Optional[str] = None
    # On-chain settlement: filled when the payment txn is broadcast.
    payment_tx_hash: Optional[str] = None
    payment_accepted: bool = False
    payment_status: Optional[str] = None
    settled_chain_id: Optional[int] = None
    # Race replication. `replicas_wanted` is the max number of workers
    # that may claim this job in parallel; the first to submit a valid
    # result wins (and is credited). `claims` is the canonical list of
    # currently-active leases: each entry is
    # `{"address": str, "claimed_at": float, "expires_at": float}`.
    replicas_wanted: int = 1
    claims: List[Dict[str, Any]] = field(default_factory=list)
    winner_address: Optional[str] = None


@dataclass
class _WorkerInfo:
    address: str
    tiers: List[str]
    hardware: Dict[str, Any]
    registered_at: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)
    jobs_completed: int = 0
    # Earnings ledger. `earnings_pending_animica` accumulates the cost
    # of each job the worker has completed but which has not yet been
    # settled on-chain (treasury → state-pool sweep is a future
    # consensus upgrade). `earnings_paid_animica` is the running total
    # of confirmed payouts; right now only ever populated manually.
    earnings_pending_animica: float = 0.0
    earnings_paid_animica: float = 0.0


class _AicfJobStore:
    """Thread-safe in-memory store for AICF jobs and workers."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._jobs: Dict[str, _JobRecord] = {}
        self._workers: Dict[str, _WorkerInfo] = {}

    # ---------- jobs ----------

    def submit(self, job: _JobRecord) -> None:
        with self._lock:
            self._jobs[job.job_id] = job

    def get(self, job_id: str) -> Optional[_JobRecord]:
        with self._lock:
            return self._jobs.get(job_id)

    def claim_next(self, worker_addr: str, tiers: List[str]) -> Optional[_JobRecord]:
        # Empty tiers means "this worker advertised no capability" — match
        # nothing. Past inverted semantic ("not tiers → match all") let
        # bare-RPC callers and stack-less miners (no torch) claim every
        # job and return stubs the user still paid for.
        if not tiers:
            return None
        now = time.time()
        with self._lock:
            for job in self._jobs.values():
                if job.state in {"completed", "failed"}:
                    continue
                if job.tier not in tiers:
                    continue
                # Drop expired claims so a slow worker doesn't lock out
                # the slot indefinitely. Note this mutates the live job.
                live_claims = [
                    c for c in job.claims
                    if float(c.get("expires_at") or 0.0) > now
                ]
                job.claims = live_claims
                # Don't let the same worker double-claim a job.
                if any(c.get("address") == worker_addr for c in live_claims):
                    continue
                # Cap concurrent claims at replicas_wanted (K-way race).
                cap = max(1, int(job.replicas_wanted or 1))
                if len(live_claims) >= cap:
                    continue
                claim = {
                    "address": worker_addr,
                    "claimed_at": now,
                    "expires_at": now + _WORKER_LEASE_S,
                }
                job.claims.append(claim)
                job.state = "claimed"
                # Mirror to legacy single-claim fields for logging compat.
                job.claim_owner = worker_addr
                job.claimed_at = now
                job.claim_expires_at = now + _WORKER_LEASE_S
                return job
        return None

    def complete(
        self,
        job_id: str,
        *,
        text: str,
        provider_id: str,
    ) -> Optional[_JobRecord]:
        """Atomic first-writer-wins. Returns the updated record on success
        or None if the job was already terminal (someone else won the
        race, or the stub fallback already answered). Callers must treat
        None as "lost the race — do not credit this worker."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            if job.state in {"completed", "failed"}:
                return None
            job.text = text
            job.provider_id = provider_id
            job.state = "completed"
            job.completed_at = time.time()
            job.winner_address = provider_id
            return job

    def fail(self, job_id: str, error: str) -> Optional[_JobRecord]:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            job.state = "failed"
            job.error = error
            job.completed_at = time.time()
            return job

    # ---------- workers ----------

    def register_worker(self, info: _WorkerInfo) -> None:
        with self._lock:
            self._workers[info.address] = info

    def get_worker(self, address: str) -> Optional[_WorkerInfo]:
        with self._lock:
            w = self._workers.get(address)
            if w is not None:
                w.last_seen = time.time()
            return w

    def all_workers(self) -> List[_WorkerInfo]:
        with self._lock:
            return list(self._workers.values())


# ---------------------------------------------------------------------------
# SQLite-backed persistent store
# ---------------------------------------------------------------------------
#
# Same API as _AicfJobStore so the rest of the module is store-agnostic.
# Persists job queue + worker ledger across node restarts under a
# SQLite file at ANIMICA_AICF_JOB_DB (default: <ANIMICA_DATA_DIR>/aicf_jobs.db).
# WAL journal mode + busy_timeout makes the same file safe to open
# concurrently from multiple pool/node processes — that's how
# "cross-pool job sharing" works without consensus replication: every
# process pointing at the same DB file (or a shared mount) sees the
# same claims/results in real time.

import json as _json
import sqlite3 as _sqlite3

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS jobs (
    job_id TEXT PRIMARY KEY,
    spec_json TEXT NOT NULL,
    payment_json TEXT NOT NULL,
    estimated_cost REAL NOT NULL,
    tier TEXT NOT NULL,
    state TEXT NOT NULL,
    text TEXT NOT NULL DEFAULT '',
    provider_id TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    claimed_at REAL,
    completed_at REAL,
    claim_owner TEXT,
    claim_expires_at REAL,
    error TEXT,
    payment_tx_hash TEXT,
    payment_accepted INTEGER NOT NULL DEFAULT 0,
    payment_status TEXT,
    settled_chain_id INTEGER,
    replicas_wanted INTEGER NOT NULL DEFAULT 1,
    claims_json TEXT NOT NULL DEFAULT '[]',
    winner_address TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_state_tier ON jobs(state, tier);
CREATE INDEX IF NOT EXISTS idx_jobs_claim_owner ON jobs(claim_owner);

-- Migrations for jobs DBs that pre-date race-replication columns. ALTER
-- TABLE ADD COLUMN with a default is constant-time in SQLite, so this is
-- safe for large stores. Errors are swallowed at the call site when the
-- column already exists.

CREATE TABLE IF NOT EXISTS workers (
    address TEXT PRIMARY KEY,
    tiers_json TEXT NOT NULL,
    hardware_json TEXT NOT NULL,
    registered_at REAL NOT NULL,
    last_seen REAL NOT NULL,
    jobs_completed INTEGER NOT NULL DEFAULT 0,
    earnings_pending_animica REAL NOT NULL DEFAULT 0,
    earnings_paid_animica REAL NOT NULL DEFAULT 0
);
"""


def _resolve_job_db_path() -> Optional[str]:
    override = os.environ.get("ANIMICA_AICF_JOB_DB", "").strip()
    if override:
        return override
    data_dir = os.environ.get("ANIMICA_DATA_DIR", "").strip()
    if not data_dir:
        return None  # caller falls back to in-memory store
    try:
        os.makedirs(data_dir, exist_ok=True)
    except Exception as exc:
        log.warning("aicf_jobs: cannot create data dir %s: %s", data_dir, exc)
        return None
    return os.path.join(data_dir, "aicf_jobs.db")


class _SqliteAicfJobStore:
    """SQLite-backed AICF job + worker store.

    Same API surface as _AicfJobStore so call sites are unchanged.
    Connection is opened per-thread (sqlite3 connections aren't safe
    to share across threads); we route all writes through a single
    file lock by using WAL + busy_timeout instead of an in-process
    lock so multi-process operation works the same as multi-thread.
    """

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._tls = threading.local()
        # Initialize schema once.
        conn = self._conn()
        with conn:
            conn.executescript(_SCHEMA_SQL)
        self._migrate_race_columns(conn)

    @staticmethod
    def _migrate_race_columns(conn: _sqlite3.Connection) -> None:
        """Add the race-replication columns to a pre-existing jobs table.

        ALTER TABLE ... ADD COLUMN is idempotent only when wrapped in a
        try/except — SQLite returns OperationalError if the column is
        already present. The defaults match _JobRecord so pre-race rows
        keep behaving like K=1 with no active replicas.
        """
        for col, ddl in (
            ("replicas_wanted",
             "ALTER TABLE jobs ADD COLUMN replicas_wanted INTEGER NOT NULL DEFAULT 1"),
            ("claims_json",
             "ALTER TABLE jobs ADD COLUMN claims_json TEXT NOT NULL DEFAULT '[]'"),
            ("winner_address",
             "ALTER TABLE jobs ADD COLUMN winner_address TEXT"),
        ):
            try:
                conn.execute(ddl)
            except _sqlite3.OperationalError:
                pass

    def _conn(self) -> _sqlite3.Connection:
        conn = getattr(self._tls, "conn", None)
        if conn is None:
            conn = _sqlite3.connect(
                self._db_path,
                timeout=10.0,
                isolation_level=None,  # autocommit; we use explicit txns
                check_same_thread=True,
            )
            conn.row_factory = _sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute("PRAGMA foreign_keys=ON")
            self._tls.conn = conn
        return conn

    # ---------- (de)serialization ----------

    @staticmethod
    def _job_from_row(row: _sqlite3.Row) -> _JobRecord:
        keys = row.keys()
        replicas = int(row["replicas_wanted"]) if "replicas_wanted" in keys else 1
        try:
            claims_raw = row["claims_json"] if "claims_json" in keys else "[]"
            claims = _json.loads(claims_raw) if claims_raw else []
            if not isinstance(claims, list):
                claims = []
        except (ValueError, TypeError):
            claims = []
        winner = row["winner_address"] if "winner_address" in keys else None
        return _JobRecord(
            job_id=row["job_id"],
            spec=_json.loads(row["spec_json"]),
            payment=_json.loads(row["payment_json"]),
            estimated_cost=float(row["estimated_cost"]),
            tier=row["tier"],
            state=row["state"],
            text=row["text"] or "",
            provider_id=row["provider_id"] or "",
            created_at=float(row["created_at"]),
            claimed_at=row["claimed_at"],
            completed_at=row["completed_at"],
            claim_owner=row["claim_owner"],
            claim_expires_at=row["claim_expires_at"],
            error=row["error"],
            payment_tx_hash=row["payment_tx_hash"],
            payment_accepted=bool(row["payment_accepted"]),
            payment_status=row["payment_status"],
            settled_chain_id=row["settled_chain_id"],
            replicas_wanted=replicas,
            claims=claims,
            winner_address=winner,
        )

    @staticmethod
    def _worker_from_row(row: _sqlite3.Row) -> _WorkerInfo:
        return _WorkerInfo(
            address=row["address"],
            tiers=_json.loads(row["tiers_json"]),
            hardware=_json.loads(row["hardware_json"]),
            registered_at=float(row["registered_at"]),
            last_seen=float(row["last_seen"]),
            jobs_completed=int(row["jobs_completed"]),
            earnings_pending_animica=float(row["earnings_pending_animica"]),
            earnings_paid_animica=float(row["earnings_paid_animica"]),
        )

    # ---------- jobs ----------

    def submit(self, job: _JobRecord) -> None:
        conn = self._conn()
        conn.execute(
            "INSERT OR REPLACE INTO jobs ("
            "  job_id,spec_json,payment_json,estimated_cost,tier,state,text,"
            "  provider_id,created_at,claimed_at,completed_at,claim_owner,"
            "  claim_expires_at,error,payment_tx_hash,payment_accepted,"
            "  payment_status,settled_chain_id,replicas_wanted,claims_json,"
            "  winner_address"
            ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                job.job_id,
                _json.dumps(job.spec),
                _json.dumps(job.payment),
                job.estimated_cost,
                job.tier,
                job.state,
                job.text,
                job.provider_id,
                job.created_at,
                job.claimed_at,
                job.completed_at,
                job.claim_owner,
                job.claim_expires_at,
                job.error,
                job.payment_tx_hash,
                1 if job.payment_accepted else 0,
                job.payment_status,
                job.settled_chain_id,
                int(job.replicas_wanted or 1),
                _json.dumps(job.claims or []),
                job.winner_address,
            ),
        )

    def get(self, job_id: str) -> Optional[_JobRecord]:
        row = self._conn().execute(
            "SELECT * FROM jobs WHERE job_id=?", (job_id,)
        ).fetchone()
        return self._job_from_row(row) if row else None

    def claim_next(self, worker_addr: str, tiers: List[str]) -> Optional[_JobRecord]:
        # Empty tiers means "worker has no advertised capability" — match
        # nothing. See _AicfJobStore.claim_next above for the rationale.
        if not tiers:
            return None
        now = time.time()
        conn = self._conn()
        # Single transaction so concurrent claimers can't pick the
        # same job. SQLite serializes writers, busy_timeout handles
        # the cross-process case.
        conn.execute("BEGIN IMMEDIATE")
        try:
            placeholders = ",".join(["?"] * len(tiers))
            tier_filter = f" AND tier IN ({placeholders})"
            # Scan candidate non-terminal jobs in this worker's tier and
            # pick the first one with an open replica slot. We can't
            # express "claims count below cap" as a pure SQL filter
            # without a join table, so do it in Python with a small N.
            candidates = conn.execute(
                "SELECT * FROM jobs WHERE state IN ('pending','claimed')"
                + tier_filter
                + " ORDER BY created_at ASC LIMIT 64",
                tuple(tiers),
            ).fetchall()
            chosen_row = None
            chosen_claims: List[Dict[str, Any]] = []
            for row in candidates:
                cap = int(row["replicas_wanted"]) if "replicas_wanted" in row.keys() else 1
                cap = max(1, cap)
                try:
                    raw = row["claims_json"] if "claims_json" in row.keys() else "[]"
                    claims = _json.loads(raw) if raw else []
                    if not isinstance(claims, list):
                        claims = []
                except (ValueError, TypeError):
                    claims = []
                live = [
                    c for c in claims
                    if float(c.get("expires_at") or 0.0) > now
                ]
                if any(c.get("address") == worker_addr for c in live):
                    continue
                if len(live) >= cap:
                    continue
                chosen_row = row
                chosen_claims = live
                break
            if chosen_row is None:
                conn.execute("COMMIT")
                return None
            chosen_claims.append({
                "address": worker_addr,
                "claimed_at": now,
                "expires_at": now + _WORKER_LEASE_S,
            })
            conn.execute(
                "UPDATE jobs SET state='claimed',claim_owner=?,claimed_at=?,"
                "claim_expires_at=?,claims_json=? WHERE job_id=?",
                (
                    worker_addr,
                    now,
                    now + _WORKER_LEASE_S,
                    _json.dumps(chosen_claims),
                    chosen_row["job_id"],
                ),
            )
            conn.execute("COMMIT")
            updated = self.get(chosen_row["job_id"])
            return updated
        except Exception:
            conn.execute("ROLLBACK")
            raise

    def complete(
        self,
        job_id: str,
        *,
        text: str,
        provider_id: str,
    ) -> Optional[_JobRecord]:
        """First-writer-wins via guarded UPDATE. Returns None when the
        row was already terminal (lost the race) so callers know not to
        credit the worker. SQLite's rowcount tells us how many rows the
        WHERE clause matched after the update."""
        now = time.time()
        conn = self._conn()
        cur = conn.execute(
            "UPDATE jobs SET state='completed',text=?,provider_id=?,"
            "completed_at=?,winner_address=? "
            "WHERE job_id=? AND state IN ('pending','claimed')",
            (text, provider_id, now, provider_id, job_id),
        )
        if (cur.rowcount or 0) <= 0:
            return None
        return self.get(job_id)

    def fail(self, job_id: str, error: str) -> Optional[_JobRecord]:
        now = time.time()
        with self._conn():
            self._conn().execute(
                "UPDATE jobs SET state='failed',error=?,completed_at=? "
                "WHERE job_id=?",
                (error, now, job_id),
            )
        return self.get(job_id)

    # ---------- workers ----------

    def register_worker(self, info: _WorkerInfo) -> None:
        self._conn().execute(
            "INSERT INTO workers (address,tiers_json,hardware_json,"
            "registered_at,last_seen,jobs_completed,"
            "earnings_pending_animica,earnings_paid_animica) "
            "VALUES (?,?,?,?,?,?,?,?) "
            "ON CONFLICT(address) DO UPDATE SET "
            "  tiers_json=excluded.tiers_json,"
            "  hardware_json=excluded.hardware_json,"
            "  last_seen=excluded.last_seen",
            (
                info.address,
                _json.dumps(info.tiers),
                _json.dumps(info.hardware),
                info.registered_at,
                info.last_seen,
                info.jobs_completed,
                info.earnings_pending_animica,
                info.earnings_paid_animica,
            ),
        )

    def get_worker(self, address: str) -> Optional[_WorkerInfo]:
        conn = self._conn()
        now = time.time()
        conn.execute("UPDATE workers SET last_seen=? WHERE address=?", (now, address))
        row = conn.execute(
            "SELECT * FROM workers WHERE address=?", (address,)
        ).fetchone()
        return self._worker_from_row(row) if row else None

    def all_workers(self) -> List[_WorkerInfo]:
        rows = self._conn().execute("SELECT * FROM workers").fetchall()
        return [self._worker_from_row(r) for r in rows]

    # ---------- earnings credit (atomic) ----------
    # The call sites do `w.jobs_completed += 1; w.earnings_pending_animica += ...`
    # but with SQLite, mutating an in-memory copy doesn't persist. We
    # expose a credit() helper used at the worker-credit call site.

    def credit_worker_completion(self, address: str, amount: float) -> None:
        conn = self._conn()
        with conn:
            conn.execute(
                "UPDATE workers SET jobs_completed=jobs_completed+1,"
                "earnings_pending_animica=earnings_pending_animica+? "
                "WHERE address=?",
                (float(amount or 0.0), address),
            )


def _make_store() -> Any:
    """Pick a job store at module load. Prefer SQLite when a data dir
    is configured; fall back to in-memory otherwise. Set
    ANIMICA_AICF_JOB_STORE=memory to force the in-memory path (useful
    in tests or when running on a read-only filesystem)."""
    forced = os.environ.get("ANIMICA_AICF_JOB_STORE", "").strip().lower()
    if forced == "memory":
        log.info("aicf_jobs: using in-memory store (forced)")
        return _AicfJobStore()
    path = _resolve_job_db_path()
    if path is None:
        log.info("aicf_jobs: using in-memory store (no data dir)")
        return _AicfJobStore()
    try:
        store = _SqliteAicfJobStore(path)
        log.info("aicf_jobs: using SQLite store at %s", path)
        return store
    except Exception as exc:
        log.warning(
            "aicf_jobs: SQLite store init failed (%s); falling back to memory", exc
        )
        return _AicfJobStore()


_STORE = _make_store()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _coerce_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _coerce_str(value: Any, default: str = "") -> str:
    return str(value) if value is not None else default


def _resolve_tier(requested: Optional[str]) -> str:
    if not requested:
        return _DEFAULT_TIER
    t = str(requested).lower().strip()
    return t if t in _TIER_PRICE_PER_KTOK else _DEFAULT_TIER


def _price_job(prompt_tokens: int, max_output_tokens: int, tier: str) -> float:
    rate = _TIER_PRICE_PER_KTOK.get(tier, _TIER_PRICE_PER_KTOK[_DEFAULT_TIER])
    total_tokens = max(0, prompt_tokens) + max(0, max_output_tokens)
    return round((total_tokens / 1000.0) * rate, 9)


def _decode_payment_envelope(blob: Any) -> Optional[Dict[str, Any]]:
    """Decode the txn_hex hex string from a SignedPayment blob into the
    canonical {sig, body} dict. Returns None when the input doesn't look
    like a signed-tx envelope (so callers can fall back to "no settlement")."""
    if not isinstance(blob, Mapping):
        return None
    txn_hex = str(blob.get("txn_hex") or blob.get("txnHex") or "").strip()
    if not txn_hex:
        return None
    raw = txn_hex
    if raw.startswith("0x") or raw.startswith("0X"):
        raw = raw[2:]
    try:
        raw_bytes = bytes.fromhex(raw)
    except ValueError:
        return None
    try:
        import cbor2  # type: ignore
        decoded = cbor2.loads(raw_bytes)
    except Exception:
        return None
    if not isinstance(decoded, Mapping):
        return None
    return {"raw_bytes": raw_bytes, "decoded": dict(decoded), "txn_hex": "0x" + raw}


async def _broadcast_payment(raw_hex: str) -> Dict[str, Any]:
    """Submit the payment txn to the local mempool via the existing
    tx.sendRawTransaction dispatcher. Returns {accepted, tx_hash, status, reason}."""
    try:
        from rpc.methods import dispatch
    except Exception as exc:
        return {"accepted": False, "tx_hash": None, "status": "dispatcher_unavailable", "reason": str(exc)}
    try:
        result = await dispatch("tx.sendRawTransaction", {"rawTx": raw_hex})
    except Exception as exc:
        return {"accepted": False, "tx_hash": None, "status": "submit_failed", "reason": str(exc)}
    # tx.sendRawTransaction returns either a plain hex tx_hash string or a
    # structured dict (when it wants to flag warnings / status).
    if isinstance(result, str):
        return {"accepted": True, "tx_hash": result, "status": "accepted", "reason": None}
    if isinstance(result, Mapping):
        tx_hash = result.get("tx_hash") or result.get("hash")
        accepted = bool(result.get("accepted_to_mempool") or result.get("persisted_to_chain"))
        return {
            "accepted": accepted,
            "tx_hash": tx_hash,
            "status": result.get("status") or ("accepted" if accepted else "rejected"),
            "reason": result.get("reason"),
        }
    return {"accepted": False, "tx_hash": None, "status": "unexpected_result", "reason": repr(result)[:200]}


def _stub_response(prompt: str, tier: str) -> str:
    return (
        f"[distributed-aicf stub @ tier={tier}] No external workers have "
        f"claimed this job; the node returned this placeholder so the "
        f"protocol round-trip completes. The original prompt was: "
        f"{prompt[:240]}{'…' if len(prompt) > 240 else ''}"
    )


async def _local_fallback_after_grace(job_id: str) -> None:
    """Stub-complete a job whose worker never came back, so chat doesn't hang.

    Two cases the chat user perceives identically as "no response":

    1. No worker claimed the job (state stays "pending"). Stub immediately
       after the grace window — no provider is coming.
    2. A worker claimed it (state -> "claimed") but never sent a result —
       worker disconnected, errored mid-inference, or got wedged loading a
       model. The lease default is _WORKER_LEASE_S (10 minutes); without
       this branch, chat is unresponsive that whole time. We give the
       worker one more grace window after claim, then ship the stub
       anyway.
    """
    await asyncio.sleep(_WORKER_CLAIM_GRACE_S)
    job = _STORE.get(job_id)
    if job is None or job.state in {"completed", "failed"}:
        return
    if job.state == "claimed":
        # Give the worker one more grace window to actually return a result
        # before giving up — small chat completions normally finish well
        # inside this; a worker that hasn't is wedged.
        await asyncio.sleep(_WORKER_CLAIM_GRACE_S)
        job = _STORE.get(job_id)
        if job is None or job.state in {"completed", "failed"}:
            return
    prompt = str(job.spec.get("prompt", ""))
    text = _stub_response(prompt, job.tier)
    prior_state = job.state
    _STORE.complete(job_id, text=text, provider_id="local-stub")
    log.info(
        "aicf_jobs: local-stub completed job_id=%s tier=%s prior_state=%s",
        job_id, job.tier, prior_state,
    )


def _schedule_fallback(job_id: str) -> None:
    """Schedule the local fallback in whatever event loop is running.
    Falls back to a thread if no loop is reachable from this thread."""
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_local_fallback_after_grace(job_id))
        return
    except RuntimeError:
        pass

    def _run() -> None:
        time.sleep(_WORKER_CLAIM_GRACE_S)
        job = _STORE.get(job_id)
        if job is None or job.state in {"completed", "failed"}:
            return
        if job.state == "claimed":
            time.sleep(_WORKER_CLAIM_GRACE_S)
            job = _STORE.get(job_id)
            if job is None or job.state in {"completed", "failed"}:
                return
        prompt = str(job.spec.get("prompt", ""))
        text = _stub_response(prompt, job.tier)
        prior_state = job.state
        _STORE.complete(job_id, text=text, provider_id="local-stub")
        log.info(
            "aicf_jobs: local-stub completed (thread) job_id=%s tier=%s prior_state=%s",
            job_id, job.tier, prior_state,
        )

    threading.Thread(target=_run, daemon=True, name=f"aicf-stub-{job_id[:8]}").start()


# ---------------------------------------------------------------------------
# Client-facing methods (estimate / submit / stream / status / settle)
# ---------------------------------------------------------------------------


@method("aicf.estimateJobCost", desc="Estimate cost of a distributed-AICF job")
async def estimate_job_cost(
    ctx: Any = None,
    **params: Any,
) -> Dict[str, Any]:
    p = dict(params or {})
    prompt_tokens = _coerce_int(p.get("prompt_tokens"), 0)
    max_output_tokens = _coerce_int(p.get("max_output_tokens"), 256)
    tier = _resolve_tier(p.get("tier_preferred"))
    cost = _price_job(prompt_tokens, max_output_tokens, tier)
    providers = len([w for w in _STORE.all_workers() if not w.tiers or tier in w.tiers])
    return {
        "cost_animica": cost,
        "latency_ms": _TIER_LATENCY_MS.get(tier, _TIER_LATENCY_MS[_DEFAULT_TIER]),
        "tier": tier,
        "providers": providers,
        "schedule": "in-memory-defaults",
    }


@method("aicf.submitInferenceJob", desc="Submit a distributed-AICF inference job")
async def submit_inference_job(
    ctx: Any = None,
    **params: Any,
) -> Dict[str, Any]:
    p = dict(params or {})
    spec = p.get("spec")
    payment = p.get("payment")
    if not isinstance(spec, Mapping):
        raise InvalidParams("submitInferenceJob: 'spec' must be an object")
    if not isinstance(payment, Mapping):
        raise InvalidParams("submitInferenceJob: 'payment' must be an object")
    prompt = _coerce_str(spec.get("prompt"))
    if not prompt:
        raise InvalidParams("submitInferenceJob: spec.prompt is required")

    tier = _resolve_tier(spec.get("tier_preferred"))
    max_out = _coerce_int(spec.get("max_output_tokens"), 256)
    prompt_tokens = max(1, len(prompt) // 4)  # rough token estimate
    cost = _price_job(prompt_tokens, max_out, tier)

    job_id = "0x" + uuid.uuid4().hex
    # Per-job replicas override: caller can pass spec.replicas to ask
    # for narrower or wider racing than the node default (e.g. elite
    # tier might want 1, debugging might want a single replica). Bound
    # to [1, 16] so a misconfig can't fan out forever.
    requested_replicas = spec.get("replicas") if isinstance(spec, Mapping) else None
    try:
        replicas = int(requested_replicas) if requested_replicas is not None else _REPLICAS_DEFAULT
    except (TypeError, ValueError):
        replicas = _REPLICAS_DEFAULT
    replicas = max(1, min(16, replicas))
    rec = _JobRecord(
        job_id=job_id,
        spec=dict(spec),
        payment=dict(payment),
        estimated_cost=cost,
        tier=tier,
        replicas_wanted=replicas,
    )

    # Real on-chain settlement: decode the SignedPayment envelope and
    # broadcast the underlying tx via tx.sendRawTransaction. This moves
    # value from the user's wallet into the AICF treasury on the next
    # mined block. The body's `to` field must already match the
    # treasury address — we don't rewrite it server-side.
    decoded = _decode_payment_envelope(payment)
    if decoded is not None:
        broadcast = await _broadcast_payment(decoded["txn_hex"])
        rec.payment_tx_hash = broadcast.get("tx_hash")
        rec.payment_accepted = bool(broadcast.get("accepted"))
        rec.payment_status = broadcast.get("status")
        try:
            body = decoded["decoded"].get("body") or {}
            chain_field = body.get("chainId")
            if isinstance(chain_field, int):
                rec.settled_chain_id = chain_field
        except Exception:
            pass
        if not rec.payment_accepted and _require_settlement():
            reason = broadcast.get("reason") or broadcast.get("status") or "unknown"
            raise InvalidParams(
                f"submitInferenceJob: payment rejected by mempool: {reason}"
            )
        # Surface the rejection reason — historically only status was
        # logged, which hid the actual mempool error and let payments
        # silently fail for every chat turn. When acceptance is False
        # we want the cause front-and-center so debit regressions are
        # caught immediately.
        log_fn = log.warning if not rec.payment_accepted else log.info
        log_fn(
            "aicf_jobs: payment broadcast accepted=%s tx_hash=%s status=%s reason=%s",
            rec.payment_accepted,
            rec.payment_tx_hash,
            rec.payment_status,
            broadcast.get("reason"),
        )
    elif _require_settlement():
        raise InvalidParams(
            "submitInferenceJob: payment envelope could not be decoded; "
            "settlement is required by ANIMICA_AICF_REQUIRE_SETTLEMENT"
        )
    else:
        log.info(
            "aicf_jobs: payment envelope not decoded; accepting without "
            "on-chain settlement (set ANIMICA_AICF_REQUIRE_SETTLEMENT=1 to "
            "make this a hard error)"
        )

    _STORE.submit(rec)
    _schedule_fallback(job_id)
    log.info("aicf_jobs: submitted job_id=%s tier=%s est_cost=%.9f", job_id, tier, cost)
    return {
        "job_id": job_id,
        "accepted_tier": tier,
        "provider_id": "",   # filled in on completion
        "estimated_cost_animica": cost,
        "payment_tx_hash": rec.payment_tx_hash,
        "payment_accepted": rec.payment_accepted,
        "payment_status": rec.payment_status,
    }


@method("aicf.getTreasuryAddress", desc="Get the on-chain AICF treasury address for this node")
async def get_treasury_address(
    ctx: Any = None,
    **params: Any,
) -> Dict[str, Any]:
    chain_id = _coerce_int((params or {}).get("chain_id"), 0)
    if chain_id <= 0:
        # Best-effort: pull from the RPC ctx if available.
        try:
            chain_id = int(getattr(ctx, "chain_id", 0) or 0)
        except Exception:
            chain_id = 0
    if chain_id <= 0:
        chain_id = 1
    addr = _treasury_address_for(chain_id)
    return {
        "treasury_address": addr,
        "chain_id": chain_id,
        "source": "env" if os.environ.get(_TREASURY_ENV) else "default",
        "require_settlement": _require_settlement(),
    }


@method("aicf.streamJob", desc="Stream chunks for an in-flight AICF job")
async def stream_job(
    ctx: Any = None,
    **params: Any,
) -> Dict[str, Any]:
    p = dict(params or {})
    job_id = _coerce_str(p.get("job_id"))
    cursor = _coerce_int(p.get("cursor"), 0)
    if not job_id:
        raise InvalidParams("streamJob: 'job_id' is required")
    job = _STORE.get(job_id)
    if job is None:
        raise InvalidParams(f"streamJob: unknown job_id {job_id}")

    # Long-ish poll: wait up to ~1.5s for new text or completion.
    deadline = time.time() + 1.5
    while time.time() < deadline:
        if job.state in {"completed", "failed"}:
            break
        if len(job.text) > cursor:
            break
        await asyncio.sleep(0.05)
        job = _STORE.get(job_id) or job

    chunk_text = job.text[cursor:] if len(job.text) > cursor else ""
    is_final = job.state in {"completed", "failed"} and (cursor + len(chunk_text)) >= len(job.text)
    return {
        "text": chunk_text,
        "final": is_final,
        "next_cursor": cursor + len(chunk_text),
        "token_count": max(1, len(chunk_text) // 4) if chunk_text else 0,
        "state": job.state,
    }


@method("aicf.jobStatus", desc="Get the current status of an AICF job")
async def job_status(
    ctx: Any = None,
    **params: Any,
) -> Dict[str, Any]:
    p = dict(params or {})
    job_id = _coerce_str(p.get("job_id"))
    if not job_id:
        raise InvalidParams("jobStatus: 'job_id' is required")
    job = _STORE.get(job_id)
    if job is None:
        raise InvalidParams(f"jobStatus: unknown job_id {job_id}")
    return {
        "job_id": job.job_id,
        "state": "running" if job.state in {"pending", "claimed"} else job.state,
        "text": job.text,
        "tier": job.tier,
        "provider_id": job.provider_id,
        "error": job.error,
        "created_at": job.created_at,
        "completed_at": job.completed_at,
        "payment_tx_hash": job.payment_tx_hash,
        "payment_accepted": job.payment_accepted,
        "payment_status": job.payment_status,
    }


@method("aicf.settleJob", desc="Settle and return final result for a completed AICF job")
async def settle_job(
    ctx: Any = None,
    **params: Any,
) -> Dict[str, Any]:
    p = dict(params or {})
    job_id = _coerce_str(p.get("job_id"))
    if not job_id:
        raise InvalidParams("settleJob: 'job_id' is required")
    job = _STORE.get(job_id)
    if job is None:
        raise InvalidParams(f"settleJob: unknown job_id {job_id}")
    if job.state not in {"completed", "failed"}:
        # Block briefly for completion (cap at ~3s).
        deadline = time.time() + 3.0
        while time.time() < deadline and job.state not in {"completed", "failed"}:
            await asyncio.sleep(0.05)
            job = _STORE.get(job_id) or job
    latency_ms = 0
    if job.completed_at and job.created_at:
        latency_ms = int(max(0.0, (job.completed_at - job.created_at) * 1000))
    return {
        "text": job.text,
        "cost_animica": job.estimated_cost,
        "provider_id": job.provider_id or "local-stub",
        "settled": job.state == "completed",
        "latency_ms": latency_ms,
        "state": job.state,
        "error": job.error,
        "payment_tx_hash": job.payment_tx_hash,
        "payment_accepted": job.payment_accepted,
        "payment_status": job.payment_status,
    }


# ---------------------------------------------------------------------------
# Worker-facing methods
# ---------------------------------------------------------------------------


@method("aicf.workerRegister", desc="Register a worker to claim AICF jobs", aliases=("aicf_workerRegister",))
async def worker_register(
    ctx: Any = None,
    **params: Any,
) -> Dict[str, Any]:
    p = dict(params or {})
    address = _coerce_str(p.get("address")).strip()
    if not address:
        raise InvalidParams("workerRegister: 'address' is required")
    tiers_raw = p.get("tiers") or []
    if not isinstance(tiers_raw, list):
        raise InvalidParams("workerRegister: 'tiers' must be a list")
    tiers = [_resolve_tier(t) for t in tiers_raw]
    hardware = p.get("hardware") or {}
    if not isinstance(hardware, Mapping):
        raise InvalidParams("workerRegister: 'hardware' must be an object")
    info = _WorkerInfo(address=address, tiers=tiers, hardware=dict(hardware))
    _STORE.register_worker(info)
    log.info("aicf_jobs: worker registered address=%s tiers=%s", address, tiers)
    return {"registered": True, "address": address, "tiers": tiers}


@method("aicf.workerStatus", desc="Get status of a registered AICF worker", aliases=("aicf_workerStatus",))
async def worker_status(
    ctx: Any = None,
    **params: Any,
) -> Dict[str, Any]:
    p = dict(params or {})
    address = _coerce_str(p.get("address")).strip()
    if not address:
        raise InvalidParams("workerStatus: 'address' is required")
    w = _STORE.get_worker(address)
    if w is None:
        return {"registered": False, "address": address}
    return {
        "registered": True,
        "address": w.address,
        "tiers": list(w.tiers),
        "hardware": dict(w.hardware),
        "registered_at": w.registered_at,
        "last_seen": w.last_seen,
        "jobs_completed": w.jobs_completed,
        "earnings_pending_animica": w.earnings_pending_animica,
        "earnings_paid_animica": w.earnings_paid_animica,
    }


@method(
    "aicf.workerEarnings",
    desc="Get pending and paid AICF earnings for a worker address",
    aliases=("aicf_workerEarnings",),
)
async def worker_earnings(
    ctx: Any = None,
    **params: Any,
) -> Dict[str, Any]:
    """Per-worker earnings ledger.

    ``earnings_pending_animica`` accumulates each completed-job cost; it
    represents value the worker has earned but which has not been swept
    from the on-chain treasury into the AICF state pool yet. That sweep
    is a future consensus-level operation — when wired, the pending
    amount becomes claimable via the existing aicf.claim / aicf.getClaimable
    flow.

    Until then, this ledger is the canonical reference for "what does
    the network owe me." Persistence is in-memory and resets on node
    restart, just like the rest of the AICF job queue.
    """
    p = dict(params or {})
    address = _coerce_str(p.get("address")).strip()
    if not address:
        raise InvalidParams("workerEarnings: 'address' is required")
    w = _STORE.get_worker(address)
    if w is None:
        return {
            "registered": False,
            "address": address,
            "earnings_pending_animica": 0.0,
            "earnings_paid_animica": 0.0,
            "jobs_completed": 0,
            "note": "settlement_pending_consensus_upgrade",
        }
    return {
        "registered": True,
        "address": w.address,
        "jobs_completed": w.jobs_completed,
        "earnings_pending_animica": w.earnings_pending_animica,
        "earnings_paid_animica": w.earnings_paid_animica,
        "note": "settlement_pending_consensus_upgrade",
    }


@method("aicf.workerClaimNextJob", desc="Claim the next pending AICF job", aliases=("aicf_workerClaimNextJob",))
async def worker_claim_next_job(
    ctx: Any = None,
    **params: Any,
) -> Optional[Dict[str, Any]]:
    p = dict(params or {})
    address = _coerce_str(p.get("address")).strip()
    if not address:
        raise InvalidParams("workerClaimNextJob: 'address' is required")
    tiers_raw = p.get("tiers") or []
    if not isinstance(tiers_raw, list):
        raise InvalidParams("workerClaimNextJob: 'tiers' must be a list")
    tiers = [_resolve_tier(t) for t in tiers_raw]
    job = _STORE.claim_next(address, tiers)
    if job is None:
        return None
    return {
        "job_id": job.job_id,
        "spec": dict(job.spec),
        "tier": job.tier,
        "estimated_cost_animica": job.estimated_cost,
        "claim_expires_at": job.claim_expires_at,
    }


@method("aicf.workerSubmitResult", desc="Submit the result of a claimed AICF job", aliases=("aicf_workerSubmitResult",))
async def worker_submit_result(
    ctx: Any = None,
    **params: Any,
) -> Dict[str, Any]:
    p = dict(params or {})
    address = _coerce_str(p.get("address")).strip()
    job_id = _coerce_str(p.get("job_id")).strip()
    text = _coerce_str(p.get("text"))
    if not address or not job_id:
        raise InvalidParams("workerSubmitResult: 'address' and 'job_id' are required")
    job = _STORE.get(job_id)
    if job is None:
        raise InvalidParams(f"workerSubmitResult: unknown job_id {job_id}")
    if job.state not in {"claimed", "pending"}:
        # Already completed (e.g. local stub raced ahead, or another
        # worker won the race) — accept idempotently. With K-way race
        # replication this is the dominant non-winner outcome; surface
        # it so the miner side can distinguish "I lost the race" from
        # "I won and was credited."
        winner = job.winner_address or job.provider_id or ""
        return {
            "accepted": False,
            "state": job.state,
            "reason": "lost_race" if winner and winner != address else "already_terminal",
            "winner_address": winner,
            "job_id": job_id,
        }
    # The legacy single-owner mismatch warning is now noisy under K-way
    # replication where every replica's address differs from claim_owner.
    # Keep the late-submit log only when the submitter isn't in the
    # active claims list at all (truly unsolicited submit).
    claim_addrs = {c.get("address") for c in (job.claims or [])}
    if claim_addrs and address not in claim_addrs:
        log.info(
            "aicf_jobs: accepting unsolicited submit for job %s from %s "
            "(active claimants: %s)",
            job_id, address, sorted(a for a in claim_addrs if a),
        )
    completed = _STORE.complete(job_id, text=text, provider_id=address)
    if completed is None:
        # Lost the race between read-state and complete: someone else
        # transitioned the job to terminal in between. Do NOT credit;
        # surface the same lost_race reply as the early branch above.
        latest = _STORE.get(job_id)
        winner = (latest.winner_address or latest.provider_id) if latest else ""
        return {
            "accepted": False,
            "state": latest.state if latest else "unknown",
            "reason": "lost_race" if winner and winner != address else "already_terminal",
            "winner_address": winner or "",
            "job_id": job_id,
        }
    # Credit ONLY the winner for the full estimated cost of the job. With
    # the SQLite store, in-memory dataclass mutation doesn't persist — use
    # the atomic credit helper when available so the IOU survives a node
    # restart. With the in-memory store, fall back to direct field
    # mutation. See aicf.workerEarnings for retrieval; once the
    # treasury→state-pool sweep activates (consensus rule), these IOUs
    # become claimable via aicf.claim.
    amount = float(completed.estimated_cost or 0.0)
    if hasattr(_STORE, "credit_worker_completion"):
        try:
            _STORE.credit_worker_completion(address, amount)
        except Exception as exc:
            log.warning("aicf_jobs: credit_worker_completion failed: %s", exc)
    else:
        w = _STORE.get_worker(address)
        if w is not None:
            w.jobs_completed += 1
            try:
                w.earnings_pending_animica += amount
            except (TypeError, ValueError):
                pass
    return {"accepted": True, "state": "completed", "job_id": job_id,
            "winner_address": address}


__all__ = [
    "estimate_job_cost",
    "submit_inference_job",
    "stream_job",
    "job_status",
    "settle_job",
    "worker_register",
    "worker_status",
    "worker_claim_next_job",
    "worker_submit_result",
]
