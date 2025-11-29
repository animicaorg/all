from __future__ import annotations

import time
from collections import defaultdict, deque
from datetime import datetime, timezone
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

    def _now_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _hashrate_from_events(self, events: List[ShareEvent], window_seconds: float) -> float:
        if not events:
            return 0.0
        cutoff = time.time() - window_seconds
        total = sum(float(ev.get("difficulty") or 0.0) for ev in events if ev["timestamp"] >= cutoff and ev["status"] == "accepted")
        return total / window_seconds if window_seconds > 0 else 0.0

    def pool_summary(self) -> Dict[str, object]:
        stats = self._server.stats()
        job = self._job_manager.current_job()
        share_events = list(self._share_events)
        return {
            "pool_name": "Animica Stratum Pool",
            "network": f"chain-{self._config.chain_id}",
            "height": (job.height if job else None) or 0,
            "last_block_hash": (job.header.get("hash") if job and job.header else None) or "0x0",
            "pool_hashrate": self._hashrate_from_events(share_events, 600),
            "num_miners": stats.get("clients", 0),
            "num_workers": stats.get("clients", 0),
            "round_duration_seconds": self._config.poll_interval,
            "round_shares": len(share_events),
            "round_estimated_reward": "0",
            "uptime_seconds": stats.get("uptime_sec", int(time.time() - self._started)),
            "stratum_endpoint": f"stratum+tcp://{self._config.host}:{self._config.port}",
            "last_update": self._now_iso(),
        }

    def miners(self) -> Dict[str, object]:
        sessions = self._server.session_snapshots()
        events_by_worker: Dict[str, List[ShareEvent]] = defaultdict(list)
        for ev in self._share_events:
            events_by_worker[str(ev.get("worker"))].append(ev)

        items: List[Dict[str, object]] = []
        for session in sessions:
            worker_id = session.get("worker") or session.get("session_id")
            worker_events = events_by_worker.get(str(worker_id), [])
            items.append(
                {
                    "worker_id": worker_id,
                    "worker_name": worker_id,
                    "address": session.get("address") or "",
                    "hashrate_1m": self._hashrate_from_events(worker_events, 60),
                    "hashrate_15m": self._hashrate_from_events(worker_events, 900),
                    "hashrate_1h": self._hashrate_from_events(worker_events, 3600),
                    "last_share_at": session.get("last_share_at"),
                    "difficulty": session.get("current_difficulty") or session.get("share_target"),
                    "shares_accepted": session.get("shares_accepted", 0),
                    "shares_rejected": session.get("shares_rejected", 0),
                }
            )

        return {"items": items, "total": len(items)}

    def miner_detail(self, worker_id: str) -> Dict[str, object]:
        events = [ev for ev in self._share_events if str(ev.get("worker")) == worker_id]
        session = next(
            (s for s in self._server.session_snapshots() if str(s.get("worker") or s.get("session_id")) == worker_id), None
        )
        if not events and session is None:
            return {}

        cutoff = time.time() - 3600
        buckets: Dict[int, List[ShareEvent]] = defaultdict(list)
        for ev in events:
            if ev["timestamp"] >= cutoff:
                bucket = int(ev["timestamp"] // 60)
                buckets[bucket].append(ev)

        timeseries: List[Tuple[str, float]] = []
        for bucket in sorted(buckets.keys()):
            bucket_events = buckets[bucket]
            ts = datetime.fromtimestamp(bucket * 60, tz=timezone.utc).isoformat()
            timeseries.append((ts, self._hashrate_from_events(bucket_events, 60)))

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
            "shares_accepted": sum(1 for ev in events if ev.get("status") == "accepted"),
            "shares_rejected": sum(1 for ev in events if ev.get("status") == "rejected"),
            "current_difficulty": (latest.get("difficulty") if latest else 0) or 0,
            "connected_since": (
                datetime.fromtimestamp(session["connected_since"], tz=timezone.utc).isoformat()
                if session and session.get("connected_since")
                else None
            ),
        }

    def recent_blocks(self) -> Dict[str, object]:
        blocks = list(self._block_events)
        return {
            "items": [
                {
                    "height": blk.get("height"),
                    "hash": blk.get("job_id"),
                    "timestamp": datetime.fromtimestamp(float(blk.get("timestamp")), tz=timezone.utc).isoformat()
                    if blk.get("timestamp")
                    else None,
                    "found_by_pool": blk.get("found_by_pool", False),
                    "reward": "0",
                }
                for blk in blocks
            ],
            "total": len(blocks),
        }

    def health(self) -> Dict[str, object]:
        return {"status": "ok", "uptime": int(time.time() - self._started)}
