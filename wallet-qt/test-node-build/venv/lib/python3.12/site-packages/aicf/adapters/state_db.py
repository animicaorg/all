from __future__ import annotations

"""
AICF SQLite state adapter
=========================

Purpose
-------
Lightweight persistence for AICF provider registry, jobs, leases, stakes,
balances, and payouts. This module favors a pragmatic SQLite schema with
JSON payloads for forward-compat, plus a handful of indexed columns used for
filters and joins.

Design notes
------------
- Single-writer, many-reader friendly via WAL.
- Schema versioned and auto-migrated on open().
- JSON columns store the full canonical representation of higher-level
  dataclasses (from aicf.aitypes.*) without taking a hard dependency here.
- All writes are transactional; helpers expose simple CRUD and domain actions.

This file is deliberately self-contained and safe to import early in the
bring-up sequence.

Example
-------
    db = AICFStateDB("file:aicf.db?mode=rwc")
    with db.tx():
        db.upsert_provider({
            "provider_id": "prov_01",
            "name": "Example",
            "caps": {"ai": True, "quantum": False},
            "status": "active",
            "endpoints": {"rpc": "https://...", "proof_post": "https://..."},
            "stake": 0,
        })

    job_id = "job_01"
    with db.tx():
        db.enqueue_job({
            "job_id": job_id,
            "kind": "AI",
            "requester": "anim1xyz...",
            "units": 1024,
            "fee": 10_000,
            "payload_hash": "0xabc...",
            "meta": {"model": "tiny-ggml", "prompt_len": 200},
        })
        db.assign_job(job_id, provider_id="prov_01", lease_secs=600)

    job = db.get_job(job_id)
"""

import contextlib
import json
import os
import sqlite3
import threading
import time
from dataclasses import asdict, is_dataclass
from typing import (Any, Dict, Iterable, Iterator, List, Mapping,
                    MutableMapping, Optional, Tuple)

# ---- Errors -----------------------------------------------------------------


class AICFStateError(RuntimeError):
    """Base error for AICF state DB."""


class NotFound(AICFStateError):
    """Requested record not found."""


class Conflict(AICFStateError):
    """State conflict such as duplicate job id, double assignment, etc."""


# ---- Utilities ---------------------------------------------------------------


def _now_s() -> int:
    return int(time.time())


def _to_json_blob(obj: Any) -> bytes:
    if is_dataclass(obj):
        obj = asdict(obj)
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _from_json_blob(blob: Optional[bytes]) -> Any:
    if not blob:
        return None
    return json.loads(blob.decode("utf-8"))


# ---- Main adapter ------------------------------------------------------------


class AICFStateDB:
    """
    Tiny SQLite adapter for AICF.

    Thread-safe for simple concurrent access via an internal RLock. For high
    concurrency, open separate connections per thread or process.
    """

    SCHEMA_VERSION = 1

    def __init__(self, path: str) -> None:
        """
        Open or create the SQLite database.

        `path` may be a filesystem path or a URI (e.g. "file:aicf.db?mode=rwc").
        """
        uri = path.startswith("file:")
        self._db = sqlite3.connect(
            path,
            uri=uri,
            check_same_thread=False,
            isolation_level=None,  # autocommit; we'll manage transactions
            detect_types=sqlite3.PARSE_DECLTYPES,
        )
        self._db.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._apply_pragmas()
        with self.tx():
            self._migrate()

    # -- lifecycle -------------------------------------------------------------

    def close(self) -> None:
        with self._lock:
            self._db.close()

    @contextlib.contextmanager
    def tx(self) -> Iterator[None]:
        """
        Transaction context manager.

        Usage:
            with db.tx():
                db.do_write(...)
        """
        with self._lock:
            try:
                self._db.execute("BEGIN IMMEDIATE")
                yield
                self._db.execute("COMMIT")
            except Exception:
                try:
                    self._db.execute("ROLLBACK")
                finally:
                    raise

    def _apply_pragmas(self) -> None:
        cur = self._db.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA temp_store=MEMORY")
        cur.execute("PRAGMA mmap_size=268435456")  # 256 MiB if available
        cur.close()

    # -- schema & migrations ---------------------------------------------------

    def _migrate(self) -> None:
        cur = self._db.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        # Ensure version row
        cur.execute("SELECT value FROM meta WHERE key='schema_version'")
        row = cur.fetchone()
        if not row:
            cur.execute(
                "INSERT INTO meta(key, value) VALUES('schema_version', ?)",
                (str(self.SCHEMA_VERSION),),
            )
        # Create core tables (idempotent)
        cur.executescript(
            """
            CREATE TABLE IF NOT EXISTS providers (
                provider_id TEXT PRIMARY KEY,
                status      TEXT NOT NULL,
                caps_ai     INTEGER NOT NULL DEFAULT 0,
                caps_quant  INTEGER NOT NULL DEFAULT 0,
                stake       INTEGER NOT NULL DEFAULT 0,
                balance     INTEGER NOT NULL DEFAULT 0,
                escrow      INTEGER NOT NULL DEFAULT 0,
                endpoints   TEXT,
                info_json   BLOB,
                created_at  INTEGER NOT NULL,
                updated_at  INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_providers_status
                ON providers(status);
            CREATE INDEX IF NOT EXISTS idx_providers_caps
                ON providers(caps_ai, caps_quant);

            CREATE TABLE IF NOT EXISTS jobs (
                job_id        TEXT PRIMARY KEY,
                kind          TEXT NOT NULL,        -- 'AI' | 'QUANTUM'
                status        TEXT NOT NULL,        -- 'queued' | 'assigned' | 'completed' | 'expired' | 'failed'
                requester     TEXT NOT NULL,
                provider_id   TEXT,                 -- nullable until assigned
                units         INTEGER NOT NULL,
                fee           INTEGER NOT NULL,
                priority      REAL NOT NULL DEFAULT 0.0,
                payload_hash  TEXT NOT NULL,
                meta_json     BLOB,
                enqueued_at   INTEGER NOT NULL,
                updated_at    INTEGER NOT NULL,
                FOREIGN KEY(provider_id) REFERENCES providers(provider_id) ON DELETE SET NULL
            );
            CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
            CREATE INDEX IF NOT EXISTS idx_jobs_provider ON jobs(provider_id);
            CREATE INDEX IF NOT EXISTS idx_jobs_priority ON jobs(priority);

            CREATE TABLE IF NOT EXISTS leases (
                lease_id     TEXT PRIMARY KEY,
                job_id       TEXT NOT NULL UNIQUE,
                provider_id  TEXT NOT NULL,
                issued_at    INTEGER NOT NULL,
                expires_at   INTEGER NOT NULL,
                renewed      INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY(job_id) REFERENCES jobs(job_id) ON DELETE CASCADE,
                FOREIGN KEY(provider_id) REFERENCES providers(provider_id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_leases_provider ON leases(provider_id);
            CREATE INDEX IF NOT EXISTS idx_leases_expiry ON leases(expires_at);

            CREATE TABLE IF NOT EXISTS payouts (
                payout_id    TEXT PRIMARY KEY,
                provider_id  TEXT NOT NULL,
                job_id       TEXT,
                epoch        INTEGER NOT NULL,
                amount       INTEGER NOT NULL,
                status       TEXT NOT NULL,        -- 'pending' | 'settled' | 'failed'
                created_at   INTEGER NOT NULL,
                settled_at   INTEGER,
                meta_json    BLOB,
                FOREIGN KEY(provider_id) REFERENCES providers(provider_id) ON DELETE CASCADE,
                FOREIGN KEY(job_id) REFERENCES jobs(job_id) ON DELETE SET NULL
            );
            CREATE INDEX IF NOT EXISTS idx_payouts_provider ON payouts(provider_id);
            CREATE INDEX IF NOT EXISTS idx_payouts_status ON payouts(status);
            """
        )
        cur.close()

    # ---- providers -----------------------------------------------------------

    def upsert_provider(self, provider: Mapping[str, Any]) -> None:
        """
        Insert or update a provider.

        Expected keys (extra keys allowed and stored in info_json):
            - provider_id: str (required)
            - status: str
            - caps: {"ai": bool, "quantum": bool}
            - stake: int
            - balance: int (optional)
            - escrow: int (optional)
            - endpoints: Mapping[str, Any] (optional)
        """
        p_id = str(provider["provider_id"])
        status = str(provider.get("status", "active"))
        caps = provider.get("caps", {})
        caps_ai = 1 if bool(caps.get("ai", False)) else 0
        caps_quant = 1 if bool(caps.get("quantum", False)) else 0
        stake = int(provider.get("stake", 0))
        balance = int(provider.get("balance", 0))
        escrow = int(provider.get("escrow", 0))
        endpoints = _to_json_blob(provider.get("endpoints") or {})
        info_json = _to_json_blob(provider)
        now = _now_s()

        self._db.execute(
            """
            INSERT INTO providers(provider_id,status,caps_ai,caps_quant,stake,balance,escrow,endpoints,info_json,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(provider_id) DO UPDATE SET
                status=excluded.status,
                caps_ai=excluded.caps_ai,
                caps_quant=excluded.caps_quant,
                stake=excluded.stake,
                balance=excluded.balance,
                escrow=excluded.escrow,
                endpoints=excluded.endpoints,
                info_json=excluded.info_json,
                updated_at=excluded.updated_at
            """,
            (
                p_id,
                status,
                caps_ai,
                caps_quant,
                stake,
                balance,
                escrow,
                endpoints,
                info_json,
                now,
                now,
            ),
        )

    def get_provider(self, provider_id: str) -> Dict[str, Any]:
        row = self._db.execute(
            "SELECT * FROM providers WHERE provider_id=?", (provider_id,)
        ).fetchone()
        if not row:
            raise NotFound(f"provider {provider_id} not found")
        return self._row_provider(row)

    def list_providers(
        self,
        *,
        status: Optional[str] = None,
        ai: Optional[bool] = None,
        quantum: Optional[bool] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        sql = "SELECT * FROM providers WHERE 1=1"
        args: List[Any] = []
        if status:
            sql += " AND status=?"
            args.append(status)
        if ai is not None:
            sql += " AND caps_ai=?"
            args.append(1 if ai else 0)
        if quantum is not None:
            sql += " AND caps_quant=?"
            args.append(1 if quantum else 0)
        sql += " ORDER BY updated_at DESC LIMIT ? OFFSET ?"
        args.extend([int(limit), int(offset)])
        rows = self._db.execute(sql, args).fetchall()
        return [self._row_provider(r) for r in rows]

    def set_provider_status(self, provider_id: str, status: str) -> None:
        now = _now_s()
        cur = self._db.execute(
            "UPDATE providers SET status=?, updated_at=? WHERE provider_id=?",
            (status, now, provider_id),
        )
        if cur.rowcount == 0:
            raise NotFound(f"provider {provider_id} not found")

    def adjust_stake(self, provider_id: str, delta: int) -> int:
        """Add (or subtract) stake, returning the new stake."""
        now = _now_s()
        self._db.execute(
            "UPDATE providers SET stake=stake+? WHERE provider_id=?",
            (int(delta), provider_id),
        )
        cur = self._db.execute(
            "UPDATE providers SET updated_at=? WHERE provider_id=? RETURNING stake",
            (now, provider_id),
        )
        row = cur.fetchone()
        if not row:
            raise NotFound(f"provider {provider_id} not found")
        return int(row["stake"])

    def credit_balance(
        self, provider_id: str, delta: int, *, escrow: bool = False
    ) -> Tuple[int, int]:
        """
        Credit/debit provider balances. Returns (balance, escrow).
        """
        col = "escrow" if escrow else "balance"
        self._db.execute(
            f"UPDATE providers SET {col}={col}+? WHERE provider_id=?",
            (int(delta), provider_id),
        )
        row = self._db.execute(
            "SELECT balance, escrow FROM providers WHERE provider_id=?",
            (provider_id,),
        ).fetchone()
        if not row:
            raise NotFound(f"provider {provider_id} not found")
        return int(row["balance"]), int(row["escrow"])

    # ---- jobs & leases -------------------------------------------------------

    def enqueue_job(self, job: Mapping[str, Any]) -> None:
        """
        Insert a new job in 'queued' status.

        Required keys:
            - job_id: str
            - kind: 'AI' | 'QUANTUM'
            - requester: bech32 / addr str
            - units: int
            - fee: int
            - payload_hash: str
        Optional:
            - priority: float
            - meta: Mapping
        """
        job_id = str(job["job_id"])
        kind = str(job["kind"])
        requester = str(job["requester"])
        units = int(job["units"])
        fee = int(job["fee"])
        payload_hash = str(job["payload_hash"])
        priority = float(job.get("priority", 0.0))
        meta_json = _to_json_blob(job.get("meta") or job)

        now = _now_s()
        try:
            self._db.execute(
                """
                INSERT INTO jobs(job_id,kind,status,requester,provider_id,units,fee,priority,payload_hash,meta_json,enqueued_at,updated_at)
                VALUES(?,?,?,?,NULL,?,?,?,?,?,?,?)
                """,
                (
                    job_id,
                    kind,
                    "queued",
                    requester,
                    units,
                    fee,
                    priority,
                    payload_hash,
                    meta_json,
                    now,
                    now,
                ),
            )
        except sqlite3.IntegrityError as e:
            raise Conflict(f"job {job_id} already exists") from e

    def get_job(self, job_id: str) -> Dict[str, Any]:
        row = self._db.execute(
            "SELECT * FROM jobs WHERE job_id=?", (job_id,)
        ).fetchone()
        if not row:
            raise NotFound(f"job {job_id} not found")
        return self._row_job(row)

    def list_jobs(
        self,
        *,
        status: Optional[str] = None,
        provider_id: Optional[str] = None,
        kind: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        sql = "SELECT * FROM jobs WHERE 1=1"
        args: List[Any] = []
        if status:
            sql += " AND status=?"
            args.append(status)
        if provider_id:
            sql += " AND provider_id=?"
            args.append(provider_id)
        if kind:
            sql += " AND kind=?"
            args.append(kind)
        sql += " ORDER BY priority DESC, enqueued_at ASC LIMIT ? OFFSET ?"
        args.extend([int(limit), int(offset)])
        rows = self._db.execute(sql, args).fetchall()
        return [self._row_job(r) for r in rows]

    def assign_job(self, job_id: str, *, provider_id: str, lease_secs: int) -> str:
        """
        Assign a queued job to a provider and create a lease.

        Returns the lease_id.
        """
        now = _now_s()
        # Sanity: job must be queued
        row = self._db.execute(
            "SELECT status FROM jobs WHERE job_id=?", (job_id,)
        ).fetchone()
        if not row:
            raise NotFound(f"job {job_id} not found")
        if row["status"] != "queued":
            raise Conflict(f"job {job_id} not in 'queued' status (got {row['status']})")

        lease_id = f"lease_{job_id}"
        expires_at = now + int(lease_secs)
        try:
            self._db.execute(
                """
                INSERT INTO leases(lease_id,job_id,provider_id,issued_at,expires_at,renewed)
                VALUES(?,?,?,?,?,0)
                """,
                (lease_id, job_id, provider_id, now, expires_at),
            )
        except sqlite3.IntegrityError as e:
            raise Conflict(f"job {job_id} already leased") from e

        self._db.execute(
            "UPDATE jobs SET status='assigned', provider_id=?, updated_at=? WHERE job_id=?",
            (provider_id, now, job_id),
        )
        return lease_id

    def renew_lease(self, lease_id: str, extend_secs: int) -> None:
        row = self._db.execute(
            "SELECT expires_at FROM leases WHERE lease_id=?", (lease_id,)
        ).fetchone()
        if not row:
            raise NotFound(f"lease {lease_id} not found")
        new_exp = int(row["expires_at"]) + int(extend_secs)
        self._db.execute(
            "UPDATE leases SET expires_at=?, renewed=renewed+1 WHERE lease_id=?",
            (new_exp, lease_id),
        )

    def expire_overdue_leases(self, *, now: Optional[int] = None) -> int:
        """
        Mark jobs with expired leases as 'expired' and remove leases.

        Returns count of expired jobs.
        """
        now_s = _now_s() if now is None else int(now)
        rows = self._db.execute(
            "SELECT job_id FROM leases WHERE expires_at < ?", (now_s,)
        ).fetchall()
        job_ids = [r["job_id"] for r in rows]
        if not job_ids:
            return 0
        q_marks = ",".join(["?"] * len(job_ids))
        self._db.execute(f"DELETE FROM leases WHERE job_id IN ({q_marks})", job_ids)
        self._db.execute(
            f"UPDATE jobs SET status='expired', updated_at=? WHERE job_id IN ({q_marks})",
            (now_s, *job_ids),
        )
        return len(job_ids)

    def complete_job(self, job_id: str) -> None:
        now = _now_s()
        self._db.execute("DELETE FROM leases WHERE job_id=?", (job_id,))
        cur = self._db.execute(
            "UPDATE jobs SET status='completed', updated_at=? WHERE job_id=?",
            (now, job_id),
        )
        if cur.rowcount == 0:
            raise NotFound(f"job {job_id} not found")

    def fail_job(self, job_id: str, *, reason: Optional[str] = None) -> None:
        now = _now_s()
        self._db.execute("DELETE FROM leases WHERE job_id=?", (job_id,))
        cur = self._db.execute(
            "UPDATE jobs SET status='failed', updated_at=? WHERE job_id=?",
            (now, job_id),
        )
        if cur.rowcount == 0:
            raise NotFound(f"job {job_id} not found")
        if reason:
            # Append reason into meta_json
            row = self._db.execute(
                "SELECT meta_json FROM jobs WHERE job_id=?", (job_id,)
            ).fetchone()
            meta = _from_json_blob(row["meta_json"]) or {}
            meta = dict(meta)
            meta["failure_reason"] = reason
            self._db.execute(
                "UPDATE jobs SET meta_json=?, updated_at=? WHERE job_id=?",
                (_to_json_blob(meta), now, job_id),
            )

    # ---- payouts & balances --------------------------------------------------

    def create_payout(
        self,
        *,
        payout_id: str,
        provider_id: str,
        job_id: Optional[str],
        epoch: int,
        amount: int,
        meta: Optional[Mapping[str, Any]] = None,
    ) -> None:
        now = _now_s()
        self._db.execute(
            """
            INSERT INTO payouts(payout_id,provider_id,job_id,epoch,amount,status,created_at,settled_at,meta_json)
            VALUES(?,?,?,?,?,'pending',?,NULL,?)
            """,
            (
                payout_id,
                provider_id,
                job_id,
                int(epoch),
                int(amount),
                now,
                _to_json_blob(meta or {}),
            ),
        )

    def list_payouts(
        self,
        *,
        provider_id: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        sql = "SELECT * FROM payouts WHERE 1=1"
        args: List[Any] = []
        if provider_id:
            sql += " AND provider_id=?"
            args.append(provider_id)
        if status:
            sql += " AND status=?"
            args.append(status)
        sql += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        args.extend([int(limit), int(offset)])
        rows = self._db.execute(sql, args).fetchall()
        return [self._row_payout(r) for r in rows]

    def settle_payout(self, payout_id: str) -> None:
        now = _now_s()
        cur = self._db.execute(
            "UPDATE payouts SET status='settled', settled_at=? WHERE payout_id=? AND status='pending'",
            (now, payout_id),
        )
        if cur.rowcount == 0:
            raise NotFound(f"payout {payout_id} not pending or not found")

    # ---- rows → dicts --------------------------------------------------------

    def _row_provider(self, row: sqlite3.Row) -> Dict[str, Any]:
        return {
            "provider_id": row["provider_id"],
            "status": row["status"],
            "caps": {"ai": bool(row["caps_ai"]), "quantum": bool(row["caps_quant"])},
            "stake": int(row["stake"]),
            "balance": int(row["balance"]),
            "escrow": int(row["escrow"]),
            "endpoints": _from_json_blob(row["endpoints"]),
            "info": _from_json_blob(row["info_json"]),
            "created_at": int(row["created_at"]),
            "updated_at": int(row["updated_at"]),
        }

    def _row_job(self, row: sqlite3.Row) -> Dict[str, Any]:
        return {
            "job_id": row["job_id"],
            "kind": row["kind"],
            "status": row["status"],
            "requester": row["requester"],
            "provider_id": row["provider_id"],
            "units": int(row["units"]),
            "fee": int(row["fee"]),
            "priority": float(row["priority"]),
            "payload_hash": row["payload_hash"],
            "meta": _from_json_blob(row["meta_json"]),
            "enqueued_at": int(row["enqueued_at"]),
            "updated_at": int(row["updated_at"]),
        }

    def _row_payout(self, row: sqlite3.Row) -> Dict[str, Any]:
        return {
            "payout_id": row["payout_id"],
            "provider_id": row["provider_id"],
            "job_id": row["job_id"],
            "epoch": int(row["epoch"]),
            "amount": int(row["amount"]),
            "status": row["status"],
            "created_at": int(row["created_at"]),
            "settled_at": (
                int(row["settled_at"]) if row["settled_at"] is not None else None
            ),
            "meta": _from_json_blob(row["meta_json"]),
        }


# Convenience: open via env var if present (useful for quick REPLs)
def open_default() -> AICFStateDB:
    """
    Open AICFStateDB at the path from AICF_DB (default: ./aicf.db).
    """
    path = os.environ.get("AICF_DB", "aicf.db")
    return AICFStateDB(path)


__all__ = [
    "AICFStateDB",
    "AICFStateError",
    "NotFound",
    "Conflict",
    "open_default",
]
