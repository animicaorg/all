"""
animica.ena.store
=================

SQLite-backed persistence for ENA. Holds sessions/traces, retrieval chunks +
index metadata, useful-work jobs + receipts, dataset registrations, training
runs + eval reports, and agent memory.

Records that are intrinsically document-shaped (jobs, receipts, runs, evals)
are stored as canonical-JSON blobs with a few promoted columns for querying;
retrieval rows (chunks, indexes, memory) get real columns so search can scan
them without deserializing everything.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterable, Optional

from .errors import StoreError

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    created_at INTEGER NOT NULL,
    data       TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS traces (
    trace_id   TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    data       TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS indexes (
    name             TEXT PRIMARY KEY,
    embedding_provider TEXT NOT NULL,
    embedding_model  TEXT NOT NULL,
    dimensions       INTEGER NOT NULL,
    chunk_count      INTEGER NOT NULL DEFAULT 0,
    created_at       INTEGER NOT NULL,
    data             TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chunks (
    chunk_id    TEXT PRIMARY KEY,
    index_name  TEXT NOT NULL,
    source      TEXT NOT NULL,
    ordinal     INTEGER NOT NULL,
    text        TEXT NOT NULL,
    text_hash   TEXT NOT NULL,
    embedding   TEXT,
    embedding_dim INTEGER NOT NULL DEFAULT 0,
    metadata    TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_chunks_index ON chunks(index_name);
CREATE TABLE IF NOT EXISTS jobs (
    job_id      TEXT PRIMARY KEY,
    job_hash    TEXT NOT NULL,
    job_type    TEXT NOT NULL,
    status      TEXT NOT NULL,
    worker_id   TEXT,
    aicf_task_id TEXT NOT NULL,
    created_at  INTEGER NOT NULL,
    updated_at  INTEGER NOT NULL,
    data        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_type ON jobs(job_type);
CREATE TABLE IF NOT EXISTS receipts (
    receipt_id  TEXT PRIMARY KEY,
    job_id      TEXT NOT NULL,
    receipt_hash TEXT NOT NULL,
    created_at  INTEGER NOT NULL,
    data        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_receipts_job ON receipts(job_id);
CREATE TABLE IF NOT EXISTS datasets (
    dataset_id  TEXT PRIMARY KEY,
    kind        TEXT NOT NULL,
    path        TEXT NOT NULL,
    sha256      TEXT NOT NULL,
    row_count   INTEGER NOT NULL DEFAULT 0,
    created_at  INTEGER NOT NULL,
    data        TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS training_runs (
    run_id      TEXT PRIMARY KEY,
    status      TEXT NOT NULL,
    backend     TEXT NOT NULL,
    base_model  TEXT NOT NULL,
    created_at  INTEGER NOT NULL,
    updated_at  INTEGER NOT NULL,
    data        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_runs_status ON training_runs(status);
CREATE TABLE IF NOT EXISTS evals (
    eval_id     TEXT PRIMARY KEY,
    run_id      TEXT,
    created_at  INTEGER NOT NULL,
    data        TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS memory (
    memory_id   TEXT PRIMARY KEY,
    text        TEXT NOT NULL,
    source      TEXT,
    created_at  INTEGER NOT NULL,
    metadata    TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS payments (
    txid        TEXT PRIMARY KEY,
    job_id      TEXT NOT NULL,
    value_nano  INTEGER NOT NULL,
    from_addr   TEXT,
    status      TEXT NOT NULL,
    created_at  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_payments_job ON payments(job_id);
CREATE TABLE IF NOT EXISTS wallet_connect (
    request_id  TEXT PRIMARY KEY,
    nonce       TEXT NOT NULL,
    status      TEXT NOT NULL,
    address     TEXT,
    accounts    TEXT NOT NULL DEFAULT '[]',
    created_at  INTEGER NOT NULL,
    expires_at  INTEGER NOT NULL
);
"""


class Store:
    """Thread-safe (serialized) SQLite wrapper for ENA state."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        try:
            self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        except sqlite3.Error as exc:  # pragma: no cover
            raise StoreError(f"cannot open ENA db at {self.db_path}: {exc}") from exc
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA foreign_keys=ON;")
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    # -- low-level --------------------------------------------------------
    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _exec(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._conn.execute(sql, tuple(params))
            self._conn.commit()
            return cur

    def _query(self, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return list(self._conn.execute(sql, tuple(params)).fetchall())

    # -- jobs -------------------------------------------------------------
    def upsert_job(self, job: dict[str, Any]) -> None:
        self._exec(
            """INSERT INTO jobs
               (job_id, job_hash, job_type, status, worker_id, aicf_task_id,
                created_at, updated_at, data)
               VALUES (?,?,?,?,?,?,?,?,?)
               ON CONFLICT(job_id) DO UPDATE SET
                 job_hash=excluded.job_hash, job_type=excluded.job_type,
                 status=excluded.status, worker_id=excluded.worker_id,
                 aicf_task_id=excluded.aicf_task_id,
                 updated_at=excluded.updated_at, data=excluded.data""",
            (job["job_id"], job["job_hash"], job["job_type"], job["status"],
             job.get("worker_id"), job["aicf_task_id"], job["created_at"],
             job["updated_at"], json.dumps(job)),
        )

    def get_job(self, job_id: str) -> Optional[dict[str, Any]]:
        rows = self._query("SELECT data FROM jobs WHERE job_id=?", (job_id,))
        return json.loads(rows[0]["data"]) if rows else None

    def list_jobs(self, status: Optional[str] = None,
                  job_type: Optional[str] = None,
                  worker_id: Optional[str] = None,
                  limit: int = 200) -> list[dict[str, Any]]:
        clauses, params = [], []
        if status:
            clauses.append("status=?"); params.append(status)
        if job_type:
            clauses.append("job_type=?"); params.append(job_type)
        if worker_id:
            clauses.append("worker_id=?"); params.append(worker_id)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        rows = self._query(
            f"SELECT data FROM jobs{where} ORDER BY created_at ASC LIMIT ?",
            (*params, limit))
        return [json.loads(r["data"]) for r in rows]

    def claim_one_job(self, worker_id: str, job_types: Optional[list[str]],
                      claimed_status: str = "claimed") -> Optional[dict[str, Any]]:
        """Atomically move the oldest ``proposed`` job to ``claimed``."""
        with self._lock:
            clauses = ["status='proposed'"]
            params: list[Any] = []
            if job_types:
                placeholders = ",".join("?" for _ in job_types)
                clauses.append(f"job_type IN ({placeholders})")
                params.extend(job_types)
            row = self._conn.execute(
                f"SELECT data FROM jobs WHERE {' AND '.join(clauses)} "
                f"ORDER BY created_at ASC LIMIT 1", tuple(params)).fetchone()
            if not row:
                return None
            job = json.loads(row["data"])
            job["status"] = claimed_status
            job["worker_id"] = worker_id
            from .models import now_ts
            job["updated_at"] = now_ts()
            self._conn.execute(
                "UPDATE jobs SET status=?, worker_id=?, updated_at=?, data=? "
                "WHERE job_id=? AND status='proposed'",
                (job["status"], worker_id, job["updated_at"],
                 json.dumps(job), job["job_id"]))
            self._conn.commit()
            return job

    # -- receipts ---------------------------------------------------------
    def upsert_receipt(self, receipt: dict[str, Any]) -> None:
        self._exec(
            """INSERT INTO receipts (receipt_id, job_id, receipt_hash, created_at, data)
               VALUES (?,?,?,?,?)
               ON CONFLICT(receipt_id) DO UPDATE SET
                 receipt_hash=excluded.receipt_hash, data=excluded.data""",
            (receipt["receipt_id"], receipt["job_id"], receipt["receipt_hash"],
             receipt.get("created_at", 0), json.dumps(receipt)),
        )

    def get_receipt_for_job(self, job_id: str) -> Optional[dict[str, Any]]:
        rows = self._query(
            "SELECT data FROM receipts WHERE job_id=? ORDER BY created_at DESC LIMIT 1",
            (job_id,))
        return json.loads(rows[0]["data"]) if rows else None

    # -- indexes / chunks -------------------------------------------------
    def upsert_index(self, meta: dict[str, Any]) -> None:
        self._exec(
            """INSERT INTO indexes
               (name, embedding_provider, embedding_model, dimensions,
                chunk_count, created_at, data)
               VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(name) DO UPDATE SET
                 embedding_provider=excluded.embedding_provider,
                 embedding_model=excluded.embedding_model,
                 dimensions=excluded.dimensions,
                 chunk_count=excluded.chunk_count, data=excluded.data""",
            (meta["name"], meta["embedding_provider"], meta["embedding_model"],
             meta["dimensions"], meta.get("chunk_count", 0),
             meta.get("created_at", 0), json.dumps(meta)),
        )

    def get_index(self, name: str) -> Optional[dict[str, Any]]:
        rows = self._query("SELECT data FROM indexes WHERE name=?", (name,))
        return json.loads(rows[0]["data"]) if rows else None

    def list_indexes(self) -> list[dict[str, Any]]:
        return [json.loads(r["data"]) for r in
                self._query("SELECT data FROM indexes ORDER BY created_at DESC")]

    def add_chunks(self, chunks: list[dict[str, Any]]) -> None:
        with self._lock:
            self._conn.executemany(
                """INSERT OR REPLACE INTO chunks
                   (chunk_id, index_name, source, ordinal, text, text_hash,
                    embedding, embedding_dim, metadata)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                [(c["chunk_id"], c["index_name"], c["source"], c["ordinal"],
                  c["text"], c["text_hash"],
                  json.dumps(c["embedding"]) if c.get("embedding") is not None else None,
                  c.get("embedding_dim", 0), json.dumps(c.get("metadata", {})))
                 for c in chunks])
            self._conn.commit()

    def iter_chunks(self, index_name: Optional[str] = None) -> list[dict[str, Any]]:
        if index_name:
            rows = self._query("SELECT * FROM chunks WHERE index_name=? ORDER BY ordinal",
                               (index_name,))
        else:
            rows = self._query("SELECT * FROM chunks ORDER BY index_name, ordinal")
        out = []
        for r in rows:
            out.append({
                "chunk_id": r["chunk_id"], "index_name": r["index_name"],
                "source": r["source"], "ordinal": r["ordinal"], "text": r["text"],
                "text_hash": r["text_hash"], "embedding_dim": r["embedding_dim"],
                "embedding": json.loads(r["embedding"]) if r["embedding"] else None,
                "metadata": json.loads(r["metadata"] or "{}"),
            })
        return out

    # -- datasets ---------------------------------------------------------
    def upsert_dataset(self, ds: dict[str, Any]) -> None:
        self._exec(
            """INSERT INTO datasets
               (dataset_id, kind, path, sha256, row_count, created_at, data)
               VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(dataset_id) DO UPDATE SET
                 kind=excluded.kind, path=excluded.path, sha256=excluded.sha256,
                 row_count=excluded.row_count, data=excluded.data""",
            (ds["dataset_id"], ds["kind"], ds["path"], ds["sha256"],
             ds.get("row_count", 0), ds.get("created_at", 0), json.dumps(ds)),
        )

    def list_datasets(self) -> list[dict[str, Any]]:
        return [json.loads(r["data"]) for r in
                self._query("SELECT data FROM datasets ORDER BY created_at DESC")]

    # -- training runs ----------------------------------------------------
    def upsert_run(self, run: dict[str, Any]) -> None:
        self._exec(
            """INSERT INTO training_runs
               (run_id, status, backend, base_model, created_at, updated_at, data)
               VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(run_id) DO UPDATE SET
                 status=excluded.status, backend=excluded.backend,
                 base_model=excluded.base_model, updated_at=excluded.updated_at,
                 data=excluded.data""",
            (run["run_id"], run["status"], run["backend"], run["base_model"],
             run.get("created_at", 0), run.get("updated_at", 0), json.dumps(run)),
        )

    def get_run(self, run_id: str) -> Optional[dict[str, Any]]:
        rows = self._query("SELECT data FROM training_runs WHERE run_id=?", (run_id,))
        return json.loads(rows[0]["data"]) if rows else None

    def list_runs(self, limit: int = 200) -> list[dict[str, Any]]:
        return [json.loads(r["data"]) for r in self._query(
            "SELECT data FROM training_runs ORDER BY created_at DESC LIMIT ?", (limit,))]

    # -- evals ------------------------------------------------------------
    def add_eval(self, ev: dict[str, Any]) -> None:
        self._exec(
            "INSERT OR REPLACE INTO evals (eval_id, run_id, created_at, data) "
            "VALUES (?,?,?,?)",
            (ev["eval_id"], ev.get("run_id"), ev.get("created_at", 0), json.dumps(ev)),
        )

    # -- memory -----------------------------------------------------------
    def add_memory(self, mem: dict[str, Any]) -> None:
        self._exec(
            "INSERT OR REPLACE INTO memory (memory_id, text, source, created_at, metadata) "
            "VALUES (?,?,?,?,?)",
            (mem["memory_id"], mem["text"], mem.get("source"),
             mem.get("created_at", 0), json.dumps(mem.get("metadata", {}))),
        )

    def query_memory(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        like = f"%{query}%"
        rows = self._query(
            "SELECT memory_id, text, source, created_at FROM memory "
            "WHERE text LIKE ? ORDER BY created_at DESC LIMIT ?", (like, limit))
        return [dict(r) for r in rows]

    # -- aggregate stats (for dashboards) ---------------------------------
    def stats(self) -> dict[str, Any]:
        def counts(sql: str) -> dict[str, int]:
            return {str(r[0]): int(r[1]) for r in self._query(sql)}

        def scalar(sql: str) -> int:
            rows = self._query(sql)
            return int(rows[0][0] or 0) if rows else 0

        jobs_by_status = counts("SELECT status, COUNT(*) FROM jobs GROUP BY status")
        jobs_by_type = counts("SELECT job_type, COUNT(*) FROM jobs GROUP BY job_type")
        runs_by_status = counts(
            "SELECT status, COUNT(*) FROM training_runs GROUP BY status")
        contributors = scalar(
            "SELECT COUNT(DISTINCT worker_id) FROM jobs WHERE worker_id IS NOT NULL")
        models = scalar("SELECT COUNT(DISTINCT base_model) FROM training_runs")
        return {
            "jobs_total": sum(jobs_by_status.values()),
            "jobs_by_status": jobs_by_status,
            "jobs_by_type": jobs_by_type,
            "jobs_verified": jobs_by_status.get("verified", 0),
            "receipts_total": scalar("SELECT COUNT(*) FROM receipts"),
            "training_runs_total": sum(runs_by_status.values()),
            "training_runs_by_status": runs_by_status,
            "indexes_total": scalar("SELECT COUNT(*) FROM indexes"),
            "chunks_total": scalar("SELECT COUNT(*) FROM chunks"),
            "datasets_total": scalar("SELECT COUNT(*) FROM datasets"),
            "contributors": contributors,
            "distinct_models": models,
            "evals_total": scalar("SELECT COUNT(*) FROM evals"),
        }

    def recent_jobs(self, limit: int = 10) -> list[dict[str, Any]]:
        rows = self._query(
            "SELECT job_id, job_type, status, worker_id, updated_at FROM jobs "
            "ORDER BY updated_at DESC LIMIT ?", (limit,))
        return [dict(r) for r in rows]

    def leaderboard(self, limit: int = 10) -> list[dict[str, Any]]:
        rows = self._query(
            "SELECT worker_id, COUNT(*) AS jobs FROM jobs "
            "WHERE worker_id IS NOT NULL AND status IN ('completed','verified') "
            "GROUP BY worker_id ORDER BY jobs DESC LIMIT ?", (limit,))
        return [{"worker_id": r["worker_id"], "jobs": int(r["jobs"])} for r in rows]

    # -- payments (demand side) -------------------------------------------
    def payment_seen(self, txid: str) -> bool:
        return bool(self._query("SELECT 1 FROM payments WHERE txid=?", (txid,)))

    def record_payment(self, pmt: dict[str, Any]) -> bool:
        """Insert a payment; returns False if the txid was already used."""
        with self._lock:
            try:
                self._conn.execute(
                    "INSERT INTO payments (txid, job_id, value_nano, from_addr, status, created_at) "
                    "VALUES (?,?,?,?,?,?)",
                    (pmt["txid"], pmt["job_id"], int(pmt["value_nano"]),
                     pmt.get("from_addr"), pmt.get("status", "verified"),
                     pmt.get("created_at", 0)))
                self._conn.commit()
                return True
            except sqlite3.IntegrityError:
                return False

    def payment_for_job(self, job_id: str) -> Optional[dict[str, Any]]:
        rows = self._query(
            "SELECT txid, value_nano, from_addr, status, created_at FROM payments "
            "WHERE job_id=? ORDER BY created_at DESC LIMIT 1", (job_id,))
        return dict(rows[0]) if rows else None

    def total_funded_nano(self) -> int:
        rows = self._query("SELECT COALESCE(SUM(value_nano),0) FROM payments")
        return int(rows[0][0] or 0) if rows else 0

    # -- wallet-connect (web/mobile wallet handshake) ---------------------
    def create_wc(self, wc: dict[str, Any]) -> None:
        self._exec(
            "INSERT OR REPLACE INTO wallet_connect "
            "(request_id, nonce, status, address, accounts, created_at, expires_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (wc["request_id"], wc["nonce"], wc["status"], wc.get("address"),
             json.dumps(wc.get("accounts", [])), wc["created_at"], wc["expires_at"]))

    def get_wc(self, request_id: str) -> Optional[dict[str, Any]]:
        rows = self._query(
            "SELECT request_id, nonce, status, address, accounts, created_at, expires_at "
            "FROM wallet_connect WHERE request_id=?", (request_id,))
        if not rows:
            return None
        r = rows[0]
        return {"request_id": r["request_id"], "nonce": r["nonce"], "status": r["status"],
                "address": r["address"], "accounts": json.loads(r["accounts"] or "[]"),
                "created_at": r["created_at"], "expires_at": r["expires_at"]}

    def set_wc_result(self, request_id: str, status: str, address: Optional[str],
                      accounts: list[str]) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE wallet_connect SET status=?, address=?, accounts=? "
                "WHERE request_id=? AND status='pending'",
                (status, address, json.dumps(accounts), request_id))
            self._conn.commit()
            return cur.rowcount > 0

    # -- sessions / traces ------------------------------------------------
    def save_session(self, session: dict[str, Any]) -> None:
        self._exec(
            "INSERT OR REPLACE INTO sessions (session_id, created_at, data) VALUES (?,?,?)",
            (session["session_id"], session.get("created_at", 0), json.dumps(session)),
        )

    def add_trace(self, trace: dict[str, Any]) -> None:
        self._exec(
            "INSERT OR REPLACE INTO traces (trace_id, session_id, created_at, data) "
            "VALUES (?,?,?,?)",
            (trace["trace_id"], trace["session_id"], trace.get("created_at", 0),
             json.dumps(trace)),
        )
