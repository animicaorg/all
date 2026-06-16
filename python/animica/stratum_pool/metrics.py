from __future__ import annotations

import hashlib
import hmac
import json
import os
import sqlite3
import time
from collections import OrderedDict, defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any, Deque, Dict, List, Optional, Tuple

from mining.stratum_server import StratumServer

from .config import PoolConfig
from .job_manager import JobManager

ShareEvent = Dict[str, object]
AccountingEvent = Dict[str, object]

# 256-bit space of a SHA3-256 digest — used to convert a share target into the
# expected number of raw hashes (2**256 / share_target_int).
_TWO_POW_256 = 2 ** 256

# How long a miner-reported hashrate sample stays "fresh". Miners report every
# ~30s, so a 120s window tolerates a couple of missed beats before a worker is
# dropped from the aggregate network hashrate.
_REPORTED_HR_TTL = 120.0


class RentalConflict(Exception):
    """Raised when a rig already has an active rental assignment."""


class PoolMetrics:
    """Lightweight in-memory metrics aggregator for the Stratum pool."""

    _VALID_MODES = {"pps", "solo", "both"}

    @staticmethod
    def _env_flag(name: str, default: bool = False) -> bool:
        raw = str(os.getenv(name, "1" if default else "0") or "").strip().lower()
        if raw in {"1", "true", "yes", "on"}:
            return True
        if raw in {"0", "false", "no", "off", ""}:
            return False
        return default

    def __init__(
        self, config: PoolConfig, job_manager: JobManager, server: StratumServer
    ) -> None:
        self._config = config
        self._pool_mode = str(config.pool_mode or "pps").strip().lower()
        if self._pool_mode not in self._VALID_MODES:
            self._pool_mode = "pps"
        self._job_manager = job_manager
        self._server = server
        # ENA training-treasury fee: skim this fraction (basis points) of each
        # accepted share's ANM credit and route it to the ENA training treasury.
        # The treasury accrues as a synthetic worker balance and is paid out by
        # the normal payout scheduler. 0 / unset disables the fee.
        self._ena_fee_bps = max(
            0, min(10_000, int(getattr(config, "ena_fee_bps", 0) or 0))
        )
        self._ena_treasury_address = str(
            getattr(config, "ena_treasury_address", "") or ""
        ).strip()
        self._ena_fee_worker = "__ena_treasury_fee__"
        self._share_events: Deque[ShareEvent] = deque(maxlen=5000)
        self._block_events: Deque[Dict[str, object]] = deque(maxlen=200)
        self._accounting_events: Deque[AccountingEvent] = deque(maxlen=5000)
        self._worker_balances_cache: Dict[tuple[str, str, str], Dict[str, object]] = {}
        self._payout_records: list[dict[str, object]] = []
        self._payout_record_seq: int = 0
        self._started = time.time()
        self._db_lock = Lock()
        self._db = self._init_db(config.db_url)
        # Rig-rental redirect: maps an owner rig (worker, address) to the
        # active assignment that re-points its mining credit at the renter
        # for the paid window. Kept in memory so the hot share path does no
        # per-share SQL; rebuilt from status='active' rows on boot so
        # redirects survive a pool restart.
        self._rental_lock = Lock()
        self._rental_index: Dict[Tuple[str, str], Dict[str, object]] = {}
        self._load_rental_index()
        # Miner-reported hashrate (Option A): each dual-miner polls its local
        # xmrig HTTP API and POSTs its measured H/s here. Summing the fresh
        # reports gives the true network hashrate without depending on sparse
        # block-shares. Keyed by worker; entries expire after _REPORTED_HR_TTL.
        self._reported_hashrates_lock = Lock()
        self._reported_hashrates: Dict[str, Dict[str, object]] = {}
        self._payout_state_lock = Lock()
        self._read_cache_ttl = max(
            0.0,
            float(os.getenv("ANIMICA_POOL_READ_CACHE_TTL", "0.5") or 0.5),
        )
        self._read_cache_max_entries = max(
            64,
            int(os.getenv("ANIMICA_POOL_READ_CACHE_MAX_ENTRIES", "2048") or 2048),
        )
        self._read_cache: "OrderedDict[str, Tuple[float, object]]" = OrderedDict()
        self._db_write_batch_size = max(
            1,
            int(os.getenv("ANIMICA_POOL_DB_BATCH_WRITE_SIZE", "64") or 64),
        )
        self._db_write_flush_seconds = max(
            0.05,
            float(os.getenv("ANIMICA_POOL_DB_BATCH_FLUSH_SECONDS", "0.25") or 0.25),
        )
        self._db_pending_statements = 0
        self._db_last_commit_ts = self._started
        self._housekeep_interval_seconds = max(
            1.0,
            float(os.getenv("ANIMICA_POOL_HOUSEKEEP_INTERVAL_SECONDS", "300") or 300),
        )
        self._last_housekeep_ts = self._started
        self._share_retention_seconds = max(
            0.0,
            float(
                os.getenv(
                    "ANIMICA_POOL_SHARE_RETENTION_SECONDS",
                    str(int(48 * 3600)),
                )
                or 48 * 3600
            ),
        )
        self._ledger_retention_seconds = max(
            0.0,
            float(
                os.getenv(
                    "ANIMICA_POOL_LEDGER_RETENTION_SECONDS",
                    str(int(7 * 24 * 3600)),
                )
                or 7 * 24 * 3600
            ),
        )
        self._payout_history_retention_seconds = max(
            0.0,
            float(
                os.getenv(
                    "ANIMICA_POOL_PAYOUT_HISTORY_RETENTION_SECONDS",
                    str(int(30 * 24 * 3600)),
                )
                or 30 * 24 * 3600
            ),
        )
        self._worker_cache_ttl_seconds = max(
            30.0,
            float(
                os.getenv("ANIMICA_POOL_WORKER_CACHE_TTL_SECONDS", str(int(6 * 3600)))
                or 6 * 3600
            ),
        )
        self._worker_cache_max_entries = max(
            100,
            int(os.getenv("ANIMICA_POOL_WORKER_CACHE_MAX_ENTRIES", "5000") or 5000),
        )
        self._payout_interval_seconds = max(
            0.0, float(getattr(config, "payout_interval_seconds", 0.0) or 0.0)
        )
        self._inactive_share_grace_seconds = max(
            0.0,
            float(os.getenv("ANIMICA_POOL_INACTIVE_SHARE_GRACE_SECONDS", "300")),
        )
        self._inactive_share_halflife_seconds = max(
            0.0,
            float(os.getenv("ANIMICA_POOL_INACTIVE_SHARE_HALFLIFE_SECONDS", "1800")),
        )
        self._next_payout_at: Optional[float] = (
            self._started + self._payout_interval_seconds
            if self._payout_interval_seconds > 0
            else None
        )
        self._last_payout_at: Optional[float] = None
        self._last_payout_count: int = 0
        self._last_payout_error: Optional[str] = None
        self._ledger_record_pps_share_credit = self._env_flag(
            "ANIMICA_POOL_LEDGER_RECORD_PPS_SHARES",
            default=False,
        )

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
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA temp_store=MEMORY")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS shares (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL,
                worker TEXT,
                address TEXT,
                mode TEXT NOT NULL DEFAULT 'pps',
                difficulty REAL,
                status TEXT,
                job_id TEXT,
                height INTEGER,
                is_block INTEGER,
                tx_count INTEGER,
                work REAL
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
                raw_tx TEXT,
                nonce INTEGER,
                status TEXT NOT NULL,
                error TEXT,
                retry_count INTEGER NOT NULL DEFAULT 0,
                last_retry_ts REAL,
                next_retry_ts REAL,
                confirmed_ts REAL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS rental_assignments (
                id TEXT PRIMARY KEY,
                owner_worker TEXT NOT NULL,
                owner_address TEXT NOT NULL,
                renter_anm_address TEXT,
                renter_xmr_anim_address TEXT,
                anm_mode TEXT,
                coins TEXT NOT NULL,
                start_ts REAL NOT NULL,
                end_ts REAL NOT NULL,
                status TEXT NOT NULL,
                created_ts REAL NOT NULL
            )
            """
        )
        self._ensure_column(conn, "blocks", "worker", "TEXT")
        self._ensure_column(conn, "blocks", "address", "TEXT")
        self._ensure_column(conn, "blocks", "reward", "INTEGER")
        self._ensure_column(conn, "shares", "mode", "TEXT NOT NULL DEFAULT 'pps'")
        # Per-share expected raw SHA3 hashes (2**256 / share_target_int). Summed
        # over a window this yields the TRUE raw hashrate (matches xmrig's H/s),
        # unlike the legacy `difficulty` column which is just the share ratio.
        self._ensure_column(conn, "shares", "work", "REAL")
        self._ensure_column(conn, "worker_balances", "paid_out", "INTEGER NOT NULL DEFAULT 0")
        self._ensure_column(conn, "payouts", "raw_tx", "TEXT")
        self._ensure_column(conn, "payouts", "nonce", "INTEGER")
        self._ensure_column(conn, "payouts", "retry_count", "INTEGER NOT NULL DEFAULT 0")
        self._ensure_column(conn, "payouts", "last_retry_ts", "REAL")
        self._ensure_column(conn, "payouts", "next_retry_ts", "REAL")
        self._ensure_column(conn, "payouts", "confirmed_ts", "REAL")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_payouts_mode_status_ts ON payouts(mode, status, ts)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_payouts_mode_tx_hash ON payouts(mode, tx_hash)"
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_shares_status_ts ON shares(status, ts)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_shares_address_ts ON shares(address, ts)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_shares_worker_ts ON shares(worker, ts)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_blocks_ts ON blocks(ts)")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_worker_balances_mode_address ON worker_balances(mode, address)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_ledger_mode_ts ON accounting_ledger(mode, ts)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_rental_active "
            "ON rental_assignments(status, owner_worker, owner_address, end_ts)"
        )
        conn.execute("PRAGMA optimize")
        conn.commit()
        return conn

    @staticmethod
    def _expected_share_work(job: object, difficulty: float) -> float:
        """Expected raw SHA3 hashes the miner performed to find this share.

        A share is accepted when ``int256(sha3_256(prefix||nonce)) <=
        share_target_int``; for a uniform 256-bit digest the expected number of
        attempts is ``2**256 / share_target_int``. Summing this over accepted
        shares in a time window gives the TRUE raw hashrate (H/s) and matches
        what xmrig reports — unlike the stored ``difficulty`` ratio (~1.0), which
        is unrelated to hash count. Falls back to the Bitcoin-style
        ``difficulty * 2**32`` only when the target isn't available.
        """
        try:
            target_int = int(getattr(job, "share_target_int", 0) or 0)
        except (TypeError, ValueError):
            target_int = 0
        if target_int > 0:
            return float(_TWO_POW_256) / float(target_int)
        return max(0.0, float(difficulty or 0.0)) * 4_294_967_296.0

    @staticmethod
    def _ensure_column(
        conn: sqlite3.Connection, table: str, column: str, column_type: str
    ) -> None:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        existing = {str(row[1]) for row in rows}
        if column in existing:
            return
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_type}")

    def _commit_db_now(self, *, now_ts: Optional[float] = None) -> None:
        if self._db is None:
            return
        self._db.commit()
        self._db_pending_statements = 0
        self._db_last_commit_ts = float(now_ts if now_ts is not None else time.time())

    def _record_db_write(
        self,
        *,
        statements: int = 1,
        defer_commit: bool = False,
        now_ts: Optional[float] = None,
    ) -> None:
        if self._db is None:
            return
        current_ts = float(now_ts if now_ts is not None else time.time())
        self._db_pending_statements += max(1, int(statements or 1))
        if not defer_commit:
            self._commit_db_now(now_ts=current_ts)
            return
        if self._db_pending_statements >= self._db_write_batch_size:
            self._commit_db_now(now_ts=current_ts)
            return
        if (current_ts - self._db_last_commit_ts) >= self._db_write_flush_seconds:
            self._commit_db_now(now_ts=current_ts)

    def _flush_pending_db_writes(self, *, force: bool = False) -> None:
        if self._db is None or self._db_pending_statements <= 0:
            return
        now_ts = time.time()
        if not force and (now_ts - self._db_last_commit_ts) < self._db_write_flush_seconds:
            return
        with self._db_lock:
            if self._db_pending_statements > 0:
                self._commit_db_now(now_ts=now_ts)

    def _prune_read_cache(self, now_ts: Optional[float] = None) -> None:
        ttl = float(self._read_cache_ttl)
        if ttl <= 0:
            self._read_cache.clear()
            return
        now = float(now_ts if now_ts is not None else time.time())
        cutoff = now - ttl
        stale_keys = [k for k, (ts, _value) in self._read_cache.items() if float(ts) < cutoff]
        for stale in stale_keys:
            self._read_cache.pop(stale, None)
        while len(self._read_cache) > self._read_cache_max_entries:
            self._read_cache.popitem(last=False)

    def _prune_worker_balance_cache(self, *, now_ts: float) -> None:
        if not self._worker_balances_cache:
            return

        ttl = float(self._worker_cache_ttl_seconds)
        stale_cutoff = float(now_ts) - ttl
        removable: list[tuple[tuple[str, str, str], float]] = []
        for key, row in self._worker_balances_cache.items():
            updated_ts = float(row.get("updated_ts") or 0.0)
            if updated_ts <= 0 or updated_ts >= stale_cutoff:
                continue
            gross_credit = int(row.get("total_credit") or 0)
            paid_out = int(row.get("paid_out") or 0)
            available_credit = max(0, gross_credit - paid_out)
            if available_credit > 0:
                continue
            removable.append((key, updated_ts))

        for key, _updated_ts in removable:
            self._worker_balances_cache.pop(key, None)

        overflow = len(self._worker_balances_cache) - self._worker_cache_max_entries
        if overflow <= 0:
            return

        candidates: list[tuple[tuple[str, str, str], float, int]] = []
        for key, row in self._worker_balances_cache.items():
            updated_ts = float(row.get("updated_ts") or 0.0)
            gross_credit = int(row.get("total_credit") or 0)
            paid_out = int(row.get("paid_out") or 0)
            available_credit = max(0, gross_credit - paid_out)
            if self._db is None and available_credit > 0:
                # In no-DB mode, positive balances only exist in memory.
                continue
            candidates.append((key, updated_ts, available_credit))
        candidates.sort(key=lambda item: (item[2], item[1]))
        for key, _updated_ts, _available_credit in candidates[:overflow]:
            self._worker_balances_cache.pop(key, None)

    def _prune_payout_records(self, *, now_ts: float) -> None:
        if self._db is not None or not self._payout_records:
            return
        retention = float(self._payout_history_retention_seconds)
        if retention <= 0:
            return
        cutoff = float(now_ts) - retention
        keep: list[dict[str, object]] = []
        for record in self._payout_records:
            status = str(record.get("status") or "").strip().lower()
            timestamp = float(record.get("timestamp") or 0.0)
            if status not in {"confirmed", "dropped", "failed"}:
                keep.append(record)
                continue
            if timestamp <= 0 or timestamp >= cutoff:
                keep.append(record)
        self._payout_records = keep

    def _prune_db_history(self, *, now_ts: float) -> None:
        if self._db is None:
            return
        deleted = False
        with self._db_lock:
            if self._share_retention_seconds > 0:
                share_cutoff = float(now_ts) - float(self._share_retention_seconds)
                deleted = (
                    self._db.execute("DELETE FROM shares WHERE ts < ?", (share_cutoff,)).rowcount
                    > 0
                ) or deleted
            if self._ledger_retention_seconds > 0:
                ledger_cutoff = float(now_ts) - float(self._ledger_retention_seconds)
                deleted = (
                    self._db.execute(
                        "DELETE FROM accounting_ledger WHERE ts < ?",
                        (ledger_cutoff,),
                    ).rowcount
                    > 0
                ) or deleted
            if self._payout_history_retention_seconds > 0:
                payout_cutoff = float(now_ts) - float(self._payout_history_retention_seconds)
                deleted = (
                    self._db.execute(
                        """
                        DELETE FROM payouts
                        WHERE ts < ?
                          AND status IN ('confirmed', 'dropped', 'failed')
                        """,
                        (payout_cutoff,),
                    ).rowcount
                    > 0
                ) or deleted
            if deleted:
                self._commit_db_now(now_ts=now_ts)

    def _run_housekeeping(self, *, now_ts: Optional[float] = None, force: bool = False) -> None:
        now = float(now_ts if now_ts is not None else time.time())
        self._flush_pending_db_writes(force=force)
        if not force and (now - self._last_housekeep_ts) < self._housekeep_interval_seconds:
            return
        self._last_housekeep_ts = now
        self._prune_read_cache(now)
        self._prune_worker_balance_cache(now_ts=now)
        self._prune_payout_records(now_ts=now)
        self._prune_db_history(now_ts=now)
        self._sweep_expired_rentals(now_ts=now)

    def _read_cache_get(self, key: str) -> Optional[object]:
        ttl = float(self._read_cache_ttl)
        if ttl <= 0:
            return None
        self._prune_read_cache()
        cached = self._read_cache.get(key)
        if cached is None:
            return None
        ts, value = cached
        if (time.time() - float(ts)) > ttl:
            self._read_cache.pop(key, None)
            return None
        self._read_cache.move_to_end(key, last=True)
        return value

    def _read_cache_set(self, key: str, value: object) -> None:
        ttl = float(self._read_cache_ttl)
        if ttl <= 0:
            return
        now = time.time()
        self._prune_read_cache(now)
        self._read_cache[key] = (now, value)
        self._read_cache.move_to_end(key, last=True)
        while len(self._read_cache) > self._read_cache_max_entries:
            self._read_cache.popitem(last=False)

    # ------------------------------------------------------------------
    # Rig-rental redirect engine
    # ------------------------------------------------------------------
    _RENTAL_COLUMNS = (
        "id, owner_worker, owner_address, renter_anm_address, "
        "renter_xmr_anim_address, anm_mode, coins, start_ts, end_ts, "
        "status, created_ts"
    )

    @staticmethod
    def rig_id_for(worker: str, address: str) -> str:
        raw = f"{worker}\x1f{address}".encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    @staticmethod
    def _rental_key(worker: object, address: object) -> Tuple[str, str]:
        return (str(worker or ""), str(address or ""))

    def _rental_row_to_dict(self, row: Tuple[object, ...]) -> Dict[str, object]:
        return {
            "id": str(row[0]),
            "owner_worker": str(row[1] or ""),
            "owner_address": str(row[2] or ""),
            "renter_anm_address": row[3],
            "renter_xmr_anim_address": row[4],
            "anm_mode": row[5],
            "coins": str(row[6] or ""),
            "start_ts": float(row[7] or 0.0),
            "end_ts": float(row[8] or 0.0),
            "status": str(row[9] or ""),
            "created_ts": float(row[10] or 0.0),
        }

    def _load_rental_index(self) -> None:
        if self._db is None:
            return
        with self._db_lock:
            rows = self._db.execute(
                f"SELECT {self._RENTAL_COLUMNS} FROM rental_assignments "
                "WHERE status='active'"
            ).fetchall()
        index: Dict[Tuple[str, str], Dict[str, object]] = {}
        for row in rows:
            rec = self._rental_row_to_dict(row)
            index[self._rental_key(rec["owner_worker"], rec["owner_address"])] = rec
        with self._rental_lock:
            self._rental_index = index

    def _mark_rental_status(self, rental_id: str, status: str) -> None:
        if self._db is None:
            return
        with self._db_lock:
            self._db.execute(
                "UPDATE rental_assignments SET status=? WHERE id=?",
                (status, str(rental_id)),
            )
            self._db.commit()

    def _active_rental_for(
        self, worker: str, address: str, now: float
    ) -> Optional[Dict[str, object]]:
        """Return the active rental for an owner rig, or None.

        Lazily drops (and marks completed) an assignment whose window has
        elapsed, so the rig's credit reverts to the owner the moment the
        rental ends — no reconnect, no waiting for the housekeeping sweep.
        """
        key = self._rental_key(worker, address)
        expired_id: Optional[str] = None
        result: Optional[Dict[str, object]] = None
        with self._rental_lock:
            rec = self._rental_index.get(key)
            if rec is not None and rec.get("status") == "active":
                if now >= float(rec["end_ts"]):
                    self._rental_index.pop(key, None)
                    expired_id = str(rec["id"])
                elif now >= float(rec["start_ts"]):
                    result = dict(rec)
        if expired_id is not None:
            self._mark_rental_status(expired_id, "completed")
        return result

    def active_rental(self, worker: str, address: str) -> Optional[Dict[str, object]]:
        """Public rental lookup for the XMR stratum redirect (and others).
        Returns the active assignment for an owner rig, or None."""
        return self._active_rental_for(worker, address, time.time())

    def _sweep_expired_rentals(self, *, now_ts: float) -> None:
        if self._db is None:
            return
        with self._db_lock:
            self._db.execute(
                "UPDATE rental_assignments SET status='completed' "
                "WHERE status='active' AND end_ts <= ?",
                (float(now_ts),),
            )
            self._db.commit()
        with self._rental_lock:
            for key, rec in list(self._rental_index.items()):
                if float(rec.get("end_ts") or 0.0) <= float(now_ts):
                    self._rental_index.pop(key, None)

    def upsert_rental_assignment(
        self,
        *,
        id: str,
        owner_worker: str,
        owner_address: str,
        coins: str,
        start_ts: float,
        end_ts: float,
        renter_anm_address: Optional[str] = None,
        renter_xmr_anim_address: Optional[str] = None,
        anm_mode: Optional[str] = None,
    ) -> Dict[str, object]:
        if self._db is None:
            raise RuntimeError("pool DB not configured")
        coins_norm = str(coins or "").upper()
        if coins_norm not in {"ANM", "XMR", "BOTH"}:
            raise ValueError("coins must be ANM, XMR, or BOTH")
        mode_norm: Optional[str] = None
        if anm_mode:
            mode_norm = str(anm_mode).strip().lower()
            if mode_norm not in {"pps", "solo"}:
                raise ValueError("anm_mode must be pps or solo")
        rec: Dict[str, object] = {
            "id": str(id),
            "owner_worker": str(owner_worker or ""),
            "owner_address": str(owner_address or ""),
            "renter_anm_address": renter_anm_address or None,
            "renter_xmr_anim_address": renter_xmr_anim_address or None,
            "anm_mode": mode_norm,
            "coins": coins_norm,
            "start_ts": float(start_ts),
            "end_ts": float(end_ts),
            "status": "active",
            "created_ts": time.time(),
        }
        with self._db_lock:
            existing = self._db.execute(
                "SELECT id FROM rental_assignments WHERE status='active' "
                "AND owner_worker=? AND owner_address=? AND id != ?",
                (rec["owner_worker"], rec["owner_address"], rec["id"]),
            ).fetchone()
            if existing is not None:
                raise RentalConflict(
                    f"rig already has active rental {existing[0]}"
                )
            self._db.execute(
                f"INSERT INTO rental_assignments ({self._RENTAL_COLUMNS}) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET "
                "owner_worker=excluded.owner_worker, "
                "owner_address=excluded.owner_address, "
                "renter_anm_address=excluded.renter_anm_address, "
                "renter_xmr_anim_address=excluded.renter_xmr_anim_address, "
                "anm_mode=excluded.anm_mode, coins=excluded.coins, "
                "start_ts=excluded.start_ts, end_ts=excluded.end_ts, "
                "status=excluded.status",
                (
                    rec["id"], rec["owner_worker"], rec["owner_address"],
                    rec["renter_anm_address"], rec["renter_xmr_anim_address"],
                    rec["anm_mode"], rec["coins"], rec["start_ts"],
                    rec["end_ts"], rec["status"], rec["created_ts"],
                ),
            )
            self._db.commit()
        with self._rental_lock:
            self._rental_index[
                self._rental_key(rec["owner_worker"], rec["owner_address"])
            ] = rec
        return rec

    def cancel_rental_assignment(self, rental_id: str) -> bool:
        if self._db is None:
            return False
        with self._db_lock:
            cur = self._db.execute(
                "UPDATE rental_assignments SET status='cancelled' "
                "WHERE id=? AND status='active'",
                (str(rental_id),),
            )
            changed = int(cur.rowcount or 0)
            self._db.commit()
        with self._rental_lock:
            for key, rec in list(self._rental_index.items()):
                if str(rec.get("id")) == str(rental_id):
                    self._rental_index.pop(key, None)
        return changed > 0

    def get_rental_assignment(self, rental_id: str) -> Optional[Dict[str, object]]:
        if self._db is None:
            return None
        with self._db_lock:
            row = self._db.execute(
                f"SELECT {self._RENTAL_COLUMNS} FROM rental_assignments WHERE id=?",
                (str(rental_id),),
            ).fetchone()
        if row is None:
            return None
        return self._rental_row_to_dict(row)

    def rentable_rigs(
        self, *, online_window_seconds: float = 600.0
    ) -> List[Dict[str, object]]:
        """List rigs (worker, address) seen recently, with live stats.

        The owner's payout address is intentionally NOT exposed; rigs are
        referenced by an opaque ``rig_id = sha256(worker|address)``.
        """
        if self._db is None:
            return []
        now = time.time()
        cutoff_online = now - float(online_window_seconds)
        cutoff_1m = now - 60
        cutoff_15m = now - 900
        cutoff_1h = now - 3600
        cutoff_max = min(cutoff_online, cutoff_1h)
        with self._db_lock:
            rows = self._db.execute(
                """
                SELECT worker, address,
                       SUM(CASE WHEN status='accepted' AND ts >= ? THEN difficulty ELSE 0 END),
                       SUM(CASE WHEN status='accepted' AND ts >= ? THEN difficulty ELSE 0 END),
                       SUM(CASE WHEN status='accepted' AND ts >= ? THEN difficulty ELSE 0 END),
                       MAX(ts), MAX(mode)
                FROM shares
                WHERE ts >= ? AND address LIKE 'anim1%'
                  AND worker IS NOT NULL AND worker != ''
                GROUP BY worker, address
                """,
                (cutoff_1m, cutoff_15m, cutoff_1h, cutoff_max),
            ).fetchall()
        rigs: List[Dict[str, object]] = []
        for worker, address, d1, d15, d60, last_ts, mode in rows:
            last = float(last_ts or 0.0)
            key = self._rental_key(worker, address)
            with self._rental_lock:
                rented = key in self._rental_index
            rigs.append(
                {
                    "rig_id": self.rig_id_for(str(worker), str(address)),
                    "worker_name": str(worker),
                    "hashrate_1m": float(d1 or 0.0) / 60,
                    "hashrate_15m": float(d15 or 0.0) / 900,
                    "hashrate_1h": float(d60 or 0.0) / 3600,
                    "last_share_at": last,
                    "online": bool(last >= cutoff_online),
                    "pool_mode": str(mode or "pps"),
                    "rented": bool(rented),
                }
            )
        return rigs

    def rig_stats(
        self,
        *,
        rig_id: Optional[str] = None,
        worker: Optional[str] = None,
        address: Optional[str] = None,
        online_window_seconds: float = 600.0,
    ) -> Optional[Dict[str, object]]:
        """Live stats for one rig, by opaque rig_id or by (worker, address)."""
        for rig in self.rentable_rigs(online_window_seconds=online_window_seconds):
            if rig_id is not None and rig["rig_id"] == rig_id:
                return rig
            if (
                worker is not None
                and address is not None
                and rig["rig_id"] == self.rig_id_for(str(worker), str(address))
            ):
                return rig
        return None

    def rig_window_stats(
        self, *, worker: str, address: str, start_ts: float, end_ts: float
    ) -> Dict[str, object]:
        """Measured delivery within a rental window, for pro-rata settlement.

        Counts distinct one-minute buckets that contain at least one accepted
        share; measured uptime = buckets x 60s, capped at the window length.
        """
        window = max(0.0, float(end_ts) - float(start_ts))
        if self._db is None:
            return {"measured_uptime_seconds": 0, "active_buckets": 0, "window_seconds": int(window)}
        with self._db_lock:
            rows = self._db.execute(
                """
                SELECT CAST(ts/60 AS INTEGER) AS bucket
                FROM shares
                WHERE worker=? AND address=? AND status='accepted'
                  AND ts >= ? AND ts < ?
                GROUP BY bucket
                """,
                (str(worker), str(address), float(start_ts), float(end_ts)),
            ).fetchall()
        buckets = len(rows)
        measured = min(window, buckets * 60.0)
        return {
            "measured_uptime_seconds": int(measured),
            "active_buckets": buckets,
            "window_seconds": int(window),
        }

    async def record_share(
        self,
        session: object,
        job: object,
        submit_params: Dict[str, object],
        ok: bool,
        reason: Optional[str],
        is_block: bool,
        tx_count: int,
    ) -> None:
        now = time.time()
        self._run_housekeeping(now_ts=now)
        reward = self._expected_reward(job)
        session_id = str(
            getattr(session, "session_id", None)
            or getattr(session, "extranonce1", None)
            or ""
        ).strip()
        worker_hint = getattr(session, "worker", None)
        parsed_worker, parsed_address = self._parse_worker_identity(worker_hint)
        worker = self._normalize_worker(worker_hint or parsed_worker, session_id)
        address_hint = getattr(session, "address", None) or parsed_address
        address = self._normalize_address(address_hint)
        share_mode = self._resolve_share_mode(session=session, submit_params=submit_params)

        # Rig-rental redirect: if this owner rig is under an active rental,
        # credit the renter (and honour the renter's chosen ANM mode) while
        # leaving the raw shares/blocks rows attributed to the true owner so
        # the rig's own hashrate history stays intact. The instant the window
        # ends, _active_rental_for returns None and credit reverts to the
        # owner automatically — the rig goes back to mining for itself.
        credit_address = address
        credit_mode = share_mode
        rental = self._active_rental_for(worker, address, now)
        if rental is not None:
            renter_anm = rental.get("renter_anm_address")
            coins = str(rental.get("coins") or "")
            if renter_anm and coins in ("ANM", "BOTH"):
                credit_address = str(renter_anm)
                anm_mode = str(rental.get("anm_mode") or "").strip().lower()
                if anm_mode in {"pps", "solo"}:
                    credit_mode = anm_mode

        accepted_block = bool(ok and is_block)
        difficulty_value = submit_params.get("d_ratio")
        if difficulty_value in (None, ""):
            difficulty_value = submit_params.get("shareTarget")
        if difficulty_value in (None, ""):
            difficulty_value = (
                1.0 if accepted_block else float(getattr(job, "share_target", 0.0))
            )
        difficulty = float(difficulty_value)
        if accepted_block:
            # Block-winning shares should be credited at full ratio even when
            # legacy submit payloads omit d_ratio.
            difficulty = max(1.0, difficulty)
        # Raw SHA3 hashes this share represents (2**256 / share_target_int).
        # Summed over a window this gives the true H/s — see _expected_share_work.
        share_work = self._expected_share_work(job, difficulty)
        job_id = str(
            getattr(job, "job_id", None) or submit_params.get("jobId") or "unknown-job"
        )
        header = getattr(job, "header", None)
        header_map = header if isinstance(header, dict) else {}
        event: ShareEvent = {
            "timestamp": now,
            "session_id": session_id or "unknown-session",
            "worker": worker,
            "address": address,
            "difficulty": difficulty,
            "work": share_work,
            "status": "accepted" if ok else "rejected",
            "reason": reason,
            "pool_mode": share_mode,
            "job_id": job_id,
            "height": submit_params.get("height")
            or header_map.get("number")
            or header_map.get("height"),
        }
        self._share_events.append(event)
        self._persist_share(
            event,
            pool_mode=share_mode,
            is_block=accepted_block,
            tx_count=tx_count,
            reward=reward,
            defer_commit=True,
            now_ts=now,
        )
        self._apply_accounting_for_share(
            ts=now,
            worker=worker,
            address=credit_address,
            job_id=job_id,
            ok=ok,
            is_block=accepted_block,
            reward=reward,
            difficulty=difficulty,
            reason=reason,
            share_mode=credit_mode,
            defer_commit=True,
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
                    "job_id": job_id,
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
        pool_mode: str,
        is_block: bool,
        tx_count: int,
        reward: int,
        defer_commit: bool = False,
        now_ts: Optional[float] = None,
    ) -> None:
        if self._db is None:
            return

        with self._db_lock:
            statements = 1
            self._db.execute(
                """
                INSERT INTO shares (ts, worker, address, mode, difficulty, status, job_id, height, is_block, tx_count, work)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.get("timestamp"),
                    event.get("worker"),
                    event.get("address"),
                    self._normalize_share_mode(pool_mode, default="pps"),
                    event.get("difficulty"),
                    event.get("status"),
                    event.get("job_id"),
                    event.get("height"),
                    1 if is_block else 0,
                    tx_count,
                    event.get("work"),
                ),
            )
            if is_block:
                statements += 1
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
            self._record_db_write(
                statements=statements,
                defer_commit=defer_commit,
                now_ts=now_ts,
            )

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
                self._commit_db_now()

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
        return self._int_value(
            raw.get("reward")
            or raw.get("blockReward")
            or raw.get("coinbaseValue")
            or raw.get("coinbase_value")
            or raw.get("coinbasevalue")
        )

    def _expected_reward(self, job: object) -> int:
        return self._reward_from_raw(getattr(job, "raw", None))

    @staticmethod
    def _parse_worker_identity(raw_worker: object) -> tuple[Optional[str], Optional[str]]:
        text = str(raw_worker or "").strip()
        if not text:
            return None, None

        split_pos = -1
        for delim in (".", "/", ":"):
            idx = text.find(delim)
            if idx > 0 and (split_pos < 0 or idx < split_pos):
                split_pos = idx

        if split_pos > 0:
            head = text[:split_pos].strip()
            tail = text[split_pos + 1 :].strip()
        else:
            head = text
            tail = ""

        if head.startswith("anim1"):
            worker = tail or text
            return worker, head
        if text.startswith("anim1"):
            return text, text
        return text, None

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
    def _looks_like_address(value: object) -> bool:
        text = str(value or "").strip().lower()
        if not text:
            return False
        return text.startswith("anim1") or text.startswith("0x") or text.startswith(
            "system:"
        )

    @staticmethod
    def _normalize_share_mode(raw_mode: object, *, default: str = "pps") -> str:
        mode = str(raw_mode or "").strip().lower()
        if mode in {"pps", "solo"}:
            return mode
        fallback = str(default or "pps").strip().lower()
        if fallback in {"pps", "solo"}:
            return fallback
        return "pps"

    def _pick_share_mode(
        self,
        *candidates: object,
        default: Optional[str] = None,
    ) -> str:
        if self._pool_mode in {"pps", "solo"}:
            return self._pool_mode
        for candidate in candidates:
            mode = str(candidate or "").strip().lower()
            if mode in {"pps", "solo"}:
                return mode
        fallback = default
        if fallback is None:
            fallback = "pps" if self._pool_mode == "both" else self._pool_mode
        return self._normalize_share_mode(fallback, default="pps")

    def _resolve_share_mode(
        self,
        *,
        session: object,
        submit_params: Dict[str, object],
    ) -> str:
        return self._pick_share_mode(
            submit_params.get("_pool_mode"),
            submit_params.get("pool_mode"),
            submit_params.get("mode"),
            getattr(session, "pool_mode", None),
            default="pps",
        )

    def _session_snapshots(self) -> List[object]:
        snapshots = getattr(self._server, "session_snapshots", None)
        if not callable(snapshots):
            return []
        try:
            sessions = snapshots() or []
        except Exception:
            return []
        return list(sessions)

    def _session_worker_and_address(self, session: object) -> tuple[str, str]:
        if isinstance(session, dict):
            raw_worker = session.get("worker") or session.get("session_id")
            raw_address = session.get("address")
            session_id = session.get("session_id")
        else:
            raw_worker = getattr(session, "worker", None) or getattr(
                session, "session_id", None
            )
            raw_address = getattr(session, "address", None)
            session_id = getattr(session, "session_id", None)

        parsed_worker, parsed_address = self._parse_worker_identity(raw_worker)
        worker = self._normalize_worker(raw_worker or parsed_worker, str(session_id or ""))
        address = self._normalize_address(raw_address or parsed_address)
        return worker, address

    def _session_summary_by_address(self) -> Dict[str, Dict[str, object]]:
        summary: Dict[str, Dict[str, object]] = {}
        for session in self._session_snapshots():
            worker, address = self._session_worker_and_address(session)
            if not address:
                continue

            if isinstance(session, dict):
                shares_accepted = self._int_value(session.get("shares_accepted"))
                shares_rejected = self._int_value(session.get("shares_rejected"))
                last_share_at = float(session.get("last_share_at") or 0.0)
                difficulty = session.get("current_difficulty") or session.get(
                    "share_target"
                )
                connected_since = float(session.get("connected_since") or 0.0)
                session_mode = self._pick_share_mode(
                    session.get("pool_mode"),
                    default="pps",
                )
            else:
                shares_accepted = self._int_value(
                    getattr(session, "shares_accepted", 0)
                )
                shares_rejected = self._int_value(
                    getattr(session, "shares_rejected", 0)
                )
                last_share_at = float(getattr(session, "last_share_at", 0.0) or 0.0)
                difficulty = getattr(session, "current_difficulty", None) or getattr(
                    session, "share_target", None
                )
                connected_since = float(
                    getattr(session, "connected_since", 0.0) or 0.0
                )
                session_mode = self._pick_share_mode(
                    getattr(session, "pool_mode", None),
                    default="pps",
                )

            current = summary.get(address)
            if current is None:
                summary[address] = {
                    "address": address,
                    "worker_name": worker,
                    "shares_accepted": shares_accepted,
                    "shares_rejected": shares_rejected,
                    "last_share_at": last_share_at,
                    "difficulty": difficulty,
                    "connected_since": connected_since,
                    "pool_mode": session_mode,
                    "mode_counts": {session_mode: 1},
                }
                continue

            current["shares_accepted"] = int(current.get("shares_accepted") or 0) + int(
                shares_accepted
            )
            current["shares_rejected"] = int(current.get("shares_rejected") or 0) + int(
                shares_rejected
            )
            current["last_share_at"] = max(
                float(current.get("last_share_at") or 0.0), last_share_at
            )
            if (
                str(current.get("worker_name") or "").strip() in ("", "unknown-worker")
                and str(worker or "").strip()
            ):
                current["worker_name"] = worker
            if current.get("difficulty") in (None, "") and difficulty not in (None, ""):
                current["difficulty"] = difficulty
            mode_counts = current.get("mode_counts")
            if not isinstance(mode_counts, dict):
                mode_counts = {}
            mode_counts[session_mode] = int(mode_counts.get(session_mode) or 0) + 1
            current["mode_counts"] = mode_counts
            ordered_modes = sorted(
                mode_counts.items(),
                key=lambda item: (int(item[1]), 1 if item[0] == "pps" else 0, item[0]),
                reverse=True,
            )
            if ordered_modes:
                current["pool_mode"] = ordered_modes[0][0]
            if connected_since > 0:
                existing_since = float(current.get("connected_since") or 0.0)
                current["connected_since"] = (
                    connected_since
                    if existing_since <= 0
                    else min(existing_since, connected_since)
                )
        return summary

    def _resolve_address_for_identity(self, identity: str) -> Optional[str]:
        key = str(identity or "").strip()
        if not key:
            return None

        normalized_key = self._normalize_address(key)
        if self._looks_like_address(key) and self._payout_eligible_address(normalized_key):
            return normalized_key

        _parsed_worker, parsed_address = self._parse_worker_identity(key)
        normalized_parsed_address = self._normalize_address(parsed_address)
        if self._looks_like_address(parsed_address) and self._payout_eligible_address(
            normalized_parsed_address
        ):
            return normalized_parsed_address

        for session in self._session_snapshots():
            worker, address = self._session_worker_and_address(session)
            if not self._payout_eligible_address(address):
                continue
            if key == worker:
                return address
            parsed_worker, _ = self._parse_worker_identity(worker)
            if parsed_worker and parsed_worker == key:
                return address

        for event in reversed(self._share_events):
            event_address = self._normalize_address(event.get("address"))  # type: ignore[arg-type]
            if not self._payout_eligible_address(event_address):
                continue
            event_worker = str(event.get("worker") or "").strip()
            if key == event_worker:
                return event_address
            parsed_worker, _ = self._parse_worker_identity(event_worker)
            if parsed_worker and parsed_worker == key:
                return event_address

        if self._db is not None:
            patterns = (key, f"%.{key}", f"%/{key}", f"%:{key}")
            with self._db_lock:
                rows = self._db.execute(
                    """
                    SELECT address, worker
                    FROM shares
                    WHERE address IS NOT NULL
                      AND address != ''
                      AND (worker = ? OR worker LIKE ? OR worker LIKE ? OR worker LIKE ?)
                    ORDER BY ts DESC
                    LIMIT 20
                    """,
                    patterns,
                ).fetchall()
                if not rows:
                    rows = self._db.execute(
                        """
                        SELECT address, worker
                        FROM worker_balances
                        WHERE mode = ?
                          AND (worker = ? OR worker LIKE ? OR worker LIKE ? OR worker LIKE ?)
                        ORDER BY updated_ts DESC
                        LIMIT 20
                        """,
                        (self._pool_mode, *patterns),
                    ).fetchall()
            for address, worker in rows:
                resolved_address = self._normalize_address(address)
                if not self._payout_eligible_address(resolved_address):
                    continue
                worker_text = str(worker or "").strip()
                if worker_text == key:
                    return resolved_address
                parsed_worker, _ = self._parse_worker_identity(worker_text)
                if parsed_worker and parsed_worker == key:
                    return resolved_address
        return None

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

    def _ena_fee_split(
        self, miner_address: str, pps_credit: int, solo_credit: int
    ) -> Tuple[int, int]:
        """Return (pps_fee, solo_fee) to route to the ENA treasury for one share.

        Disabled (returns 0,0) when no fee is configured, no treasury address is
        set, or the credited address is itself the treasury (no self-skim).
        """
        if self._ena_fee_bps <= 0 or not self._ena_treasury_address:
            return 0, 0
        if miner_address == self._ena_treasury_address:
            return 0, 0
        bps = self._ena_fee_bps
        pps_fee = (int(pps_credit) * bps) // 10_000 if pps_credit > 0 else 0
        solo_fee = (int(solo_credit) * bps) // 10_000 if solo_credit > 0 else 0
        return pps_fee, solo_fee

    def _credit_ena_treasury_fee(
        self,
        *,
        ts: float,
        from_worker: str,
        from_address: str,
        job_id: str,
        accounting_mode: str,
        ena_fee_pps: int,
        ena_fee_solo: int,
        defer_commit: bool = False,
    ) -> None:
        """Credit a skimmed ENA training-treasury fee to the treasury balance."""
        if ena_fee_pps <= 0 and ena_fee_solo <= 0:
            return
        self._apply_balance_delta(
            ts=ts,
            worker=self._ena_fee_worker,
            address=self._ena_treasury_address,
            pps_credit=ena_fee_pps,
            solo_credit=ena_fee_solo,
            defer_commit=defer_commit,
        )
        self._record_accounting_event(
            ts=ts,
            worker=self._ena_fee_worker,
            address=self._ena_treasury_address,
            event="ena_treasury_fee",
            amount=int(ena_fee_pps) + int(ena_fee_solo),
            job_id=job_id,
            details={
                "fee_bps": self._ena_fee_bps,
                "from_worker": from_worker,
                "from_address": from_address,
                "pool_mode": accounting_mode,
            },
            defer_commit=defer_commit,
        )

    def _apply_balance_delta(
        self,
        *,
        ts: float,
        worker: str,
        address: str,
        last_share_ts: Optional[float] = None,
        pps_credit: int = 0,
        solo_credit: int = 0,
        paid_out_delta: int = 0,
        accepted_shares_delta: int = 0,
        accepted_blocks_delta: int = 0,
        rejected_shares_delta: int = 0,
        defer_commit: bool = False,
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
                "last_share_ts": 0.0,
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
        if last_share_ts is not None:
            row["last_share_ts"] = max(
                float(row.get("last_share_ts") or 0.0),
                float(last_share_ts),
            )

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
            self._record_db_write(defer_commit=defer_commit, now_ts=ts)

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
        defer_commit: bool = False,
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
                    json.dumps(details or {}, separators=(",", ":")),
                ),
            )
            self._record_db_write(defer_commit=defer_commit, now_ts=ts)

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
        share_mode: str,
        defer_commit: bool = False,
    ) -> None:
        accounting_mode = self._normalize_share_mode(
            share_mode,
            default="pps" if self._pool_mode == "both" else self._pool_mode,
        )
        if self._pool_mode in {"pps", "solo"}:
            accounting_mode = self._pool_mode

        if ok:
            pps_credit = 0
            solo_credit = 0
            if accounting_mode == "pps":
                pps_credit = self._credit_for_share(reward, difficulty)
            elif accounting_mode == "solo" and is_block:
                solo_credit = int(reward)

            # ENA training-treasury fee: skim a fixed fraction of this share's
            # credit before the miner is paid. The miner keeps the remainder;
            # the fee accrues to the treasury address (paid out as usual). Never
            # skim from a share that is itself credited to the treasury.
            ena_fee_pps, ena_fee_solo = self._ena_fee_split(
                address, pps_credit, solo_credit
            )
            pps_credit -= ena_fee_pps
            solo_credit -= ena_fee_solo

            if (
                accounting_mode == "pps"
                and pps_credit > 0
                and self._ledger_record_pps_share_credit
            ):
                self._record_accounting_event(
                    ts=ts,
                    worker=worker,
                    address=address,
                    event="pps_share_credit",
                    amount=pps_credit,
                    job_id=job_id,
                    details={
                        "difficulty_ratio": difficulty,
                        "reward": reward,
                        "pool_mode": accounting_mode,
                        "ena_fee": ena_fee_pps,
                    },
                    defer_commit=defer_commit,
                )
            elif accounting_mode == "solo" and is_block and solo_credit > 0:
                self._record_accounting_event(
                    ts=ts,
                    worker=worker,
                    address=address,
                    event="solo_block_credit",
                    amount=solo_credit,
                    job_id=job_id,
                    details={
                        "reward": reward,
                        "pool_mode": accounting_mode,
                        "ena_fee": ena_fee_solo,
                    },
                    defer_commit=defer_commit,
                )
            self._apply_balance_delta(
                ts=ts,
                worker=worker,
                address=address,
                last_share_ts=ts,
                pps_credit=pps_credit,
                solo_credit=solo_credit,
                accepted_shares_delta=1,
                accepted_blocks_delta=1 if is_block else 0,
                defer_commit=defer_commit,
            )
            self._credit_ena_treasury_fee(
                ts=ts,
                from_worker=worker,
                from_address=address,
                job_id=job_id,
                accounting_mode=accounting_mode,
                ena_fee_pps=ena_fee_pps,
                ena_fee_solo=ena_fee_solo,
                defer_commit=defer_commit,
            )
            return

        self._apply_balance_delta(
            ts=ts,
            worker=worker,
            address=address,
            last_share_ts=ts,
            rejected_shares_delta=1,
            defer_commit=defer_commit,
        )
        self._record_accounting_event(
            ts=ts,
            worker=worker,
            address=address,
            event="share_rejected",
            amount=0,
            job_id=job_id,
            details={"reason": reason or "unknown", "pool_mode": accounting_mode},
            defer_commit=defer_commit,
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

    # --- ACCURATE raw hashrate (H/s) --------------------------------------
    # Sum the per-share `work` (expected raw SHA3 hashes = 2**256/share_target)
    # over the window and divide by elapsed time. This is the real hashing rate
    # the connected miners produce and matches xmrig's reported H/s, unlike the
    # difficulty-ratio sum above. Pre-existing shares (recorded before the
    # `work` column existed) contribute 0, so the figure converges as new shares
    # land. Falls back to difficulty*2**32 inside _expected_share_work when a
    # share had no target.

    def _raw_hashrate_from_events(
        self, events: List[ShareEvent], window_seconds: float
    ) -> float:
        if not events:
            return 0.0
        cutoff = time.time() - window_seconds
        total = sum(
            float(ev.get("work") or 0.0)
            for ev in events
            if ev["timestamp"] >= cutoff and ev["status"] == "accepted"
        )
        return total / window_seconds if window_seconds > 0 else 0.0

    def _raw_hashrate_from_db(self, window_seconds: float) -> float:
        if self._db is None:
            return 0.0
        cutoff = time.time() - window_seconds
        with self._db_lock:
            row = self._db.execute(
                "SELECT COALESCE(SUM(work), 0) FROM shares WHERE status = 'accepted' AND ts >= ?",
                (cutoff,),
            ).fetchone()
        total = float(row[0] or 0.0) if row else 0.0
        return total / window_seconds if window_seconds > 0 else 0.0

    # --- Miner-reported hashrate (Option A) -------------------------------
    def record_reported_hashrate(
        self,
        worker: str,
        address: Optional[str],
        hps: float,
        algo: str = "animica",
    ) -> None:
        """Store a miner's self-measured hashrate (H/s) from its xmrig API.

        This is the smooth, accurate source for the network hashrate: xmrig
        counts every hash, so summing fresh per-worker reports avoids the
        block-share sparsity of the share-work estimate.
        """
        w = self._normalize_worker(worker, "") if worker else ""
        if not w:
            return
        try:
            value = max(0.0, float(hps or 0.0))
        except (TypeError, ValueError):
            return
        with self._reported_hashrates_lock:
            self._reported_hashrates[w] = {
                "hps": value,
                "address": self._normalize_address(address) if address else None,
                "algo": str(algo or "animica"),
                "ts": time.time(),
            }

    def reported_network_hashrate(self, fresh_seconds: float = _REPORTED_HR_TTL) -> Tuple[float, int]:
        """Sum of fresh miner-reported hashrates → (total_hps, reporting_miners)."""
        cutoff = time.time() - float(fresh_seconds)
        total = 0.0
        n = 0
        with self._reported_hashrates_lock:
            stale = [w for w, e in self._reported_hashrates.items() if float(e.get("ts") or 0.0) < cutoff]
            for w in stale:
                self._reported_hashrates.pop(w, None)
            for e in self._reported_hashrates.values():
                total += float(e.get("hps") or 0.0)
                n += 1
        return total, n

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

    def _connected_payout_addresses(self) -> set[str]:
        addresses: set[str] = set()
        snapshots = getattr(self._server, "session_snapshots", None)
        if not callable(snapshots):
            return addresses
        try:
            sessions = snapshots() or []
        except Exception:
            return addresses
        for session in sessions:
            if isinstance(session, dict):
                raw_address = session.get("address")
                worker_hint = session.get("worker") or session.get("session_id")
            else:
                raw_address = getattr(session, "address", None)
                worker_hint = getattr(session, "worker", None) or getattr(
                    session, "session_id", None
                )
            address = self._normalize_address(raw_address)
            if not self._payout_eligible_address(address):
                _worker, parsed_address = self._parse_worker_identity(worker_hint)
                address = self._normalize_address(parsed_address)
            if self._payout_eligible_address(address):
                addresses.add(address)
        return addresses

    def _effective_due_amount(
        self,
        *,
        amount: int,
        address: str,
        last_share_ts: float,
        connected_addresses: set[str],
        now_ts: float,
    ) -> int:
        base_amount = max(0, int(amount or 0))
        if base_amount <= 0:
            return 0
        if address in connected_addresses:
            return base_amount
        half_life = float(self._inactive_share_halflife_seconds)
        if half_life <= 0:
            return base_amount
        share_ts = float(last_share_ts or 0.0)
        if share_ts <= 0:
            return base_amount
        age_seconds = max(0.0, now_ts - share_ts)
        age_after_grace = age_seconds - float(self._inactive_share_grace_seconds)
        if age_after_grace <= 0:
            return base_amount
        decay_factor = 0.5 ** (age_after_grace / half_life)
        return max(0, int(base_amount * decay_factor))

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
        self._run_housekeeping()
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
        self,
        *,
        min_amount: int,
        limit: int = 50,
        max_total_amount: Optional[int] = None,
    ) -> List[Dict[str, object]]:
        self._run_housekeeping()
        min_amount = max(1, int(min_amount or 1))
        limit = max(1, min(int(limit or 50), 500))
        items: List[Dict[str, object]] = []
        max_total = (
            max(0, int(max_total_amount))
            if max_total_amount is not None
            else None
        )
        now_ts = float(time.time())
        connected_addresses = self._connected_payout_addresses()

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
                    """,
                    (self._pool_mode, min_amount),
                ).fetchall()
                share_rows = self._db.execute(
                    """
                    SELECT address, MAX(ts) as last_share_ts
                    FROM shares
                    GROUP BY address
                    """
                ).fetchall()
            share_ts_by_address = {
                str(address or "").strip(): float(last_share_ts or 0.0)
                for address, last_share_ts in share_rows
                if str(address or "").strip()
            }
            for address, due_amount, worker_rows, updated_ts in rows:
                addr = str(address or "").strip()
                amount = int(due_amount or 0)
                last_share_ts = float(
                    share_ts_by_address.get(addr) or float(updated_ts or 0.0)
                )
                amount = self._effective_due_amount(
                    amount=amount,
                    address=addr,
                    last_share_ts=last_share_ts,
                    connected_addresses=connected_addresses,
                    now_ts=now_ts,
                )
                if (
                    not self._payout_eligible_address(addr)
                    or amount <= 0
                    or amount < min_amount
                ):
                    continue
                items.append(
                    {
                        "address": addr,
                        "amount": amount,
                        "workers": int(worker_rows or 0),
                        "updated_at": self._iso_ts(float(updated_ts or 0.0)),
                    }
                )
            items.sort(
                key=lambda entry: (
                    -int(entry.get("amount") or 0),
                    str(entry.get("address") or ""),
                )
            )
            return self._cap_due_items(
                items=items,
                min_amount=min_amount,
                limit=limit,
                max_total=max_total,
            )

        share_ts_by_address: Dict[str, float] = {}
        for event in self._share_events:
            addr = str(event.get("address") or "").strip()
            if not addr:
                continue
            ts = float(event.get("timestamp") or 0.0)
            current = share_ts_by_address.get(addr, 0.0)
            if ts > current:
                share_ts_by_address[addr] = ts

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
            row_last_share_ts = float(
                share_ts_by_address.get(addr)
                or float(row.get("last_share_ts") or 0.0)
                or float(row.get("updated_ts") or 0.0)
            )
            current = by_address.get(addr)
            if current is None:
                by_address[addr] = {
                    "address": addr,
                    "amount": available,
                    "workers": 1,
                    "updated_ts": float(row.get("updated_ts") or 0.0),
                    "last_share_ts": row_last_share_ts,
                }
            else:
                current["amount"] = int(current.get("amount") or 0) + available
                current["workers"] = int(current.get("workers") or 0) + 1
                current["updated_ts"] = max(
                    float(current.get("updated_ts") or 0.0),
                    float(row.get("updated_ts") or 0.0),
                )
                current["last_share_ts"] = max(
                    float(current.get("last_share_ts") or 0.0),
                    row_last_share_ts,
                )

        for entry in by_address.values():
            amount = self._effective_due_amount(
                amount=int(entry.get("amount") or 0),
                address=str(entry.get("address") or ""),
                last_share_ts=float(entry.get("last_share_ts") or 0.0),
                connected_addresses=connected_addresses,
                now_ts=now_ts,
            )
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
        items.sort(
            key=lambda entry: (
                -int(entry.get("amount") or 0),
                str(entry.get("address") or ""),
            )
        )
        return self._cap_due_items(
            items=items,
            min_amount=min_amount,
            limit=limit,
            max_total=max_total,
        )

    @staticmethod
    def _cap_due_items(
        *,
        items: List[Dict[str, object]],
        min_amount: int,
        limit: int,
        max_total: Optional[int],
    ) -> List[Dict[str, object]]:
        if max_total is None:
            return items[:limit]
        if max_total < min_amount:
            return []

        eligible: List[Dict[str, object]] = []
        amounts: List[int] = []
        for item in items:
            amount = int(item.get("amount") or 0)
            if amount <= 0:
                continue
            eligible.append(item)
            amounts.append(amount)
        if not eligible:
            return []

        budget = int(max_total)
        total_due = int(sum(amounts))
        if total_due <= budget:
            return eligible[:limit]

        # Pro-rate the available budget across all due addresses so no single
        # largest balance can monopolize each payout cycle.
        scale = float(budget) / float(total_due)
        allocated: List[int] = []
        fractions: List[float] = []
        for amount in amounts:
            scaled = float(amount) * scale
            base = min(amount, int(scaled))
            allocated.append(base)
            fractions.append(scaled - float(base))

        remaining = budget - sum(allocated)
        if remaining > 0:
            order = sorted(
                range(len(eligible)),
                key=lambda idx: (
                    fractions[idx],
                    str(eligible[idx].get("address") or ""),
                    -idx,
                ),
                reverse=True,
            )
            for idx in order:
                if remaining <= 0:
                    break
                room = amounts[idx] - allocated[idx]
                if room <= 0:
                    continue
                allocated[idx] += 1
                remaining -= 1

        capped: List[Dict[str, object]] = []
        for item, applied in zip(eligible, allocated):
            if applied < min_amount:
                continue
            next_item = dict(item)
            next_item["amount"] = int(applied)
            capped.append(next_item)
            if len(capped) >= limit:
                break
        return capped

    def mined_reward_in_window(
        self, *, window_seconds: float, now: Optional[float] = None
    ) -> int:
        window = max(0.0, float(window_seconds or 0.0))
        if window <= 0:
            return 0
        now_ts = float(now) if now is not None else float(time.time())
        cutoff = now_ts - window

        if self._db is not None:
            with self._db_lock:
                row = self._db.execute(
                    """
                    SELECT COALESCE(SUM(reward), 0)
                    FROM blocks
                    WHERE found_by_pool = 1 AND ts > ? AND ts <= ?
                    """,
                    (cutoff, now_ts),
                ).fetchone()
            return max(0, int((row or [0])[0] or 0))

        total = 0
        for blk in self._block_events:
            ts = float(blk.get("timestamp") or 0.0)
            if ts <= cutoff or ts > now_ts:
                continue
            if not bool(blk.get("found_by_pool", True)):
                continue
            total += int(blk.get("reward") or 0)
        return max(0, int(total))

    def payout_available_budget(self) -> int:
        """Return mined reward that has not already been committed to payouts."""
        if self._db is not None:
            with self._db_lock:
                mined_row = self._db.execute(
                    """
                    SELECT COALESCE(SUM(reward), 0)
                    FROM blocks
                    WHERE found_by_pool = 1
                    """
                ).fetchone()
                paid_row = self._db.execute(
                    """
                    SELECT COALESCE(SUM(amount), 0)
                    FROM payouts
                    WHERE mode = ? AND status IN (?, ?)
                    """,
                    (self._pool_mode, "submitted", "confirmed"),
                ).fetchone()
            mined_total = int((mined_row or [0])[0] or 0)
            paid_total = int((paid_row or [0])[0] or 0)
            return max(0, mined_total - paid_total)

        mined_total = 0
        for blk in self._block_events:
            if not bool(blk.get("found_by_pool", True)):
                continue
            mined_total += int(blk.get("reward") or 0)

        paid_total = 0
        for (mode, _worker, _address), row in self._worker_balances_cache.items():
            if mode != self._pool_mode:
                continue
            paid_total += int(row.get("paid_out") or 0)
        return max(0, mined_total - paid_total)

    def record_payout_sent(
        self,
        *,
        address: str,
        amount: int,
        tx_hash: str,
        raw_tx: Optional[str] = None,
        nonce: Optional[int] = None,
        ts: Optional[float] = None,
    ) -> int:
        addr = str(address or "").strip()
        requested = max(0, int(amount or 0))
        if requested <= 0 or not self._payout_eligible_address(addr):
            return 0
        norm_hash = str(tx_hash or "").strip().lower()
        if norm_hash and not norm_hash.startswith("0x"):
            norm_hash = "0x" + norm_hash
        raw_payload = str(raw_tx or "").strip() if raw_tx is not None else None
        nonce_value = int(nonce) if nonce is not None else None
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
                    details = json.dumps({"tx_hash": norm_hash}, sort_keys=True)
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
                            "details": {"tx_hash": norm_hash},
                        }
                    )
                    remaining -= delta
                    applied += delta

                self._db.execute(
                    """
                    INSERT INTO payouts (
                        ts, mode, address, amount, tx_hash, raw_tx, nonce,
                        status, error, retry_count, last_retry_ts, next_retry_ts, confirmed_ts
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        now,
                        self._pool_mode,
                        addr,
                        int(applied),
                        norm_hash,
                        raw_payload,
                        nonce_value,
                        "submitted",
                        None,
                        0,
                        None,
                        None,
                        None,
                    ),
                )
                self._commit_db_now(now_ts=now)
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
                details={"tx_hash": norm_hash},
            )
            remaining -= delta
            applied += delta
        self._payout_record_seq += 1
        self._payout_records.append(
            {
                "id": int(self._payout_record_seq),
                "timestamp": now,
                "mode": self._pool_mode,
                "address": addr,
                "amount": int(applied),
                "tx_hash": norm_hash,
                "raw_tx": raw_payload,
                "nonce": nonce_value,
                "status": "submitted",
                "error": None,
                "retry_count": 0,
                "last_retry_ts": None,
                "next_retry_ts": None,
                "confirmed_ts": None,
            }
        )
        return applied

    def pending_payout_submissions(self, *, limit: int = 200) -> List[Dict[str, object]]:
        self._run_housekeeping()
        max_rows = max(1, min(int(limit or 200), 2000))
        if self._db is not None:
            with self._db_lock:
                rows = self._db.execute(
                    """
                    SELECT
                        id, ts, address, amount, tx_hash, raw_tx, nonce, status, error,
                        retry_count, last_retry_ts, next_retry_ts, confirmed_ts
                    FROM payouts
                    WHERE mode = ? AND status = ? AND tx_hash IS NOT NULL AND tx_hash != ''
                    ORDER BY ts ASC, id ASC
                    LIMIT ?
                    """,
                    (self._pool_mode, "submitted", max_rows),
                ).fetchall()
            items: List[Dict[str, object]] = []
            for (
                row_id,
                ts,
                address,
                amount,
                tx_hash,
                raw_tx,
                nonce,
                status,
                error,
                retry_count,
                last_retry_ts,
                next_retry_ts,
                confirmed_ts,
            ) in rows:
                items.append(
                    {
                        "id": int(row_id or 0),
                        "timestamp": float(ts or 0.0),
                        "address": str(address or ""),
                        "amount": int(amount or 0),
                        "tx_hash": str(tx_hash or "").lower(),
                        "raw_tx": str(raw_tx) if raw_tx is not None else None,
                        "nonce": int(nonce) if nonce is not None else None,
                        "status": str(status or "submitted"),
                        "error": str(error) if error is not None else None,
                        "retry_count": int(retry_count or 0),
                        "last_retry_ts": (
                            float(last_retry_ts) if last_retry_ts is not None else None
                        ),
                        "next_retry_ts": (
                            float(next_retry_ts) if next_retry_ts is not None else None
                        ),
                        "confirmed_ts": (
                            float(confirmed_ts) if confirmed_ts is not None else None
                        ),
                    }
                )
            return items

        items: List[Dict[str, object]] = []
        for record in self._payout_records:
            if str(record.get("mode") or "") != self._pool_mode:
                continue
            if str(record.get("status") or "") != "submitted":
                continue
            tx_hash = str(record.get("tx_hash") or "").strip().lower()
            if not tx_hash:
                continue
            items.append(dict(record))
            if len(items) >= max_rows:
                break
        return items

    def mark_payout_confirmed(
        self,
        *,
        tx_hash: str,
        ts: Optional[float] = None,
        details: Optional[Dict[str, object]] = None,
    ) -> bool:
        norm_hash = str(tx_hash or "").strip().lower()
        if not norm_hash:
            return False
        if not norm_hash.startswith("0x"):
            norm_hash = "0x" + norm_hash
        now = float(ts or time.time())
        payload = dict(details or {})
        payload.setdefault("tx_hash", norm_hash)

        if self._db is not None:
            with self._db_lock:
                row = self._db.execute(
                    """
                    SELECT id, address, amount
                    FROM payouts
                    WHERE mode = ? AND LOWER(tx_hash) = LOWER(?) AND status = ?
                    ORDER BY id DESC
                    LIMIT 1
                    """,
                    (self._pool_mode, norm_hash, "submitted"),
                ).fetchone()
                if row is None:
                    return False
                payout_id, address, amount = row
                self._db.execute(
                    """
                    UPDATE payouts
                    SET status = ?, error = NULL, confirmed_ts = ?, next_retry_ts = NULL
                    WHERE id = ?
                    """,
                    ("confirmed", now, int(payout_id)),
                )
                self._commit_db_now(now_ts=now)
            self._record_accounting_event(
                ts=now,
                worker="__pool__",
                address=str(address or "unknown-address"),
                event="payout_confirmed",
                amount=max(0, int(amount or 0)),
                job_id=None,
                details=payload,
            )
            return True

        for record in reversed(self._payout_records):
            if str(record.get("mode") or "") != self._pool_mode:
                continue
            if str(record.get("status") or "") != "submitted":
                continue
            if str(record.get("tx_hash") or "").lower() != norm_hash:
                continue
            record["status"] = "confirmed"
            record["error"] = None
            record["confirmed_ts"] = now
            record["next_retry_ts"] = None
            self._record_accounting_event(
                ts=now,
                worker="__pool__",
                address=str(record.get("address") or "unknown-address"),
                event="payout_confirmed",
                amount=max(0, int(record.get("amount") or 0)),
                job_id=None,
                details=payload,
            )
            return True
        return False

    def record_payout_rebroadcast(
        self,
        *,
        tx_hash: str,
        new_tx_hash: Optional[str] = None,
        raw_tx: Optional[str] = None,
        nonce: Optional[int] = None,
        next_retry_ts: Optional[float] = None,
        ts: Optional[float] = None,
        details: Optional[Dict[str, object]] = None,
    ) -> bool:
        norm_hash = str(tx_hash or "").strip().lower()
        if not norm_hash:
            return False
        if not norm_hash.startswith("0x"):
            norm_hash = "0x" + norm_hash
        target_hash = str(new_tx_hash or norm_hash).strip().lower()
        if target_hash and not target_hash.startswith("0x"):
            target_hash = "0x" + target_hash
        now = float(ts or time.time())
        retry_at = float(next_retry_ts) if next_retry_ts is not None else None
        payload = dict(details or {})
        payload.setdefault("tx_hash", target_hash)
        payload.setdefault("previous_tx_hash", norm_hash)

        if self._db is not None:
            with self._db_lock:
                row = self._db.execute(
                    """
                    SELECT id, address, amount, retry_count
                    FROM payouts
                    WHERE mode = ? AND LOWER(tx_hash) = LOWER(?) AND status = ?
                    ORDER BY id DESC
                    LIMIT 1
                    """,
                    (self._pool_mode, norm_hash, "submitted"),
                ).fetchone()
                if row is None:
                    return False
                payout_id, address, amount, retry_count = row
                next_count = int(retry_count or 0) + 1
                self._db.execute(
                    """
                    UPDATE payouts
                    SET tx_hash = ?, raw_tx = COALESCE(?, raw_tx), nonce = COALESCE(?, nonce),
                        retry_count = ?, last_retry_ts = ?, next_retry_ts = ?, error = NULL
                    WHERE id = ?
                    """,
                    (
                        target_hash,
                        raw_tx,
                        int(nonce) if nonce is not None else None,
                        next_count,
                        now,
                        retry_at,
                        int(payout_id),
                    ),
                )
                self._commit_db_now(now_ts=now)
            self._record_accounting_event(
                ts=now,
                worker="__pool__",
                address=str(address or "unknown-address"),
                event="payout_rebroadcast",
                amount=max(0, int(amount or 0)),
                job_id=None,
                details=payload,
            )
            return True

        for record in reversed(self._payout_records):
            if str(record.get("mode") or "") != self._pool_mode:
                continue
            if str(record.get("status") or "") != "submitted":
                continue
            if str(record.get("tx_hash") or "").lower() != norm_hash:
                continue
            record["tx_hash"] = target_hash
            if raw_tx is not None:
                record["raw_tx"] = str(raw_tx)
            if nonce is not None:
                record["nonce"] = int(nonce)
            record["retry_count"] = int(record.get("retry_count") or 0) + 1
            record["last_retry_ts"] = now
            record["next_retry_ts"] = retry_at
            record["error"] = None
            self._record_accounting_event(
                ts=now,
                worker="__pool__",
                address=str(record.get("address") or "unknown-address"),
                event="payout_rebroadcast",
                amount=max(0, int(record.get("amount") or 0)),
                job_id=None,
                details=payload,
            )
            return True
        return False

    def record_payout_retry(
        self,
        *,
        tx_hash: str,
        error: str,
        next_retry_ts: Optional[float] = None,
        ts: Optional[float] = None,
    ) -> bool:
        norm_hash = str(tx_hash or "").strip().lower()
        if not norm_hash:
            return False
        if not norm_hash.startswith("0x"):
            norm_hash = "0x" + norm_hash
        now = float(ts or time.time())
        retry_at = float(next_retry_ts) if next_retry_ts is not None else None
        message = str(error or "retry scheduled")

        if self._db is not None:
            with self._db_lock:
                row = self._db.execute(
                    """
                    SELECT id, address, amount, retry_count
                    FROM payouts
                    WHERE mode = ? AND LOWER(tx_hash) = LOWER(?) AND status = ?
                    ORDER BY id DESC
                    LIMIT 1
                    """,
                    (self._pool_mode, norm_hash, "submitted"),
                ).fetchone()
                if row is None:
                    return False
                payout_id, address, amount, retry_count = row
                next_count = int(retry_count or 0) + 1
                self._db.execute(
                    """
                    UPDATE payouts
                    SET retry_count = ?, last_retry_ts = ?, next_retry_ts = ?, error = ?
                    WHERE id = ?
                    """,
                    (next_count, now, retry_at, message, int(payout_id)),
                )
                self._commit_db_now(now_ts=now)
            self._record_accounting_event(
                ts=now,
                worker="__pool__",
                address=str(address or "unknown-address"),
                event="payout_retry_scheduled",
                amount=max(0, int(amount or 0)),
                job_id=None,
                details={"tx_hash": norm_hash, "error": message, "next_retry_ts": retry_at},
            )
            return True

        for record in reversed(self._payout_records):
            if str(record.get("mode") or "") != self._pool_mode:
                continue
            if str(record.get("status") or "") != "submitted":
                continue
            if str(record.get("tx_hash") or "").lower() != norm_hash:
                continue
            record["retry_count"] = int(record.get("retry_count") or 0) + 1
            record["last_retry_ts"] = now
            record["next_retry_ts"] = retry_at
            record["error"] = message
            self._record_accounting_event(
                ts=now,
                worker="__pool__",
                address=str(record.get("address") or "unknown-address"),
                event="payout_retry_scheduled",
                amount=max(0, int(record.get("amount") or 0)),
                job_id=None,
                details={"tx_hash": norm_hash, "error": message, "next_retry_ts": retry_at},
            )
            return True
        return False

    def _release_paid_out_credit(
        self,
        *,
        address: str,
        amount: int,
        tx_hash: str,
        reason: str,
        ts: float,
    ) -> int:
        addr = str(address or "").strip()
        remaining = max(0, int(amount or 0))
        if remaining <= 0 or not self._payout_eligible_address(addr):
            return 0

        released = 0
        if self._db is not None:
            with self._db_lock:
                rows = self._db.execute(
                    """
                    SELECT worker, paid_out
                    FROM worker_balances
                    WHERE mode = ? AND address = ? AND paid_out > 0
                    ORDER BY updated_ts DESC, worker DESC
                    """,
                    (self._pool_mode, addr),
                ).fetchall()
                for worker, paid_out in rows:
                    if remaining <= 0:
                        break
                    available = max(0, int(paid_out or 0))
                    if available <= 0:
                        continue
                    delta = min(available, remaining)
                    self._db.execute(
                        """
                        UPDATE worker_balances
                        SET paid_out = MAX(0, paid_out - ?), updated_ts = ?
                        WHERE mode = ? AND worker = ? AND address = ?
                        """,
                        (delta, ts, self._pool_mode, str(worker), addr),
                    )
                    details = json.dumps(
                        {"tx_hash": tx_hash, "reason": reason},
                        sort_keys=True,
                    )
                    self._db.execute(
                        """
                        INSERT INTO accounting_ledger (ts, mode, worker, address, event, amount, job_id, details)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            ts,
                            self._pool_mode,
                            str(worker),
                            addr,
                            "payout_reverted",
                            int(delta),
                            None,
                            details,
                        ),
                    )
                    self._accounting_events.appendleft(
                        {
                            "timestamp": ts,
                            "mode": self._pool_mode,
                            "worker": str(worker),
                            "address": addr,
                            "event": "payout_reverted",
                            "amount": int(delta),
                            "job_id": "",
                            "details": {"tx_hash": tx_hash, "reason": reason},
                        }
                    )
                    remaining -= delta
                    released += delta
                self._commit_db_now(now_ts=ts)
            return released

        candidates = [
            (key, row)
            for key, row in self._worker_balances_cache.items()
            if key[0] == self._pool_mode
            and str(key[2] or "").strip() == addr
            and int(row.get("paid_out") or 0) > 0
        ]
        candidates.sort(
            key=lambda item: (
                float(item[1].get("updated_ts") or 0.0),
                str(item[0][1]),
            ),
            reverse=True,
        )
        for (mode, worker, _), row in candidates:
            if remaining <= 0:
                break
            available = max(0, int(row.get("paid_out") or 0))
            if available <= 0:
                continue
            delta = min(available, remaining)
            self._apply_balance_delta(
                ts=ts,
                worker=str(worker),
                address=addr,
                paid_out_delta=-delta,
            )
            self._record_accounting_event(
                ts=ts,
                worker=str(worker),
                address=addr,
                event="payout_reverted",
                amount=delta,
                job_id=None,
                details={"tx_hash": tx_hash, "reason": reason},
            )
            remaining -= delta
            released += delta
        return released

    def mark_payout_dropped(
        self,
        *,
        tx_hash: str,
        error: str,
        release_credit: bool = True,
        ts: Optional[float] = None,
    ) -> bool:
        norm_hash = str(tx_hash or "").strip().lower()
        if not norm_hash:
            return False
        if not norm_hash.startswith("0x"):
            norm_hash = "0x" + norm_hash
        now = float(ts or time.time())
        message = str(error or "dropped")

        if self._db is not None:
            with self._db_lock:
                row = self._db.execute(
                    """
                    SELECT id, address, amount
                    FROM payouts
                    WHERE mode = ? AND LOWER(tx_hash) = LOWER(?) AND status = ?
                    ORDER BY id DESC
                    LIMIT 1
                    """,
                    (self._pool_mode, norm_hash, "submitted"),
                ).fetchone()
                if row is None:
                    return False
                payout_id, address, amount = row
                self._db.execute(
                    """
                    UPDATE payouts
                    SET status = ?, error = ?, last_retry_ts = ?, next_retry_ts = NULL
                    WHERE id = ?
                    """,
                    ("dropped", message, now, int(payout_id)),
                )
                self._commit_db_now(now_ts=now)
            released = 0
            if release_credit:
                released = self._release_paid_out_credit(
                    address=str(address or ""),
                    amount=max(0, int(amount or 0)),
                    tx_hash=norm_hash,
                    reason=message,
                    ts=now,
                )
            self._record_accounting_event(
                ts=now,
                worker="__pool__",
                address=str(address or "unknown-address"),
                event="payout_dropped",
                amount=max(0, int(amount or 0)),
                job_id=None,
                details={
                    "tx_hash": norm_hash,
                    "error": message,
                    "released_credit": int(released),
                },
            )
            return True

        for record in reversed(self._payout_records):
            if str(record.get("mode") or "") != self._pool_mode:
                continue
            if str(record.get("status") or "") != "submitted":
                continue
            if str(record.get("tx_hash") or "").lower() != norm_hash:
                continue
            record["status"] = "dropped"
            record["error"] = message
            record["last_retry_ts"] = now
            record["next_retry_ts"] = None
            released = 0
            if release_credit:
                released = self._release_paid_out_credit(
                    address=str(record.get("address") or ""),
                    amount=max(0, int(record.get("amount") or 0)),
                    tx_hash=norm_hash,
                    reason=message,
                    ts=now,
                )
            self._record_accounting_event(
                ts=now,
                worker="__pool__",
                address=str(record.get("address") or "unknown-address"),
                event="payout_dropped",
                amount=max(0, int(record.get("amount") or 0)),
                job_id=None,
                details={
                    "tx_hash": norm_hash,
                    "error": message,
                    "released_credit": int(released),
                },
            )
            return True
        return False

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
            self._commit_db_now(now_ts=now)

    def pool_summary(self) -> Dict[str, object]:
        self._run_housekeeping()
        cached = self._read_cache_get("pool_summary")
        if isinstance(cached, dict):
            return dict(cached)

        stats = self._server.stats()
        job = self._job_manager.current_job()
        share_events = list(self._share_events)
        pool_hashrate = self._hashrate_from_db(600) or self._hashrate_from_events(
            share_events, 600
        )
        # Accurate raw H/s from per-share work. Shares are sparse (recorded at
        # block granularity), so the headline uses a 1h window for stability and
        # falls back to a wider 6h window when the last hour had no shares.
        raw_1m = self._raw_hashrate_from_db(60) or self._raw_hashrate_from_events(share_events, 60)
        raw_15m = self._raw_hashrate_from_db(900) or self._raw_hashrate_from_events(share_events, 900)
        raw_1h = self._raw_hashrate_from_db(3600) or self._raw_hashrate_from_events(share_events, 3600)
        share_work_hps = raw_1h or self._raw_hashrate_from_db(21600) or raw_15m
        # Prefer the smooth miner-reported sum (Option A); fall back to the
        # share-work estimate when no miner is reporting (e.g. stock xmrig).
        reported_hps, reporting_miners = self.reported_network_hashrate()
        network_hashrate_hps = reported_hps or share_work_hps
        latest_block = self._latest_block()
        current_reward = str(self._reward_from_raw(getattr(job, "raw", None)))
        accounting = self.accounting_summary()
        payout = self.payout_status()
        result = {
            "pool_name": "Animica Stratum Pool",
            "network": self._config.network or f"chain-{self._config.chain_id}",
            "pool_mode": self._pool_mode,
            "height": (job.height if job else None) or 0,
            "last_block_hash": latest_block.get("hash") or "0x0",
            "pool_hashrate": pool_hashrate,
            # Accurate raw SHA3 hashrate (H/s). Prefers miner-reported (smooth)
            # and falls back to share-work. This is the Animica network hashrate.
            "network_hashrate_hps": network_hashrate_hps,
            "hashrate_source": "reported" if reported_hps else "share_work",
            "reported_hashrate_hps": reported_hps,
            "reporting_miners": reporting_miners,
            "hashrate_raw_1m": raw_1m,
            "hashrate_raw_15m": raw_15m,
            "hashrate_raw_1h": raw_1h,
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
        self._read_cache_set("pool_summary", result)
        return dict(result)

    def _blocks_found_total(self) -> int:
        if self._db is not None:
            with self._db_lock:
                row = self._db.execute("SELECT COUNT(*) FROM blocks").fetchone()
            return int(row[0] or 0) if row else 0
        return len(self._block_events)

    def _blocks_found_by_address(self, address: str) -> int:
        addr = self._normalize_address(address)
        if not self._payout_eligible_address(addr):
            return 0
        if self._db is not None:
            with self._db_lock:
                row = self._db.execute(
                    "SELECT COUNT(*) FROM blocks WHERE address = ?",
                    (addr,),
                ).fetchone()
            return int(row[0] or 0) if row else 0
        return sum(
            1
            for blk in self._block_events
            if self._normalize_address(blk.get("address")) == addr  # type: ignore[arg-type]
        )

    def _blocks_found_by_worker(self, worker_id: str) -> int:
        address = self._resolve_address_for_identity(worker_id)
        if not address:
            return 0
        return self._blocks_found_by_address(address)

    def _address_balances_map(self) -> Dict[str, Dict[str, object]]:
        data: Dict[str, Dict[str, object]] = {}
        if self._db is not None:
            with self._db_lock:
                rows = self._db.execute(
                    """
                    SELECT address,
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
                    GROUP BY address
                    """,
                    (self._pool_mode,),
                ).fetchall()
            for (
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
                addr = self._normalize_address(address)
                if not self._payout_eligible_address(addr):
                    continue
                gross_credit = int(total_credit or 0)
                paid_total = int(paid_out or 0)
                data[addr] = {
                    "address": addr,
                    "total_credit": max(0, gross_credit - paid_total),
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

        for (mode, _worker, address), row in self._worker_balances_cache.items():
            if mode != self._pool_mode:
                continue
            addr = self._normalize_address(address)
            if not self._payout_eligible_address(addr):
                continue
            total_credit = int(row.get("total_credit") or 0)
            pps_credit = int(row.get("pps_credit") or 0)
            solo_credit = int(row.get("solo_credit") or 0)
            paid_out = int(row.get("paid_out") or 0)
            available_credit = max(0, total_credit - paid_out)
            accepted_shares = int(row.get("accepted_shares") or 0)
            accepted_blocks = int(row.get("accepted_blocks") or 0)
            rejected_shares = int(row.get("rejected_shares") or 0)
            updated_ts = float(row.get("updated_ts") or 0.0)
            current = data.get(addr)
            if current is None:
                data[addr] = {
                    "address": addr,
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
                continue
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

    def _worker_balances_map(self) -> Dict[str, Dict[str, object]]:
        # Backward-compatible alias: worker-facing APIs now key balances by
        # payout address so worker names remain cosmetic.
        return self._address_balances_map()

    def miners(self) -> Dict[str, object]:
        self._run_housekeeping()
        cached = self._read_cache_get("miners")
        if isinstance(cached, dict):
            return dict(cached)

        session_map = self._session_summary_by_address()
        balance_map = self._address_balances_map()

        cutoff_1m = time.time() - 60
        cutoff_15m = time.time() - 900
        cutoff_1h = time.time() - 3600
        cutoff_max = min(cutoff_1m, cutoff_15m, cutoff_1h)

        aggregates: Dict[str, Dict[str, object]] = {}
        block_counts: Dict[str, int] = {}
        events_by_address: Dict[str, List[ShareEvent]] = defaultdict(list)
        latest_worker_by_address: Dict[str, str] = {}
        latest_mode_by_address: Dict[str, str] = {}
        latest_mode_ts_by_address: Dict[str, float] = {}

        for ev in self._share_events:
            addr = self._normalize_address(ev.get("address"))  # type: ignore[arg-type]
            if not self._payout_eligible_address(addr):
                continue
            events_by_address[addr].append(ev)
            worker_label = str(ev.get("worker") or "").strip()
            if worker_label:
                latest_worker_by_address[addr] = worker_label
            mode_value = self._pick_share_mode(ev.get("pool_mode"), default="pps")
            event_ts = float(ev.get("timestamp") or 0.0)
            prev_mode_ts = float(latest_mode_ts_by_address.get(addr) or 0.0)
            if event_ts >= prev_mode_ts:
                latest_mode_ts_by_address[addr] = event_ts
                latest_mode_by_address[addr] = mode_value

        if self._db is not None:
            with self._db_lock:
                rows = self._db.execute(
                    """
                    SELECT address,
                           MAX(worker) as worker_name,
                           SUM(CASE WHEN status='accepted' THEN 1 ELSE 0 END) as accepted,
                           SUM(CASE WHEN status='rejected' THEN 1 ELSE 0 END) as rejected,
                           SUM(CASE WHEN status='accepted' AND ts >= ? THEN difficulty ELSE 0 END) as diff_1m,
                           SUM(CASE WHEN status='accepted' AND ts >= ? THEN difficulty ELSE 0 END) as diff_15m,
                           SUM(CASE WHEN status='accepted' AND ts >= ? THEN difficulty ELSE 0 END) as diff_1h,
                           MAX(ts) as last_ts
                    FROM shares
                    WHERE ts >= ? AND address IS NOT NULL AND address != ''
                    GROUP BY address
                    """,
                    (cutoff_1m, cutoff_15m, cutoff_1h, cutoff_max),
                ).fetchall()
                block_rows = self._db.execute(
                    """
                    SELECT address, COUNT(*)
                    FROM blocks
                    WHERE address IS NOT NULL AND address != ''
                    GROUP BY address
                    """
                ).fetchall()
                mode_rows = self._db.execute(
                    """
                    SELECT s.address, s.mode, s.ts
                    FROM shares AS s
                    JOIN (
                        SELECT address, MAX(ts) AS max_ts
                        FROM shares
                        WHERE address IS NOT NULL AND address != ''
                        GROUP BY address
                    ) AS latest
                      ON latest.address = s.address
                     AND latest.max_ts = s.ts
                    WHERE s.address IS NOT NULL AND s.address != ''
                    ORDER BY s.ts DESC
                    """
                ).fetchall()
            for row in rows:
                (
                    address,
                    worker_name,
                    accepted,
                    rejected,
                    diff1,
                    diff15,
                    diff60,
                    last_ts,
                ) = row
                addr = self._normalize_address(address)
                if not self._payout_eligible_address(addr):
                    continue
                aggregates[addr] = {
                    "worker_name": str(worker_name or "").strip(),
                    "shares_accepted": int(accepted or 0),
                    "shares_rejected": int(rejected or 0),
                    "hashrate_1m": float(diff1 or 0.0) / 60,
                    "hashrate_15m": float(diff15 or 0.0) / 900,
                    "hashrate_1h": float(diff60 or 0.0) / 3600,
                    "last_share_at": last_ts,
                }
            for address, blocks_found in block_rows:
                addr = self._normalize_address(address)
                if not self._payout_eligible_address(addr):
                    continue
                block_counts[addr] = int(blocks_found or 0)
            for address, mode, ts in mode_rows:
                addr = self._normalize_address(address)
                if not self._payout_eligible_address(addr):
                    continue
                mode_value = self._pick_share_mode(mode, default="pps")
                mode_ts = float(ts or 0.0)
                prev_mode_ts = float(latest_mode_ts_by_address.get(addr) or 0.0)
                if mode_ts >= prev_mode_ts:
                    latest_mode_ts_by_address[addr] = mode_ts
                    latest_mode_by_address[addr] = mode_value
        else:
            for address, events in events_by_address.items():
                recent_events = [
                    ev
                    for ev in events
                    if float(ev.get("timestamp") or 0.0) >= cutoff_max
                ]
                if not recent_events:
                    continue
                accepted = sum(
                    1 for ev in recent_events if str(ev.get("status") or "") == "accepted"
                )
                rejected = sum(
                    1 for ev in recent_events if str(ev.get("status") or "") == "rejected"
                )
                aggregates[address] = {
                    "worker_name": latest_worker_by_address.get(address, ""),
                    "shares_accepted": accepted,
                    "shares_rejected": rejected,
                    "hashrate_1m": self._hashrate_from_events(recent_events, 60),
                    "hashrate_15m": self._hashrate_from_events(recent_events, 900),
                    "hashrate_1h": self._hashrate_from_events(recent_events, 3600),
                    "last_share_at": max(
                        float(ev.get("timestamp") or 0.0) for ev in recent_events
                    ),
                }
            for blk in self._block_events:
                addr = self._normalize_address(blk.get("address"))  # type: ignore[arg-type]
                if not self._payout_eligible_address(addr):
                    continue
                block_counts[addr] = block_counts.get(addr, 0) + 1

        if not block_counts:
            for blk in self._block_events:
                addr = self._normalize_address(blk.get("address"))  # type: ignore[arg-type]
                if not self._payout_eligible_address(addr):
                    continue
                block_counts[addr] = block_counts.get(addr, 0) + 1

        items: List[Dict[str, object]] = []
        seen_addresses: set[str] = set()

        def build_item(address: str) -> Dict[str, object]:
            agg = aggregates.get(address, {})
            session = session_map.get(address, {})
            balance = balance_map.get(address, {})
            events = events_by_address.get(address, [])
            worker_name = (
                str(session.get("worker_name") or "").strip()
                or str(agg.get("worker_name") or "").strip()
                or latest_worker_by_address.get(address, "")
                or address
            )
            shares_accepted = int(
                agg.get("shares_accepted")
                or session.get("shares_accepted")
                or balance.get("accepted_shares")
                or 0
            )
            shares_rejected = int(
                agg.get("shares_rejected")
                or session.get("shares_rejected")
                or balance.get("rejected_shares")
                or 0
            )
            blocks_found = int(
                block_counts.get(address, balance.get("accepted_blocks") or 0)
            )
            mode_value = self._pick_share_mode(
                session.get("pool_mode"),
                latest_mode_by_address.get(address),
                default="pps",
            )
            return {
                "worker_id": address,
                "worker_name": worker_name,
                "address": address,
                "hashrate_1m": agg.get("hashrate_1m")
                or self._hashrate_from_events(events, 60),
                "hashrate_15m": agg.get("hashrate_15m")
                or self._hashrate_from_events(events, 900),
                "hashrate_1h": agg.get("hashrate_1h")
                or self._hashrate_from_events(events, 3600),
                "last_share_at": agg.get("last_share_at")
                or session.get("last_share_at")
                or balance.get("updated_ts"),
                "difficulty": session.get("difficulty"),
                "shares_accepted": shares_accepted,
                "shares_rejected": shares_rejected,
                "blocks_found": blocks_found,
                "pool_mode": mode_value,
                "credit_total": str(int(balance.get("total_credit") or 0)),
                "credit_pps": str(int(balance.get("pps_credit") or 0)),
                "credit_solo": str(int(balance.get("solo_credit") or 0)),
            }

        for address in session_map.keys():
            if not self._payout_eligible_address(address):
                continue
            items.append(build_item(address))
            seen_addresses.add(address)

        for address in aggregates.keys():
            if address in seen_addresses or not self._payout_eligible_address(address):
                continue
            items.append(build_item(address))
            seen_addresses.add(address)

        for address in balance_map.keys():
            if address in seen_addresses or not self._payout_eligible_address(address):
                continue
            items.append(build_item(address))
            seen_addresses.add(address)

        result = {"items": items, "total": len(items)}
        self._read_cache_set("miners", result)
        return dict(result)

    def miner_detail(self, worker_id: str) -> Dict[str, object]:
        self._run_housekeeping()
        cache_key = f"miner_detail:{str(worker_id or '').strip()}"
        cached = self._read_cache_get(cache_key)
        if isinstance(cached, dict):
            return dict(cached)

        identity = str(worker_id or "").strip()
        address = self._resolve_address_for_identity(identity)
        if not address:
            return {}

        balance = self._address_balances_map().get(address, {})
        session = self._session_summary_by_address().get(address, {})
        cutoff = time.time() - 3600
        buckets: Dict[int, float] = defaultdict(float)
        events: List[ShareEvent] = []

        if self._db is not None:
            with self._db_lock:
                rows = self._db.execute(
                    """
                    SELECT ts, worker, address, mode, difficulty, status
                    FROM shares
                    WHERE address = ? AND ts >= ?
                    ORDER BY ts ASC
                    """,
                    (address, cutoff),
                ).fetchall()
        else:
            rows = []

        if rows:
            for ts, worker, event_address, mode, difficulty, status in rows:
                events.append(
                    {
                        "timestamp": ts,
                        "worker": str(worker or ""),
                        "address": event_address,
                        "pool_mode": self._pick_share_mode(mode, default="pps"),
                        "difficulty": difficulty,
                        "status": status,
                    }
                )
                if status == "accepted":
                    bucket = int(ts // 60) * 60
                    buckets[bucket] += float(difficulty or 0.0)
        else:
            for ev in self._share_events:
                event_address = self._normalize_address(ev.get("address"))  # type: ignore[arg-type]
                if event_address != address or float(ev.get("timestamp") or 0.0) < cutoff:
                    continue
                event_copy = dict(ev)
                event_copy["pool_mode"] = self._pick_share_mode(
                    ev.get("pool_mode"),
                    default="pps",
                )
                events.append(event_copy)
                if ev.get("status") == "accepted":
                    bucket = int(float(ev.get("timestamp") or 0.0) // 60) * 60
                    buckets[bucket] += float(ev.get("difficulty") or 0.0)

        blocks_found = self._blocks_found_by_address(address)
        if not events and not session and not balance and blocks_found <= 0:
            return {}

        timeseries: List[Tuple[str, float]] = []
        for bucket in sorted(buckets.keys()):
            ts_iso = datetime.fromtimestamp(bucket, tz=timezone.utc).isoformat()
            timeseries.append((ts_iso, buckets[bucket] / 60))

        accepted = sum(1 for ev in events if ev.get("status") == "accepted")
        rejected = sum(1 for ev in events if ev.get("status") == "rejected")
        if accepted <= 0 and rejected <= 0 and balance:
            accepted = int(balance.get("accepted_shares") or 0)
            rejected = int(balance.get("rejected_shares") or 0)

        latest = events[-1] if events else None
        worker_name = (
            str(session.get("worker_name") or "").strip()
            or str((latest or {}).get("worker") or "").strip()
            or identity
            or address
        )
        miner_mode = self._pick_share_mode(
            session.get("pool_mode"),
            (latest or {}).get("pool_mode"),
            default="pps",
        )
        connected_since = float(session.get("connected_since") or 0.0)
        result = {
            "address": address,
            "worker_name": worker_name,
            "hashrate_timeseries": timeseries,
            "last_share": {
                "time": (
                    datetime.fromtimestamp(
                        float(latest.get("timestamp") or 0.0), tz=timezone.utc
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
            "pool_mode": miner_mode,
            "credit_total": str(int(balance.get("total_credit") or 0)),
            "credit_pps": str(int(balance.get("pps_credit") or 0)),
            "credit_solo": str(int(balance.get("solo_credit") or 0)),
            "current_difficulty": (
                session.get("difficulty")
                or (latest.get("difficulty") if latest else 0)
                or 0
            ),
            "connected_since": (
                datetime.fromtimestamp(connected_since, tz=timezone.utc).isoformat()
                if connected_since > 0
                else None
            ),
        }
        self._read_cache_set(cache_key, result)
        return dict(result)

    def recent_blocks(self) -> Dict[str, object]:
        self._run_housekeeping()
        cached = self._read_cache_get("recent_blocks")
        if isinstance(cached, dict):
            return dict(cached)

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

        result = {
            "items": items,
            "total": len(items),
            "blocks_found_total": self._blocks_found_total(),
        }
        self._read_cache_set("recent_blocks", result)
        return dict(result)

    def accounting_summary(self) -> Dict[str, object]:
        self._run_housekeeping()
        if self._db is not None:
            summary = self._accounting_summary_from_db()
            if summary:
                return summary
        return self._accounting_summary_from_memory()

    def accounting_ledger(self, *, limit: int = 100) -> Dict[str, object]:
        self._run_housekeeping()
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
        self._run_housekeeping()
        return {
            "status": "ok",
            "uptime": int(time.time() - self._started),
            "pool_mode": self._pool_mode,
            "accounting": self.accounting_summary(),
            "payout": self.payout_status(),
        }

    def close(self) -> None:
        self._run_housekeeping(force=True)
        if self._db is None:
            return
        with self._db_lock:
            if self._db is None:
                return
            if self._db_pending_statements > 0:
                self._commit_db_now()
            self._db.close()
            self._db = None
