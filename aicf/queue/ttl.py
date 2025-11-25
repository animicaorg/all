from __future__ import annotations
"""
aicf.queue.ttl
==============

Expiration policies and garbage collection of stale jobs.

This module provides:
- `TTLPolicy`: configurable time limits for different job states.
- `TTLGc`: a small engine that scans storage and expires/purges jobs.

Storage is abstracted behind a narrow protocol so this module stays
backend-agnostic (SQLite, RocksDB, etc.). All mutations must be done
atomically by the storage layer.

Typical usage
-------------
    policy = TTLPolicy()
    gc = TTLGc(storage, policy)

    # Periodic sweep (e.g., every 30–60s):
    stats = gc.sweep_once()

Design goals
------------
- Make *no* assumptions about scheduler timing; act purely on timestamps.
- Separate "expire" (transition to terminal state) vs "purge" (hard delete).
- Absolute cap via `max_total_age` as a safety valve.
- Soft integration with Prometheus metrics (no dependency required).
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Protocol, Optional, Dict, Any, Iterable, Tuple
import logging

log = logging.getLogger(__name__)

# ────────────────────────────── Optional metrics ──────────────────────────────
try:
    from aicf.metrics import (  # type: ignore
        COUNTER_TTL_EXPIRED as _C_TTL_EXPIRED,
        COUNTER_TTL_PURGED as _C_TTL_PURGED,
        GAUGE_TTL_LAST_SWEEP_TS as _G_TTL_LAST_SWEEP_TS,
        HISTOGRAM_TTL_SWEEP_SECONDS as _H_TTL_SWEEP_SEC,
    )
except Exception:  # pragma: no cover - metrics are optional

    class _Noop:
        def inc(self, *_: float, **__: Any) -> None: ...
        def set(self, *_: float, **__: Any) -> None: ...
        def observe(self, *_: float, **__: Any) -> None: ...

    _C_TTL_EXPIRED = _Noop()
    _C_TTL_PURGED = _Noop()
    _G_TTL_LAST_SWEEP_TS = _Noop()
    _H_TTL_SWEEP_SEC = _Noop()


# ─────────────────────────────── Job status enum ──────────────────────────────

try:
    from aicf.aitypes.job import JobStatus  # type: ignore
except Exception:
    # Minimal shim to avoid hard import dependency. Keep names aligned with
    # aicf/types/job.py.
    class JobStatus:
        QUEUED = "QUEUED"
        PENDING = "PENDING"
        LEASED = "LEASED"
        COMPLETED = "COMPLETED"
        FAILED = "FAILED"
        TOMBSTONED = "TOMBSTONED"
        EXPIRED = "EXPIRED"  # terminal alias used by GC


# ─────────────────────────────── Storage protocol ─────────────────────────────


class _TtlStorage(Protocol):
    """
    Minimal API the TTL GC relies on. Implementations MUST ensure operations that
    mutate state are atomic (e.g., inside a DB transaction).

    Methods:
        iter_expirable(cutoffs) -> iterable of rows (dict-like) with at least:
            {
              "job_id": str,
              "status": str,
              "enqueued_at": datetime,
              "updated_at": datetime | None,
              "lease_expires_at": datetime | None,
              "completed_at": datetime | None,
              "failed_at": datetime | None,
              "tombstoned_at": datetime | None,
            }

            Implementations can ignore the cutoffs and return a superset; the
            GC will double-check before acting.

        mark_expired(job_id, reason, now) -> None
            Transition job to a terminal EXPIRED/TOMBSTONED-like status and
            free any active lease. Should be idempotent.

        purge_job(job_id, reason, now) -> None
            Permanently delete the job and any auxiliary records (indexes,
            payloads, logs). Idempotent is appreciated.

        find_active_lease(job_id) -> Optional[str]
            Optional helper; if not provided, `mark_expired` should already
            handle lease cleanup.

        release_lease(lease_id, now) -> None
            Optional; only used if provided together with find_active_lease.
    """

    def iter_expirable(self, cutoffs: Dict[str, datetime]) -> Iterable[Dict[str, Any]]: ...
    def mark_expired(self, job_id: str, reason: str, now: datetime) -> None: ...
    def purge_job(self, job_id: str, reason: str, now: datetime) -> None: ...
    # Optional extras:
    def find_active_lease(self, job_id: str) -> Optional[str]: ...  # type: ignore[override]
    def release_lease(self, lease_id: str, now: datetime) -> None: ...  # type: ignore[override]


# ──────────────────────────────── TTL policy ─────────────────────────────────


@dataclass(frozen=True)
class TTLPolicy:
    """
    Time limits for different job lifecycle phases.

    queued_ttl:
        Maximum time a job may sit QUEUED/PENDING without progress.

    leased_grace:
        Grace after lease_expires_at for stuck leases before EXPIRATION.

    completed_retention:
        How long COMPLETED jobs are retained before PURGE.

    failed_retention:
        How long FAILED/TOMBSTONED/EXPIRED jobs are retained before PURGE.

    max_total_age:
        Absolute cap from enqueued_at regardless of status (defensive).

    Notes:
    - All durations are applied relative to their respective reference times.
    - If multiple rules apply, the strongest action wins (PURGE > EXPIRE > KEEP).
    """
    queued_ttl: timedelta = timedelta(minutes=30)
    leased_grace: timedelta = timedelta(minutes=10)
    completed_retention: timedelta = timedelta(hours=1)
    failed_retention: timedelta = timedelta(hours=1)
    max_total_age: timedelta = timedelta(days=2)


# ─────────────────────────────── GC engine ────────────────────────────────────


class TTLGc:
    """
    Periodically scans storage and applies TTL/retention rules.

    The `sweep_once()` method is cheap to call frequently; it asks storage for
    *candidates* based on coarse cutoffs, then validates each row precisely.
    """

    def __init__(self, storage: _TtlStorage, policy: Optional[TTLPolicy] = None) -> None:
        self.storage = storage
        self.policy = policy or TTLPolicy()

    def sweep_once(self, now: Optional[datetime] = None) -> Dict[str, int]:
        """
        Run a single sweep cycle. Returns counters:
            {"expired": N, "purged": M, "kept": K}
        """
        start_ts = _utc(now)
        p = self.policy

        cutoffs = {
            "queued_before": start_ts - p.queued_ttl,
            "lease_expired_before": start_ts - p.leased_grace,
            "completed_before": start_ts - p.completed_retention,
            "failed_before": start_ts - p.failed_retention,
            "too_old_before": start_ts - p.max_total_age,
        }

        expired = purged = kept = 0

        for row in self.storage.iter_expirable(cutoffs):
            action = self._decide_action(row, start_ts)
            job_id = str(row.get("job_id"))

            if action == "PURGE":
                try:
                    self.storage.purge_job(job_id, reason="ttl.purge", now=start_ts)
                    purged += 1
                    _C_TTL_PURGED.inc(1)  # type: ignore
                    log.info("ttl: purged job_id=%s", job_id)
                except Exception as e:  # pragma: no cover - defensive
                    log.exception("ttl: purge failed job_id=%s err=%r", job_id, e)
                    kept += 1  # leave it for next round
            elif action == "EXPIRE":
                try:
                    # Best effort lease drop if supported:
                    lease_id = None
                    if hasattr(self.storage, "find_active_lease"):
                        try:
                            lease_id = self.storage.find_active_lease(job_id)  # type: ignore[attr-defined]
                        except Exception:
                            lease_id = None
                    if lease_id and hasattr(self.storage, "release_lease"):
                        try:
                            self.storage.release_lease(lease_id, now=start_ts)  # type: ignore[attr-defined]
                        except Exception:
                            pass

                    self.storage.mark_expired(job_id, reason="ttl.expired", now=start_ts)
                    expired += 1
                    _C_TTL_EXPIRED.inc(1)  # type: ignore
                    log.info("ttl: expired job_id=%s", job_id)
                except Exception as e:  # pragma: no cover - defensive
                    log.exception("ttl: expire failed job_id=%s err=%r", job_id, e)
                    kept += 1
            else:
                kept += 1

        # metrics
        try:
            _G_TTL_LAST_SWEEP_TS.set(start_ts.timestamp())  # type: ignore
        except Exception:
            pass

        _H_TTL_SWEEP_SEC.observe(max(0.0, (_utc() - start_ts).total_seconds()))  # type: ignore

        return {"expired": expired, "purged": purged, "kept": kept}

    # ───────────────────────────── internals ──────────────────────────────

    def _decide_action(self, row: Dict[str, Any], now: datetime) -> str:
        """
        Decide "PURGE", "EXPIRE", or "KEEP" for a job row.
        """
        status = str(row.get("status") or "").upper()
        enq = _dt(row.get("enqueued_at"))
        upd = _dt(row.get("updated_at"))
        lease_exp = _dt(row.get("lease_expires_at"))
        completed_at = _dt(row.get("completed_at"))
        failed_at = _dt(row.get("failed_at"))
        tomb_at = _dt(row.get("tombstoned_at"))

        p = self.policy

        # Absolute age cap (wins over other rules)
        if enq and (now - enq) > p.max_total_age:
            if status in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.TOMBSTONED, JobStatus.EXPIRED):
                return "PURGE"
            return "EXPIRE"

        # Terminal states → purge after retention
        if status == JobStatus.COMPLETED:
            if completed_at and (now - completed_at) > p.completed_retention:
                return "PURGE"
            return "KEEP"

        if status in (JobStatus.FAILED, JobStatus.TOMBSTONED, JobStatus.EXPIRED):
            # Choose timestamp to compare against
            t0 = failed_at or tomb_at or upd or enq
            if t0 and (now - t0) > p.failed_retention:
                return "PURGE"
            return "KEEP"

        # Active states
        if status in (JobStatus.QUEUED, JobStatus.PENDING):
            t0 = upd or enq
            if t0 and (now - t0) > p.queued_ttl:
                return "EXPIRE"
            return "KEEP"

        if status == JobStatus.LEASED:
            # If lease has long expired → expire
            if lease_exp and (now - lease_exp) > p.leased_grace:
                return "EXPIRE"
            return "KEEP"

        # Unknown status → keep (defensive)
        return "KEEP"


# ───────────────────────────────── Utilities ─────────────────────────────────


def _utc(dt: Optional[datetime] = None) -> datetime:
    if dt is None:
        return datetime.now(timezone.utc)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _dt(x: Any) -> Optional[datetime]:
    if x is None:
        return None
    if isinstance(x, datetime):
        return _utc(x)
    # Accept ISO strings as a convenience for some storages/tests
    if isinstance(x, str):
        try:
            # fromisoformat supports offset-aware strings in 3.11
            return _utc(datetime.fromisoformat(x))
        except Exception:
            return None
    return None


__all__ = ["TTLPolicy", "TTLGc"]
