from __future__ import annotations

import sqlite3
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Deque, Dict, List, Optional, Tuple

from mining.stratum_server import Session, StratumJob, StratumServer

from .config import PoolConfig
from .job_manager import JobManager

ShareEvent = Dict[str, object]


class PoolMetrics:
    """Lightweight in-memory metrics aggregator for the Stratum pool."""

    def __init__(self, config: PoolConfig, job_manager: JobManager, server: StratumServer) -> None:
        self._config = config
        self._job_manager = job_manager
        self._server = server
        self._share_events: Deque[ShareEvent] = deque(maxlen=5000)
        self._block_events: Deque[Dict[str, object]] = deque(maxlen=200)
        self._started = time.time()
        self._db = self._init_db(config.db_url)
        self._db_lock = Lock()

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
                tx_count INTEGER
            )
            """
        )
        conn.commit()
        return conn

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
        difficulty = float(submit_params.get("d_ratio") or submit_params.get("shareTarget") or job.share_target)
        event: ShareEvent = {
            "timestamp": now,
            "session_id": session.session_id,
            "worker": session.worker or session.session_id,
            "address": session.address or "unknown",
            "difficulty": difficulty,
            "status": "accepted" if ok else "rejected",
            "reason": reason,
            "job_id": job.job_id,
            "height": submit_params.get("height") or job.header.get("number") or job.header.get("height"),
        }
        self._share_events.append(event)
        self._persist_share(event, is_block=is_block, tx_count=tx_count)
        if is_block:
            self._block_events.appendleft(
                {
                    "found_by_pool": True,
                    "timestamp": now,
                    "job_id": job.job_id,
                    "height": event["height"],
                    "tx_count": tx_count,
                }
            )

    def _persist_share(self, event: ShareEvent, *, is_block: bool, tx_count: int) -> None:
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
                    INSERT OR REPLACE INTO blocks (job_id, height, ts, found_by_pool, tx_count)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        event.get("job_id"),
                        event.get("height"),
                        event.get("timestamp"),
                        1,
                        tx_count,
                    ),
                )
            self._db.commit()

    def _now_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _hashrate_from_events(self, events: List[ShareEvent], window_seconds: float) -> float:
        if not events:
            return 0.0
        cutoff = time.time() - window_seconds
        total = sum(float(ev.get("difficulty") or 0.0) for ev in events if ev["timestamp"] >= cutoff and ev["status"] == "accepted")
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
                    "SELECT height, job_id, ts, found_by_pool FROM blocks ORDER BY ts DESC LIMIT 1"
                ).fetchone()
            if row:
                height, job_id, ts, found = row
                return {
                    "height": height,
                    "hash": job_id,
                    "timestamp": datetime.fromtimestamp(ts, tz=timezone.utc).isoformat() if ts else None,
                    "found_by_pool": bool(found),
                }

        if self._block_events:
            blk = self._block_events[0]
            return {
                "height": blk.get("height"),
                "hash": blk.get("job_id"),
                "timestamp": datetime.fromtimestamp(float(blk.get("timestamp")), tz=timezone.utc).isoformat()
                if blk.get("timestamp")
                else None,
                "found_by_pool": blk.get("found_by_pool", False),
            }

        job = self._job_manager.current_job()
        return {
            "height": (job.height if job else None) or 0,
            "hash": (job.header.get("hash") if job and job.header else None) or "0x0",
            "timestamp": None,
            "found_by_pool": False,
        }

    def pool_summary(self) -> Dict[str, object]:
        stats = self._server.stats()
        job = self._job_manager.current_job()
        share_events = list(self._share_events)
        pool_hashrate = self._hashrate_from_db(600) or self._hashrate_from_events(share_events, 600)
        latest_block = self._latest_block()
        return {
            "pool_name": "Animica Stratum Pool",
            "network": self._config.network or f"chain-{self._config.chain_id}",
            "height": (job.height if job else None) or 0,
            "last_block_hash": latest_block.get("hash") or "0x0",
            "pool_hashrate": pool_hashrate,
            "hashrate_series": self._hashrate_series(60),
            "hashrate_1m": self._hashrate_from_db(60) or self._hashrate_from_events(share_events, 60),
            "hashrate_15m": self._hashrate_from_db(900) or self._hashrate_from_events(share_events, 900),
            "hashrate_1h": self._hashrate_from_db(3600) or self._hashrate_from_events(share_events, 3600),
            "num_miners": stats.get("clients", 0),
            "num_workers": stats.get("clients", 0),
            "round_duration_seconds": self._config.poll_interval,
            "round_shares": len(share_events),
            "round_estimated_reward": "0",
            "uptime_seconds": stats.get("uptime_sec", int(time.time() - self._started)),
            "stratum_endpoint": f"stratum+tcp://{self._config.host}:{self._config.port}",
            "last_update": self._now_iso(),
            "latest_block": latest_block,
        }

    def miners(self) -> Dict[str, object]:
        sessions = self._server.session_snapshots()
        session_map = {str(s.get("worker") or s.get("session_id")): s for s in sessions}

        cutoff_1m = time.time() - 60
        cutoff_15m = time.time() - 900
        cutoff_1h = time.time() - 3600
        cutoff_max = min(cutoff_1m, cutoff_15m, cutoff_1h)

        aggregates: Dict[str, Dict[str, object]] = {}
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
            for row in rows:
                worker_id, address, accepted, rejected, diff1, diff15, diff60, last_ts = row
                aggregates[str(worker_id)] = {
                    "address": address or "",
                    "shares_accepted": int(accepted or 0),
                    "shares_rejected": int(rejected or 0),
                    "hashrate_1m": float(diff1 or 0.0) / 60,
                    "hashrate_15m": float(diff15 or 0.0) / 900,
                    "hashrate_1h": float(diff60 or 0.0) / 3600,
                    "last_share_at": last_ts,
                }

        events_by_worker: Dict[str, List[ShareEvent]] = defaultdict(list)
        for ev in self._share_events:
            events_by_worker[str(ev.get("worker"))].append(ev)

        items: List[Dict[str, object]] = []
        seen_workers = set()
        for worker_id, session in session_map.items():
            worker_events = events_by_worker.get(str(worker_id), [])
            agg = aggregates.get(worker_id, {})
            items.append(
                {
                    "worker_id": worker_id,
                    "worker_name": worker_id,
                    "address": agg.get("address") or session.get("address") or "",
                    "hashrate_1m": agg.get("hashrate_1m") or self._hashrate_from_events(worker_events, 60),
                    "hashrate_15m": agg.get("hashrate_15m") or self._hashrate_from_events(worker_events, 900),
                    "hashrate_1h": agg.get("hashrate_1h") or self._hashrate_from_events(worker_events, 3600),
                    "last_share_at": agg.get("last_share_at") or session.get("last_share_at"),
                    "difficulty": session.get("current_difficulty") or session.get("share_target"),
                    "shares_accepted": agg.get("shares_accepted") or session.get("shares_accepted", 0),
                    "shares_rejected": agg.get("shares_rejected") or session.get("shares_rejected", 0),
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
                    "address": agg.get("address") or "",
                    "hashrate_1m": agg.get("hashrate_1m") or self._hashrate_from_events(worker_events, 60),
                    "hashrate_15m": agg.get("hashrate_15m") or self._hashrate_from_events(worker_events, 900),
                    "hashrate_1h": agg.get("hashrate_1h") or self._hashrate_from_events(worker_events, 3600),
                    "last_share_at": agg.get("last_share_at"),
                    "difficulty": None,
                    "shares_accepted": agg.get("shares_accepted") or 0,
                    "shares_rejected": agg.get("shares_rejected") or 0,
                }
            )

        return {"items": items, "total": len(items)}

    def miner_detail(self, worker_id: str) -> Dict[str, object]:
        session = next(
            (s for s in self._server.session_snapshots() if str(s.get("worker") or s.get("session_id")) == worker_id), None
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

        if not events and session is None:
            return {}

        timeseries: List[Tuple[str, float]] = []
        for bucket in sorted(buckets.keys()):
            ts_iso = datetime.fromtimestamp(bucket, tz=timezone.utc).isoformat()
            timeseries.append((ts_iso, buckets[bucket] / 60))

        accepted = sum(1 for ev in events if ev.get("status") == "accepted")
        rejected = sum(1 for ev in events if ev.get("status") == "rejected")
        latest = events[-1] if events else None
        return {
            "address": latest.get("address") if latest else "",
            "worker_name": worker_id,
            "hashrate_timeseries": timeseries,
            "last_share": {
                "time": datetime.fromtimestamp(latest["timestamp"], tz=timezone.utc).isoformat() if latest else None,
                "difficulty": latest.get("difficulty") if latest else None,
                "status": latest.get("status") if latest else None,
            },
            "shares_accepted": accepted,
            "shares_rejected": rejected,
            "current_difficulty": (latest.get("difficulty") if latest else 0) or 0,
            "connected_since": (
                datetime.fromtimestamp(session["connected_since"], tz=timezone.utc).isoformat()
                if session and session.get("connected_since")
                else None
            ),
        }

    def recent_blocks(self) -> Dict[str, object]:
        items: List[Dict[str, object]] = []
        if self._db is not None:
            with self._db_lock:
                rows = self._db.execute(
                    "SELECT height, job_id, ts, found_by_pool, tx_count FROM blocks ORDER BY ts DESC LIMIT 50"
                ).fetchall()
            for height, job_id, ts, found, tx_count in rows:
                items.append(
                    {
                        "height": height,
                        "hash": job_id,
                        "timestamp": datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat() if ts else None,
                        "found_by_pool": bool(found),
                        "reward": "0",
                        "tx_count": tx_count,
                    }
                )

        if not items:
            blocks = list(self._block_events)
            items = [
                {
                    "height": blk.get("height"),
                    "hash": blk.get("job_id"),
                    "timestamp": datetime.fromtimestamp(float(blk.get("timestamp")), tz=timezone.utc).isoformat()
                    if blk.get("timestamp")
                    else None,
                    "found_by_pool": blk.get("found_by_pool", False),
                    "reward": "0",
                    "tx_count": blk.get("tx_count"),
                }
                for blk in blocks
            ]

        return {"items": items, "total": len(items)}

    def health(self) -> Dict[str, object]:
        return {"status": "ok", "uptime": int(time.time() - self._started)}
