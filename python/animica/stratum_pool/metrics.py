from __future__ import annotations

import json
import sqlite3
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any, Deque, Dict, List, Optional, Tuple

from mining.stratum_server import Session, StratumJob, StratumServer

from .config import PoolConfig
from .job_manager import JobManager

ShareEvent = Dict[str, object]
AccountingEvent = Dict[str, object]


class PoolMetrics:
    """Lightweight in-memory metrics aggregator for the Stratum pool."""

    _VALID_MODES = {"pps", "solo"}

    def __init__(
        self, config: PoolConfig, job_manager: JobManager, server: StratumServer
    ) -> None:
        self._config = config
        self._pool_mode = str(config.pool_mode or "pps").strip().lower()
        if self._pool_mode not in self._VALID_MODES:
            self._pool_mode = "pps"
        self._job_manager = job_manager
        self._server = server
        self._share_events: Deque[ShareEvent] = deque(maxlen=5000)
        self._block_events: Deque[Dict[str, object]] = deque(maxlen=200)
        self._accounting_events: Deque[AccountingEvent] = deque(maxlen=5000)
        self._worker_balances_cache: Dict[tuple[str, str, str], Dict[str, object]] = {}
        self._started = time.time()
        self._db = self._init_db(config.db_url)
        self._db_lock = Lock()
        self._payout_state_lock = Lock()
        self._payout_interval_seconds = max(
            0.0, float(getattr(config, "payout_interval_seconds", 0.0) or 0.0)
        )
        self._next_payout_at: Optional[float] = (
            self._started + self._payout_interval_seconds
            if self._payout_interval_seconds > 0
            else None
        )
        self._last_payout_at: Optional[float] = None
        self._last_payout_count: int = 0
        self._last_payout_error: Optional[str] = None

    @property
    def config(self) -> PoolConfig:
        return self._config

    def _init_db(self, db_url: str) -> Optional[sqlite3.Connection]:
        if not db_url or not db_url.startswith("sqlite"):
            return None

        # Support sqlite:///relative.db and sqlite:////abs/path.db
        path = db_url.replace("sqlite:///", "", 1)
        if path.startswith("//"):
            path = path[1:]
        db_path = Path(path).expanduser()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(db_path, check_same_thread=False)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS shares (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL,
                worker TEXT,
                address TEXT,
                difficulty REAL,
                status TEXT,
                job_id TEXT,
                height INTEGER,
                is_block INTEGER,
                tx_count INTEGER
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS blocks (
                job_id TEXT PRIMARY KEY,
                height INTEGER,
                ts REAL,
                found_by_pool INTEGER,
                reward INTEGER,
                tx_count INTEGER,
                worker TEXT,
                address TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS worker_balances (
                mode TEXT NOT NULL,
                worker TEXT NOT NULL,
                address TEXT NOT NULL,
                total_credit INTEGER NOT NULL DEFAULT 0,
                pps_credit INTEGER NOT NULL DEFAULT 0,
                solo_credit INTEGER NOT NULL DEFAULT 0,
                paid_out INTEGER NOT NULL DEFAULT 0,
                accepted_shares INTEGER NOT NULL DEFAULT 0,
                accepted_blocks INTEGER NOT NULL DEFAULT 0,
                rejected_shares INTEGER NOT NULL DEFAULT 0,
                updated_ts REAL NOT NULL,
                PRIMARY KEY (mode, worker, address)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS accounting_ledger (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL,
                mode TEXT NOT NULL,
                worker TEXT NOT NULL,
                address TEXT NOT NULL,
                event TEXT NOT NULL,
                amount INTEGER NOT NULL,
                job_id TEXT,
                details TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS payouts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL,
                mode TEXT NOT NULL,
                address TEXT NOT NULL,
                amount INTEGER NOT NULL,
                tx_hash TEXT,
                status TEXT NOT NULL,
                error TEXT
            )
            """
        )
        self._ensure_column(conn, "blocks", "worker", "TEXT")
        self._ensure_column(conn, "blocks", "address", "TEXT")
        self._ensure_column(conn, "blocks", "reward", "INTEGER")
        self._ensure_column(conn, "worker_balances", "paid_out", "INTEGER NOT NULL DEFAULT 0")
        conn.commit()
        return conn

    @staticmethod
    def _ensure_column(
        conn: sqlite3.Connection, table: str, column: str, column_type: str
    ) -> None:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        existing = {str(row[1]) for row in rows}
        if column in existing:
            return
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_type}")

    async def record_share(
        self,
        session: Session,
        job: StratumJob,
        submit_params: Dict[str, object],
        ok: bool,
        reason: Optional[str],
        is_block: bool,
        tx_count: int,
    ) -> None:
        now = time.time()
        reward = self._expected_reward(job)
        worker = self._normalize_worker(session.worker, session.session_id)
        address = self._normalize_address(session.address)
        difficulty = float(
            submit_params.get("d_ratio")
            or submit_params.get("shareTarget")
            or job.share_target
        )
        event: ShareEvent = {
            "timestamp": now,
            "session_id": session.session_id,
            "worker": worker,
            "address": address,
            "difficulty": difficulty,
            "status": "accepted" if ok else "rejected",
            "reason": reason,
            "job_id": job.job_id,
            "height": submit_params.get("height")
            or job.header.get("number")
            or job.header.get("height"),
        }
        accepted_block = bool(ok and is_block)
        self._share_events.append(event)
        self._persist_share(
            event,
            is_block=accepted_block,
            tx_count=tx_count,
            reward=reward,
        )
        self._apply_accounting_for_share(
            ts=now,
            worker=worker,
            address=address,
            job_id=job.job_id,
            ok=ok,
            is_block=accepted_block,
            reward=reward,
            difficulty=difficulty,
            reason=reason,
        )
        rejection_reason = str(reason or "").lower()
        stale_template_reject = (not ok) and (
            "stale template" in rejection_reason or "stale_template" in rejection_reason
        )
        if stale_template_reject:
            request_refresh = getattr(self._job_manager, "request_refresh", None)
            if callable(request_refresh):
                request_refresh()
        if accepted_block:
            self._block_events.appendleft(
                {
                    "found_by_pool": True,
                    "timestamp": now,
                    "job_id": job.job_id,
                    "height": event["height"],
                    "reward": reward,
                    "tx_count": tx_count,
                    "worker": worker,
                    "address": address,
                }
            )
            request_refresh = getattr(self._job_manager, "request_refresh", None)
            if callable(request_refresh):
                request_refresh()

    def _persist_share(
        self,
        event: ShareEvent,
        *,
        is_block: bool,
        tx_count: int,
        reward: int,
    ) -> None:
        if self._db is None:
            return

        with self._db_lock:
            self._db.execute(
                """
                INSERT INTO shares (ts, worker, address, difficulty, status, job_id, height, is_block, tx_count)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.get("timestamp"),
                    event.get("worker"),
                    event.get("address"),
                    event.get("difficulty"),
                    event.get("status"),
                    event.get("job_id"),
                    event.get("height"),
                    1 if is_block else 0,
                    tx_count,
                ),
            )
            if is_block:
                self._db.execute(
                    """
                INSERT OR REPLACE INTO blocks (
                    job_id, height, ts, found_by_pool, reward, tx_count, worker, address
                )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event.get("job_id"),
                        event.get("height"),
                        event.get("timestamp"),
                        1,
                        reward,
                        tx_count,
                        event.get("worker"),
                        event.get("address"),
                    ),
                )
            self._db.commit()

    def remove_duplicate_block(self, job_id: str) -> None:
        """Remove a block from tracking if it turns out to be a duplicate.
        
        This should be called after block submission when the node returns duplicate=true.
        Removes the block from both in-memory deque and database.
        """
        # Remove from in-memory deque
        self._block_events = deque(
            (blk for blk in self._block_events if blk.get("job_id") != job_id),
            maxlen=200
        )
        
        # Remove from database
        if self._db is not None:
            with self._db_lock:
                self._db.execute(
                    "DELETE FROM blocks WHERE job_id = ?",
                    (job_id,)
                )
                self._db.commit()

    def _now_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _int_value(value: object) -> int:
        if value in (None, ""):
            return 0
        if isinstance(value, str):
            try:
                return int(value, 16) if value.startswith("0x") else int(value)
            except Exception:
                return 0
        try:
            return int(value)
        except Exception:
            return 0

    def _reward_from_raw(self, raw: object) -> int:
        if not isinstance(raw, dict):
            return 0
        coinbase = raw.get("coinbase")
        if isinstance(coinbase, dict):
            amount = self._int_value(coinbase.get("amount"))
            if amount > 0:
                return amount
        return self._int_value(raw.get("reward") or raw.get("blockReward"))

    def _expected_reward(self, job: StratumJob) -> int:
        return self._reward_from_raw(job.raw)

    @staticmethod
    def _normalize_worker(worker: Optional[str], session_id: Optional[str] = None) -> str:
        value = str(worker or "").strip()
        if value:
            return value
        fallback = str(session_id or "").strip()
        return fallback or "unknown-worker"

    @staticmethod
    def _normalize_address(address: Optional[str]) -> str:
        value = str(address or "").strip()
        return value or "unknown-address"

    @staticmethod
    def _safe_ratio(value: object) -> float:
        try:
            ratio = float(value or 0.0)
        except Exception:
            return 0.0
        if ratio < 0:
            return 0.0
        if ratio > 1.0:
            return 1.0
        return ratio

    def _credit_for_share(self, reward: int, difficulty_ratio: float) -> int:
        if reward <= 0:
            return 0
        ratio = self._safe_ratio(difficulty_ratio)
        if ratio <= 0:
            return 0
        return int(float(reward) * ratio)

    def _apply_balance_delta(
        self,
        *,
        ts: float,
        worker: str,
        address: str,
        pps_credit: int = 0,
        solo_credit: int = 0,
        paid_out_delta: int = 0,
        accepted_shares_delta: int = 0,
        accepted_blocks_delta: int = 0,
        rejected_shares_delta: int = 0,
    ) -> None:
        mode = self._pool_mode
        key = (mode, worker, address)
        row = self._worker_balances_cache.get(key)
        if row is None:
            row = {
                "mode": mode,
                "worker": worker,
                "address": address,
                "total_credit": 0,
                "pps_credit": 0,
                "solo_credit": 0,
                "paid_out": 0,
                "accepted_shares": 0,
                "accepted_blocks": 0,
                "rejected_shares": 0,
                "updated_ts": ts,
            }
            self._worker_balances_cache[key] = row

        row["pps_credit"] = int(row.get("pps_credit") or 0) + int(pps_credit)
        row["solo_credit"] = int(row.get("solo_credit") or 0) + int(solo_credit)
        row["total_credit"] = int(row.get("total_credit") or 0) + int(pps_credit) + int(
            solo_credit
        )
        row["paid_out"] = max(
            0, int(row.get("paid_out") or 0) + int(paid_out_delta)
        )
        row["accepted_shares"] = int(row.get("accepted_shares") or 0) + int(
            accepted_shares_delta
        )
        row["accepted_blocks"] = int(row.get("accepted_blocks") or 0) + int(
            accepted_blocks_delta
        )
        row["rejected_shares"] = int(row.get("rejected_shares") or 0) + int(
            rejected_shares_delta
        )
        row["updated_ts"] = ts

        if self._db is None:
            return
        with self._db_lock:
            self._db.execute(
                """
                INSERT INTO worker_balances (
                    mode, worker, address, total_credit, pps_credit, solo_credit, paid_out,
                    accepted_shares, accepted_blocks, rejected_shares, updated_ts
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(mode, worker, address) DO UPDATE SET
                    total_credit = worker_balances.total_credit + excluded.total_credit,
                    pps_credit = worker_balances.pps_credit + excluded.pps_credit,
                    solo_credit = worker_balances.solo_credit + excluded.solo_credit,
                    paid_out = MAX(0, worker_balances.paid_out + excluded.paid_out),
                    accepted_shares = worker_balances.accepted_shares + excluded.accepted_shares,
                    accepted_blocks = worker_balances.accepted_blocks + excluded.accepted_blocks,
                    rejected_shares = worker_balances.rejected_shares + excluded.rejected_shares,
                    updated_ts = excluded.updated_ts
                """,
                (
                    mode,
                    worker,
                    address,
                    int(pps_credit) + int(solo_credit),
                    int(pps_credit),
                    int(solo_credit),
                    int(paid_out_delta),
                    int(accepted_shares_delta),
                    int(accepted_blocks_delta),
                    int(rejected_shares_delta),
                    ts,
                ),
            )
            self._db.commit()

    def _record_accounting_event(
        self,
        *,
        ts: float,
        worker: str,
        address: str,
        event: str,
        amount: int,
        job_id: Optional[str],
        details: Optional[dict[str, object]] = None,
    ) -> None:
        payload = {
            "timestamp": ts,
            "mode": self._pool_mode,
            "worker": worker,
            "address": address,
            "event": event,
            "amount": int(amount),
            "job_id": job_id or "",
            "details": details or {},
        }
        self._accounting_events.appendleft(payload)
        if self._db is None:
            return
        with self._db_lock:
            self._db.execute(
                """
                INSERT INTO accounting_ledger (ts, mode, worker, address, event, amount, job_id, details)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ts,
                    self._pool_mode,
                    worker,
                    address,
                    event,
                    int(amount),
                    job_id,
                    json.dumps(details or {}, sort_keys=True),
                ),
            )
            self._db.commit()

    def _apply_accounting_for_share(
        self,
        *,
        ts: float,
        worker: str,
        address: str,
        job_id: str,
        ok: bool,
        is_block: bool,
        reward: int,
        difficulty: float,
        reason: Optional[str],
    ) -> None:
        if ok:
            pps_credit = 0
            solo_credit = 0
            if self._pool_mode == "pps":
                pps_credit = self._credit_for_share(reward, difficulty)
                if pps_credit > 0:
                    self._record_accounting_event(
                        ts=ts,
                        worker=worker,
                        address=address,
                        event="pps_share_credit",
                        amount=pps_credit,
                        job_id=job_id,
                        details={"difficulty_ratio": difficulty, "reward": reward},
                    )
            elif self._pool_mode == "solo" and is_block:
                solo_credit = int(reward)
                if solo_credit > 0:
                    self._record_accounting_event(
                        ts=ts,
                        worker=worker,
                        address=address,
                        event="solo_block_credit",
                        amount=solo_credit,
                        job_id=job_id,
                        details={"reward": reward},
                    )
            self._apply_balance_delta(
                ts=ts,
                worker=worker,
                address=address,
                pps_credit=pps_credit,
                solo_credit=solo_credit,
                accepted_shares_delta=1,
                accepted_blocks_delta=1 if is_block else 0,
            )
            return

        self._apply_balance_delta(
            ts=ts,
            worker=worker,
            address=address,
            rejected_shares_delta=1,
        )
        self._record_accounting_event(
            ts=ts,
            worker=worker,
            address=address,
            event="share_rejected",
            amount=0,
            job_id=job_id,
            details={"reason": reason or "unknown"},
        )

    def _accounting_summary_from_db(self) -> Dict[str, object]:
        if self._db is None:
            return {}
        with self._db_lock:
            totals = self._db.execute(
                """
                SELECT
                    COALESCE(SUM(total_credit), 0),
                    COALESCE(SUM(pps_credit), 0),
                    COALESCE(SUM(solo_credit), 0),
                    COALESCE(SUM(paid_out), 0),
                    COALESCE(SUM(total_credit - paid_out), 0),
                    COALESCE(SUM(accepted_shares), 0),
                    COALESCE(SUM(accepted_blocks), 0),
                    COALESCE(SUM(rejected_shares), 0),
                    COUNT(*),
                    MAX(updated_ts)
                FROM worker_balances
                WHERE mode = ?
                """,
                (self._pool_mode,),
            ).fetchone()
            ledger_count = self._db.execute(
                "SELECT COUNT(*) FROM accounting_ledger WHERE mode = ?",
                (self._pool_mode,),
            ).fetchone()
        if not totals:
            return {}
        (
            gross_credit,
            pps_credit,
            solo_credit,
            paid_out_total,
            total_credit,
            accepted_shares,
            accepted_blocks,
            rejected_shares,
            workers_with_balance,
            updated_ts,
        ) = totals
        return {
            "pool_mode": self._pool_mode,
            "total_credit": str(int(total_credit or 0)),
            "gross_credit": str(int(gross_credit or 0)),
            "pps_credit": str(int(pps_credit or 0)),
            "solo_credit": str(int(solo_credit or 0)),
            "paid_out_total": str(int(paid_out_total or 0)),
            "accepted_shares": int(accepted_shares or 0),
            "accepted_blocks": int(accepted_blocks or 0),
            "rejected_shares": int(rejected_shares or 0),
            "workers_with_balance": int(workers_with_balance or 0),
            "ledger_entries": int((ledger_count or [0])[0] or 0),
            "updated_at": (
                datetime.fromtimestamp(float(updated_ts), tz=timezone.utc).isoformat()
                if updated_ts
                else None
            ),
        }

    def _accounting_summary_from_memory(self) -> Dict[str, object]:
        rows = [row for key, row in self._worker_balances_cache.items() if key[0] == self._pool_mode]
        gross_credit = sum(int(row.get("total_credit") or 0) for row in rows)
        paid_out_total = sum(int(row.get("paid_out") or 0) for row in rows)
        total_credit = max(0, gross_credit - paid_out_total)
        pps_credit = sum(int(row.get("pps_credit") or 0) for row in rows)
        solo_credit = sum(int(row.get("solo_credit") or 0) for row in rows)
        accepted_shares = sum(int(row.get("accepted_shares") or 0) for row in rows)
        accepted_blocks = sum(int(row.get("accepted_blocks") or 0) for row in rows)
        rejected_shares = sum(int(row.get("rejected_shares") or 0) for row in rows)
        updated_ts = max((float(row.get("updated_ts") or 0.0) for row in rows), default=0.0)
        return {
            "pool_mode": self._pool_mode,
            "total_credit": str(int(total_credit)),
            "gross_credit": str(int(gross_credit)),
            "pps_credit": str(int(pps_credit)),
            "solo_credit": str(int(solo_credit)),
            "paid_out_total": str(int(paid_out_total)),
            "accepted_shares": int(accepted_shares),
            "accepted_blocks": int(accepted_blocks),
            "rejected_shares": int(rejected_shares),
            "workers_with_balance": len(rows),
            "ledger_entries": len(self._accounting_events),
            "updated_at": (
                datetime.fromtimestamp(updated_ts, tz=timezone.utc).isoformat()
                if updated_ts > 0
                else None
            ),
        }

    def _hashrate_from_events(
        self, events: List[ShareEvent], window_seconds: float
    ) -> float:
        if not events:
            return 0.0
        cutoff = time.time() - window_seconds
        total = sum(
            float(ev.get("difficulty") or 0.0)
            for ev in events
            if ev["timestamp"] >= cutoff and ev["status"] == "accepted"
        )
        return total / window_seconds if window_seconds > 0 else 0.0

    def _hashrate_from_db(self, window_seconds: float) -> float:
        if self._db is None:
            return 0.0

        cutoff = time.time() - window_seconds
        with self._db_lock:
            row = self._db.execute(
                "SELECT COALESCE(SUM(difficulty), 0) FROM shares WHERE status = 'accepted' AND ts >= ?",
                (cutoff,),
            ).fetchone()
        total = float(row[0] or 0.0) if row else 0.0
        return total / window_seconds if window_seconds > 0 else 0.0

    def _hashrate_series(self, minutes: int = 60) -> List[Tuple[str, float]]:
        cutoff = time.time() - (minutes * 60)
        buckets: Dict[int, float] = defaultdict(float)

        if self._db is not None:
            with self._db_lock:
                rows = self._db.execute(
                    """
                    SELECT CAST(ts / 60 AS INT) * 60 as bucket, SUM(difficulty)
                    FROM shares
                    WHERE status = 'accepted' AND ts >= ?
                    GROUP BY bucket
                    ORDER BY bucket ASC
                    """,
                    (cutoff,),
                ).fetchall()
            for bucket_ts, diff_sum in rows:
                buckets[int(bucket_ts)] += float(diff_sum or 0.0)
        else:
            for ev in self._share_events:
                if ev["status"] == "accepted" and ev["timestamp"] >= cutoff:
                    bucket = int(ev["timestamp"] // 60) * 60
                    buckets[bucket] += float(ev.get("difficulty") or 0.0)

        series: List[Tuple[str, float]] = []
        for bucket in sorted(buckets.keys()):
            ts = datetime.fromtimestamp(bucket, tz=timezone.utc).isoformat()
            series.append((ts, buckets[bucket] / 60))
        return series

    def _latest_block(self) -> Dict[str, object]:
        if self._db is not None:
            with self._db_lock:
                row = self._db.execute(
                    """
                    SELECT height, job_id, ts, found_by_pool, reward, worker, address
                    FROM blocks
                    ORDER BY ts DESC
                    LIMIT 1
                    """
                ).fetchone()
            if row:
                height, job_id, ts, found, reward, worker, address = row
                return {
                    "height": height,
                    "hash": job_id,
                    "timestamp": (
                        datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
                        if ts
                        else None
                    ),
                    "found_by_pool": bool(found),
                    "reward": str(int(reward or 0)),
                    "worker": worker or "",
                    "address": address or "",
                }

        if self._block_events:
            blk = self._block_events[0]
            return {
                "height": blk.get("height"),
                "hash": blk.get("job_id"),
                "timestamp": (
                    datetime.fromtimestamp(
                        float(blk.get("timestamp")), tz=timezone.utc
                    ).isoformat()
                    if blk.get("timestamp")
                    else None
                ),
                "found_by_pool": blk.get("found_by_pool", False),
                "reward": str(int(blk.get("reward") or 0)),
                "worker": blk.get("worker") or "",
                "address": blk.get("address") or "",
            }

        job = self._job_manager.current_job()
        return {
            "height": (job.height if job else None) or 0,
            "hash": (job.header.get("hash") if job and job.header else None) or "0x0",
            "timestamp": None,
            "found_by_pool": False,
        }

    @staticmethod
    def _iso_ts(ts: Optional[float]) -> Optional[str]:
        if ts is None or ts <= 0:
            return None
        return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat()

    @staticmethod
    def _payout_eligible_address(address: str) -> bool:
        text = str(address or "").strip()
        if not text:
            return False
        if text.lower() == "unknown-address":
            return False
        return True

    def set_next_payout_at(self, next_payout_at: Optional[float]) -> None:
        with self._payout_state_lock:
            self._next_payout_at = float(next_payout_at) if next_payout_at else None

    def record_payout_cycle(
        self,
        *,
        ts: Optional[float] = None,
        count: int = 0,
        error: Optional[str] = None,
    ) -> None:
        now = float(ts or time.time())
        with self._payout_state_lock:
            self._last_payout_at = now
            self._last_payout_count = max(0, int(count or 0))
            self._last_payout_error = str(error).strip() if error else None

    def payout_status(self) -> Dict[str, object]:
        now = time.time()
        with self._payout_state_lock:
            interval = float(self._payout_interval_seconds)
            next_at = self._next_payout_at
            last_at = self._last_payout_at
            last_count = int(self._last_payout_count)
            last_error = self._last_payout_error
        countdown: Optional[int] = None
        if next_at is not None:
            countdown = max(0, int(next_at - now))
        return {
            "payouts_enabled": bool(interval > 0),
            "payout_interval_seconds": interval,
            "payout_min_amount": int(getattr(self._config, "payout_min_amount", 1) or 1),
            "next_payout_at": self._iso_ts(next_at),
            "payout_countdown_seconds": countdown,
            "last_payout_at": self._iso_ts(last_at),
            "last_payout_count": last_count,
            "last_payout_error": last_error,
        }

    def payout_due_addresses(
        self, *, min_amount: int, limit: int = 50
    ) -> List[Dict[str, object]]:
        min_amount = max(1, int(min_amount or 1))
        limit = max(1, min(int(limit or 50), 500))
        items: List[Dict[str, object]] = []

        if self._db is not None:
            with self._db_lock:
                rows = self._db.execute(
                    """
                    SELECT address,
                           SUM(total_credit - paid_out) as due_amount,
                           COUNT(*) as worker_rows,
                           MAX(updated_ts) as updated_ts
                    FROM worker_balances
                    WHERE mode = ?
                    GROUP BY address
                    HAVING SUM(total_credit - paid_out) >= ?
                    ORDER BY due_amount DESC, address ASC
                    LIMIT ?
                    """,
                    (self._pool_mode, min_amount, limit),
                ).fetchall()
            for address, due_amount, worker_rows, updated_ts in rows:
                addr = str(address or "").strip()
                amount = int(due_amount or 0)
                if not self._payout_eligible_address(addr) or amount <= 0:
                    continue
                items.append(
                    {
                        "address": addr,
                        "amount": amount,
                        "workers": int(worker_rows or 0),
                        "updated_at": self._iso_ts(float(updated_ts or 0.0)),
                    }
                )
            return items

        by_address: Dict[str, Dict[str, object]] = {}
        for (mode, _worker, address), row in self._worker_balances_cache.items():
            if mode != self._pool_mode:
                continue
            addr = str(address or "").strip()
            if not self._payout_eligible_address(addr):
                continue
            gross = int(row.get("total_credit") or 0)
            paid = int(row.get("paid_out") or 0)
            available = max(0, gross - paid)
            if available <= 0:
                continue
            current = by_address.get(addr)
            if current is None:
                by_address[addr] = {
                    "address": addr,
                    "amount": available,
                    "workers": 1,
                    "updated_ts": float(row.get("updated_ts") or 0.0),
                }
            else:
                current["amount"] = int(current.get("amount") or 0) + available
                current["workers"] = int(current.get("workers") or 0) + 1
                current["updated_ts"] = max(
                    float(current.get("updated_ts") or 0.0),
                    float(row.get("updated_ts") or 0.0),
                )

        ordered = sorted(
            by_address.values(),
            key=lambda entry: (-int(entry.get("amount") or 0), str(entry.get("address") or "")),
        )
        for entry in ordered:
            amount = int(entry.get("amount") or 0)
            if amount < min_amount:
                continue
            items.append(
                {
                    "address": str(entry.get("address") or ""),
                    "amount": amount,
                    "workers": int(entry.get("workers") or 0),
                    "updated_at": self._iso_ts(float(entry.get("updated_ts") or 0.0)),
                }
            )
            if len(items) >= limit:
                break
        return items

    def record_payout_sent(
        self,
        *,
        address: str,
        amount: int,
        tx_hash: str,
        ts: Optional[float] = None,
    ) -> int:
        addr = str(address or "").strip()
        requested = max(0, int(amount or 0))
        if requested <= 0 or not self._payout_eligible_address(addr):
            return 0
        now = float(ts or time.time())
        applied = 0

        if self._db is not None:
            with self._db_lock:
                rows = self._db.execute(
                    """
                    SELECT worker, total_credit, paid_out
                    FROM worker_balances
                    WHERE mode = ? AND address = ?
                    ORDER BY updated_ts ASC, worker ASC
                    """,
                    (self._pool_mode, addr),
                ).fetchall()

                remaining = requested
                for worker, total_credit, paid_out in rows:
                    if remaining <= 0:
                        break
                    available = max(0, int(total_credit or 0) - int(paid_out or 0))
                    if available <= 0:
                        continue
                    delta = min(available, remaining)
                    self._db.execute(
                        """
                        UPDATE worker_balances
                        SET paid_out = paid_out + ?, updated_ts = ?
                        WHERE mode = ? AND worker = ? AND address = ?
                        """,
                        (delta, now, self._pool_mode, str(worker), addr),
                    )
                    details = json.dumps({"tx_hash": tx_hash}, sort_keys=True)
                    self._db.execute(
                        """
                        INSERT INTO accounting_ledger (ts, mode, worker, address, event, amount, job_id, details)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            now,
                            self._pool_mode,
                            str(worker),
                            addr,
                            "payout_sent",
                            int(delta),
                            None,
                            details,
                        ),
                    )
                    self._accounting_events.appendleft(
                        {
                            "timestamp": now,
                            "mode": self._pool_mode,
                            "worker": str(worker),
                            "address": addr,
                            "event": "payout_sent",
                            "amount": int(delta),
                            "job_id": "",
                            "details": {"tx_hash": tx_hash},
                        }
                    )
                    remaining -= delta
                    applied += delta

                self._db.execute(
                    """
                    INSERT INTO payouts (ts, mode, address, amount, tx_hash, status, error)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        now,
                        self._pool_mode,
                        addr,
                        int(applied),
                        tx_hash,
                        "submitted",
                        None,
                    ),
                )
                self._db.commit()
            return applied

        remaining = requested
        for key, row in sorted(self._worker_balances_cache.items()):
            mode, worker, row_addr = key
            if mode != self._pool_mode or str(row_addr) != addr:
                continue
            if remaining <= 0:
                break
            available = max(
                0,
                int(row.get("total_credit") or 0) - int(row.get("paid_out") or 0),
            )
            if available <= 0:
                continue
            delta = min(available, remaining)
            self._apply_balance_delta(
                ts=now,
                worker=str(worker),
                address=addr,
                paid_out_delta=delta,
            )
            self._record_accounting_event(
                ts=now,
                worker=str(worker),
                address=addr,
                event="payout_sent",
                amount=delta,
                job_id=None,
                details={"tx_hash": tx_hash},
            )
            remaining -= delta
            applied += delta
        return applied

    def record_payout_failed(
        self,
        *,
        address: str,
        amount: int,
        error: str,
        ts: Optional[float] = None,
    ) -> None:
        addr = str(address or "").strip()
        now = float(ts or time.time())
        message = str(error or "unknown payout failure")
        self._record_accounting_event(
            ts=now,
            worker="__pool__",
            address=addr if addr else "unknown-address",
            event="payout_failed",
            amount=max(0, int(amount or 0)),
            job_id=None,
            details={"error": message},
        )
        if self._db is None:
            return
        with self._db_lock:
            self._db.execute(
                """
                INSERT INTO payouts (ts, mode, address, amount, tx_hash, status, error)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    now,
                    self._pool_mode,
                    addr,
                    max(0, int(amount or 0)),
                    None,
                    "failed",
                    message,
                ),
            )
            self._db.commit()

    def pool_summary(self) -> Dict[str, object]:
        stats = self._server.stats()
        job = self._job_manager.current_job()
        share_events = list(self._share_events)
        pool_hashrate = self._hashrate_from_db(600) or self._hashrate_from_events(
            share_events, 600
        )
        latest_block = self._latest_block()
        current_reward = str(self._reward_from_raw(getattr(job, "raw", None)))
        accounting = self.accounting_summary()
        payout = self.payout_status()
        return {
            "pool_name": "Animica Stratum Pool",
            "network": self._config.network or f"chain-{self._config.chain_id}",
            "pool_mode": self._pool_mode,
            "height": (job.height if job else None) or 0,
            "last_block_hash": latest_block.get("hash") or "0x0",
            "pool_hashrate": pool_hashrate,
            "blocks_found_total": self._blocks_found_total(),
            "hashrate_series": self._hashrate_series(60),
            "hashrate_1m": self._hashrate_from_db(60)
            or self._hashrate_from_events(share_events, 60),
            "hashrate_15m": self._hashrate_from_db(900)
            or self._hashrate_from_events(share_events, 900),
            "hashrate_1h": self._hashrate_from_db(3600)
            or self._hashrate_from_events(share_events, 3600),
            "num_miners": stats.get("clients", 0),
            "num_workers": stats.get("clients", 0),
            "round_duration_seconds": self._config.poll_interval,
            "round_shares": len(share_events),
            "round_estimated_reward": current_reward,
            "uptime_seconds": stats.get("uptime_sec", int(time.time() - self._started)),
            "stratum_endpoint": f"stratum+tcp://{self._config.host}:{self._config.port}",
            "last_update": self._now_iso(),
            "latest_block": latest_block,
            "accounting": accounting,
            **payout,
        }

    def _blocks_found_total(self) -> int:
        if self._db is not None:
            with self._db_lock:
                row = self._db.execute("SELECT COUNT(*) FROM blocks").fetchone()
            return int(row[0] or 0) if row else 0
        return len(self._block_events)

    def _blocks_found_by_worker(self, worker_id: str) -> int:
        if self._db is not None:
            with self._db_lock:
                row = self._db.execute(
                    "SELECT COUNT(*) FROM blocks WHERE worker = ?",
                    (worker_id,),
                ).fetchone()
            return int(row[0] or 0) if row else 0
        return sum(
            1 for blk in self._block_events if str(blk.get("worker") or "") == worker_id
        )

    def _worker_balances_map(self) -> Dict[str, Dict[str, object]]:
        data: Dict[str, Dict[str, object]] = {}
        if self._db is not None:
            with self._db_lock:
                rows = self._db.execute(
                    """
                    SELECT worker,
                           MAX(address) as address,
                           SUM(total_credit) as total_credit,
                           SUM(pps_credit) as pps_credit,
                           SUM(solo_credit) as solo_credit,
                           SUM(paid_out) as paid_out,
                           SUM(accepted_shares) as accepted_shares,
                           SUM(accepted_blocks) as accepted_blocks,
                           SUM(rejected_shares) as rejected_shares,
                           MAX(updated_ts) as updated_ts
                    FROM worker_balances
                    WHERE mode = ?
                    GROUP BY worker
                    """,
                    (self._pool_mode,),
                ).fetchall()
            for (
                worker,
                address,
                total_credit,
                pps_credit,
                solo_credit,
                paid_out,
                accepted_shares,
                accepted_blocks,
                rejected_shares,
                updated_ts,
            ) in rows:
                gross_credit = int(total_credit or 0)
                paid_total = int(paid_out or 0)
                available_credit = max(0, gross_credit - paid_total)
                data[str(worker)] = {
                    "address": address or "",
                    "total_credit": available_credit,
                    "gross_credit": gross_credit,
                    "pps_credit": int(pps_credit or 0),
                    "solo_credit": int(solo_credit or 0),
                    "paid_out": paid_total,
                    "accepted_shares": int(accepted_shares or 0),
                    "accepted_blocks": int(accepted_blocks or 0),
                    "rejected_shares": int(rejected_shares or 0),
                    "updated_ts": float(updated_ts or 0.0),
                }
            return data

        for (mode, worker, _address), row in self._worker_balances_cache.items():
            if mode != self._pool_mode:
                continue
            current = data.get(worker)
            total_credit = int(row.get("total_credit") or 0)
            pps_credit = int(row.get("pps_credit") or 0)
            solo_credit = int(row.get("solo_credit") or 0)
            paid_out = int(row.get("paid_out") or 0)
            available_credit = max(0, total_credit - paid_out)
            accepted_shares = int(row.get("accepted_shares") or 0)
            accepted_blocks = int(row.get("accepted_blocks") or 0)
            rejected_shares = int(row.get("rejected_shares") or 0)
            updated_ts = float(row.get("updated_ts") or 0.0)
            if current is None:
                data[worker] = {
                    "address": row.get("address") or "",
                    "total_credit": available_credit,
                    "gross_credit": total_credit,
                    "pps_credit": pps_credit,
                    "solo_credit": solo_credit,
                    "paid_out": paid_out,
                    "accepted_shares": accepted_shares,
                    "accepted_blocks": accepted_blocks,
                    "rejected_shares": rejected_shares,
                    "updated_ts": updated_ts,
                }
            else:
                current["total_credit"] = int(current.get("total_credit") or 0) + available_credit
                current["gross_credit"] = int(current.get("gross_credit") or 0) + total_credit
                current["pps_credit"] = int(current.get("pps_credit") or 0) + pps_credit
                current["solo_credit"] = int(current.get("solo_credit") or 0) + solo_credit
                current["paid_out"] = int(current.get("paid_out") or 0) + paid_out
                current["accepted_shares"] = int(current.get("accepted_shares") or 0) + accepted_shares
                current["accepted_blocks"] = int(current.get("accepted_blocks") or 0) + accepted_blocks
                current["rejected_shares"] = int(current.get("rejected_shares") or 0) + rejected_shares
                current["updated_ts"] = max(float(current.get("updated_ts") or 0.0), updated_ts)
        return data

    def miners(self) -> Dict[str, object]:
        sessions = self._server.session_snapshots()
        session_map = {str(s.get("worker") or s.get("session_id")): s for s in sessions}
        balance_map = self._worker_balances_map()

        cutoff_1m = time.time() - 60
        cutoff_15m = time.time() - 900
        cutoff_1h = time.time() - 3600
        cutoff_max = min(cutoff_1m, cutoff_15m, cutoff_1h)

        aggregates: Dict[str, Dict[str, object]] = {}
        block_counts: Dict[str, int] = {}
        if self._db is not None:
            with self._db_lock:
                rows = self._db.execute(
                    """
                    SELECT worker,
                           MAX(address) as address,
                           SUM(CASE WHEN status='accepted' THEN 1 ELSE 0 END) as accepted,
                           SUM(CASE WHEN status='rejected' THEN 1 ELSE 0 END) as rejected,
                           SUM(CASE WHEN status='accepted' AND ts >= ? THEN difficulty ELSE 0 END) as diff_1m,
                           SUM(CASE WHEN status='accepted' AND ts >= ? THEN difficulty ELSE 0 END) as diff_15m,
                           SUM(CASE WHEN status='accepted' AND ts >= ? THEN difficulty ELSE 0 END) as diff_1h,
                           MAX(ts) as last_ts
                    FROM shares
                    WHERE ts >= ?
                    GROUP BY worker
                    """,
                    (cutoff_1m, cutoff_15m, cutoff_1h, cutoff_max),
                ).fetchall()
                block_rows = self._db.execute(
                    """
                    SELECT worker, COUNT(*)
                    FROM blocks
                    WHERE worker IS NOT NULL AND worker != ''
                    GROUP BY worker
                    """
                ).fetchall()
            for row in rows:
                (
                    worker_id,
                    address,
                    accepted,
                    rejected,
                    diff1,
                    diff15,
                    diff60,
                    last_ts,
                ) = row
                aggregates[str(worker_id)] = {
                    "address": address or "",
                    "shares_accepted": int(accepted or 0),
                    "shares_rejected": int(rejected or 0),
                    "hashrate_1m": float(diff1 or 0.0) / 60,
                    "hashrate_15m": float(diff15 or 0.0) / 900,
                    "hashrate_1h": float(diff60 or 0.0) / 3600,
                    "last_share_at": last_ts,
                }
            for worker_id, blocks_found in block_rows:
                block_counts[str(worker_id)] = int(blocks_found or 0)

        events_by_worker: Dict[str, List[ShareEvent]] = defaultdict(list)
        for ev in self._share_events:
            events_by_worker[str(ev.get("worker"))].append(ev)
        if not block_counts:
            for blk in self._block_events:
                worker_id = str(blk.get("worker") or "")
                if worker_id:
                    block_counts[worker_id] = block_counts.get(worker_id, 0) + 1

        items: List[Dict[str, object]] = []
        seen_workers = set()
        for worker_id, session in session_map.items():
            worker_events = events_by_worker.get(str(worker_id), [])
            agg = aggregates.get(worker_id, {})
            items.append(
                {
                    "worker_id": worker_id,
                    "worker_name": worker_id,
                    "address": agg.get("address")
                    or balance_map.get(worker_id, {}).get("address")
                    or session.get("address")
                    or "",
                    "hashrate_1m": agg.get("hashrate_1m")
                    or self._hashrate_from_events(worker_events, 60),
                    "hashrate_15m": agg.get("hashrate_15m")
                    or self._hashrate_from_events(worker_events, 900),
                    "hashrate_1h": agg.get("hashrate_1h")
                    or self._hashrate_from_events(worker_events, 3600),
                    "last_share_at": agg.get("last_share_at")
                    or session.get("last_share_at"),
                    "difficulty": session.get("current_difficulty")
                    or session.get("share_target"),
                    "shares_accepted": agg.get("shares_accepted")
                    or session.get("shares_accepted", 0),
                    "shares_rejected": agg.get("shares_rejected")
                    or session.get("shares_rejected", 0),
                    "blocks_found": block_counts.get(worker_id, 0),
                    "pool_mode": self._pool_mode,
                    "credit_total": str(
                        int(balance_map.get(worker_id, {}).get("total_credit") or 0)
                    ),
                    "credit_pps": str(
                        int(balance_map.get(worker_id, {}).get("pps_credit") or 0)
                    ),
                    "credit_solo": str(
                        int(balance_map.get(worker_id, {}).get("solo_credit") or 0)
                    ),
                }
            )
            seen_workers.add(worker_id)

        # Include historical miners not currently connected
        for worker_id, agg in aggregates.items():
            if worker_id in seen_workers:
                continue
            worker_events = events_by_worker.get(worker_id, [])
            items.append(
                {
                    "worker_id": worker_id,
                    "worker_name": worker_id,
                    "address": agg.get("address")
                    or balance_map.get(worker_id, {}).get("address")
                    or "",
                    "hashrate_1m": agg.get("hashrate_1m")
                    or self._hashrate_from_events(worker_events, 60),
                    "hashrate_15m": agg.get("hashrate_15m")
                    or self._hashrate_from_events(worker_events, 900),
                    "hashrate_1h": agg.get("hashrate_1h")
                    or self._hashrate_from_events(worker_events, 3600),
                    "last_share_at": agg.get("last_share_at"),
                    "difficulty": None,
                    "shares_accepted": agg.get("shares_accepted") or 0,
                    "shares_rejected": agg.get("shares_rejected") or 0,
                    "blocks_found": block_counts.get(worker_id, 0),
                    "pool_mode": self._pool_mode,
                    "credit_total": str(
                        int(balance_map.get(worker_id, {}).get("total_credit") or 0)
                    ),
                    "credit_pps": str(
                        int(balance_map.get(worker_id, {}).get("pps_credit") or 0)
                    ),
                    "credit_solo": str(
                        int(balance_map.get(worker_id, {}).get("solo_credit") or 0)
                    ),
                }
            )

        for worker_id, balance in balance_map.items():
            if worker_id in seen_workers:
                continue
            items.append(
                {
                    "worker_id": worker_id,
                    "worker_name": worker_id,
                    "address": balance.get("address") or "",
                    "hashrate_1m": 0.0,
                    "hashrate_15m": 0.0,
                    "hashrate_1h": 0.0,
                    "last_share_at": balance.get("updated_ts"),
                    "difficulty": None,
                    "shares_accepted": int(balance.get("accepted_shares") or 0),
                    "shares_rejected": int(balance.get("rejected_shares") or 0),
                    "blocks_found": int(balance.get("accepted_blocks") or 0),
                    "pool_mode": self._pool_mode,
                    "credit_total": str(int(balance.get("total_credit") or 0)),
                    "credit_pps": str(int(balance.get("pps_credit") or 0)),
                    "credit_solo": str(int(balance.get("solo_credit") or 0)),
                }
            )

        return {"items": items, "total": len(items)}

    def miner_detail(self, worker_id: str) -> Dict[str, object]:
        balance = self._worker_balances_map().get(worker_id, {})
        session = next(
            (
                s
                for s in self._server.session_snapshots()
                if str(s.get("worker") or s.get("session_id")) == worker_id
            ),
            None,
        )

        cutoff = time.time() - 3600
        buckets: Dict[int, float] = defaultdict(float)
        events: List[ShareEvent] = []

        if self._db is not None:
            with self._db_lock:
                rows = self._db.execute(
                    """
                    SELECT ts, address, difficulty, status
                    FROM shares
                    WHERE worker = ? AND ts >= ?
                    ORDER BY ts ASC
                    """,
                    (worker_id, cutoff),
                ).fetchall()
        else:
            rows = []

        if rows:
            for ts, address, difficulty, status in rows:
                events.append(
                    {
                        "timestamp": ts,
                        "worker": worker_id,
                        "address": address,
                        "difficulty": difficulty,
                        "status": status,
                    }
                )
                if status == "accepted":
                    bucket = int(ts // 60) * 60
                    buckets[bucket] += float(difficulty or 0.0)
        else:
            for ev in self._share_events:
                if str(ev.get("worker")) == worker_id and ev["timestamp"] >= cutoff:
                    events.append(ev)
                    if ev.get("status") == "accepted":
                        bucket = int(ev["timestamp"] // 60) * 60
                        buckets[bucket] += float(ev.get("difficulty") or 0.0)

        if not events and session is None and not balance:
            return {}

        timeseries: List[Tuple[str, float]] = []
        for bucket in sorted(buckets.keys()):
            ts_iso = datetime.fromtimestamp(bucket, tz=timezone.utc).isoformat()
            timeseries.append((ts_iso, buckets[bucket] / 60))

        accepted = sum(1 for ev in events if ev.get("status") == "accepted")
        rejected = sum(1 for ev in events if ev.get("status") == "rejected")
        latest = events[-1] if events else None
        blocks_found = self._blocks_found_by_worker(worker_id)
        return {
            "address": (latest.get("address") if latest else "")
            or balance.get("address")
            or "",
            "worker_name": worker_id,
            "hashrate_timeseries": timeseries,
            "last_share": {
                "time": (
                    datetime.fromtimestamp(
                        latest["timestamp"], tz=timezone.utc
                    ).isoformat()
                    if latest
                    else None
                ),
                "difficulty": latest.get("difficulty") if latest else None,
                "status": latest.get("status") if latest else None,
            },
            "shares_accepted": accepted,
            "shares_rejected": rejected,
            "blocks_found": blocks_found,
            "pool_mode": self._pool_mode,
            "credit_total": str(int(balance.get("total_credit") or 0)),
            "credit_pps": str(int(balance.get("pps_credit") or 0)),
            "credit_solo": str(int(balance.get("solo_credit") or 0)),
            "current_difficulty": (latest.get("difficulty") if latest else 0) or 0,
            "connected_since": (
                datetime.fromtimestamp(
                    session["connected_since"], tz=timezone.utc
                ).isoformat()
                if session and session.get("connected_since")
                else None
            ),
        }

    def recent_blocks(self) -> Dict[str, object]:
        items: List[Dict[str, object]] = []
        if self._db is not None:
            with self._db_lock:
                rows = self._db.execute(
                    """
                    SELECT height, job_id, ts, found_by_pool, reward, tx_count, worker, address
                    FROM blocks
                    ORDER BY ts DESC
                    LIMIT 50
                    """
                ).fetchall()
            for height, job_id, ts, found, reward, tx_count, worker, address in rows:
                items.append(
                    {
                        "height": height,
                        "hash": job_id,
                        "timestamp": (
                            datetime.fromtimestamp(
                                float(ts), tz=timezone.utc
                            ).isoformat()
                            if ts
                            else None
                        ),
                        "found_by_pool": bool(found),
                        "reward": str(int(reward or 0)),
                        "tx_count": tx_count,
                        "worker": worker or "",
                        "address": address or "",
                    }
                )

        if not items:
            blocks = list(self._block_events)
            items = [
                {
                    "height": blk.get("height"),
                    "hash": blk.get("job_id"),
                    "timestamp": (
                        datetime.fromtimestamp(
                            float(blk.get("timestamp")), tz=timezone.utc
                        ).isoformat()
                        if blk.get("timestamp")
                        else None
                    ),
                    "found_by_pool": blk.get("found_by_pool", False),
                    "reward": str(int(blk.get("reward") or 0)),
                    "tx_count": blk.get("tx_count"),
                    "worker": blk.get("worker") or "",
                    "address": blk.get("address") or "",
                }
                for blk in blocks
            ]

        return {
            "items": items,
            "total": len(items),
            "blocks_found_total": self._blocks_found_total(),
        }

    def accounting_summary(self) -> Dict[str, object]:
        if self._db is not None:
            summary = self._accounting_summary_from_db()
            if summary:
                return summary
        return self._accounting_summary_from_memory()

    def accounting_ledger(self, *, limit: int = 100) -> Dict[str, object]:
        limit = max(1, min(int(limit or 100), 500))
        items: List[Dict[str, object]] = []
        if self._db is not None:
            with self._db_lock:
                rows = self._db.execute(
                    """
                    SELECT ts, mode, worker, address, event, amount, job_id, details
                    FROM accounting_ledger
                    WHERE mode = ?
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (self._pool_mode, limit),
                ).fetchall()
            for ts, mode, worker, address, event, amount, job_id, details in rows:
                parsed_details: object = {}
                if isinstance(details, str) and details:
                    try:
                        parsed_details = json.loads(details)
                    except Exception:
                        parsed_details = {"raw": details}
                items.append(
                    {
                        "timestamp": (
                            datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat()
                            if ts
                            else None
                        ),
                        "mode": mode,
                        "worker": worker,
                        "address": address,
                        "event": event,
                        "amount": str(int(amount or 0)),
                        "job_id": job_id or "",
                        "details": parsed_details,
                    }
                )
        else:
            for entry in list(self._accounting_events)[:limit]:
                items.append(
                    {
                        "timestamp": (
                            datetime.fromtimestamp(
                                float(entry.get("timestamp") or 0.0), tz=timezone.utc
                            ).isoformat()
                            if entry.get("timestamp")
                            else None
                        ),
                        "mode": entry.get("mode") or self._pool_mode,
                        "worker": entry.get("worker") or "",
                        "address": entry.get("address") or "",
                        "event": entry.get("event") or "",
                        "amount": str(int(entry.get("amount") or 0)),
                        "job_id": entry.get("job_id") or "",
                        "details": entry.get("details") or {},
                    }
                )

        return {
            "pool_mode": self._pool_mode,
            "items": items,
            "total": len(items),
            "summary": self.accounting_summary(),
        }

    def health(self) -> Dict[str, object]:
        return {
            "status": "ok",
            "uptime": int(time.time() - self._started),
            "pool_mode": self._pool_mode,
            "accounting": self.accounting_summary(),
            "payout": self.payout_status(),
        }
