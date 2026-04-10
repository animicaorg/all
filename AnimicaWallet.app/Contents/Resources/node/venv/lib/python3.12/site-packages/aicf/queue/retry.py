from __future__ import annotations

"""
aicf.queue.retry
================

Backoff & requeue policy for jobs that time out (no proof before lease
deadline) or fail with a provider-returned error. This module is storage-
agnostic: it expects the storage layer to expose a small set of methods
(documented via the _StorageProtocol below). All state changes happen
atomically inside the storage layer (transactions).

Usage
-----
    policy = RetryPolicy()
    retry = RetryEngine(storage, policy)

    # On lease timeout:
    retry.on_timeout(job_id, lease_id)

    # On provider/verification failure:
    retry.on_failure(job_id, error_code="proof_invalid", message="bad merkle root")

Design notes
------------
- Exponential backoff with jitter and an upper bound.
- Clear separation between transient vs permanent errors.
- Caps on total retry attempts to avoid infinite churn.
- Optional Prometheus metrics are imported softly; if not present, no-ops.

"""

import logging
import math
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Protocol, Tuple

log = logging.getLogger(__name__)

# ────────────────────────────── Optional metrics ──────────────────────────────
try:
    from aicf.metrics import \
        COUNTER_RETRY_SCHEDULED as _C_RETRY_SCHED  # type: ignore
    from aicf.metrics import COUNTER_RETRY_TOMBSTONED as _C_RETRY_TOMB
    from aicf.metrics import HISTOGRAM_RETRY_DELAY_SECONDS as _H_RETRY_DELAY
except Exception:  # pragma: no cover - fallback no-ops

    class _Noop:
        def inc(self, *_: float, **__: Any) -> None: ...
        def observe(self, *_: float, **__: Any) -> None: ...

    _C_RETRY_SCHED = _Noop()
    _C_RETRY_TOMB = _Noop()
    _H_RETRY_DELAY = _Noop()


# ───────────────────────────── Storage expectations ───────────────────────────


class _StorageProtocol(Protocol):
    """
    Minimal interface this module relies on. Your concrete storage should ensure
    these methods are atomic and durable.

    Methods:
        get_job(job_id) -> dict-like with keys:
            attempts:int, status:str, last_error:str|None

        schedule_retry(job_id, available_at: datetime, last_error: str, attempts: int, now: datetime) -> None
            - Marks job back to QUEUED (or PENDING) and sets next availability.
            - Clears/finishes any active lease.

        tombstone_job(job_id, reason: str, now: datetime) -> None
            - Marks job permanently failed; prevents future scheduling.

        release_lease(lease_id: str, now: datetime) -> None
            - Best-effort lease release (safe if lease already expired).
    """

    # Signatures are for type checking/documentation only.
    def get_job(self, job_id: str) -> Dict[str, Any]: ...
    def schedule_retry(
        self,
        job_id: str,
        available_at: datetime,
        last_error: str,
        attempts: int,
        now: datetime,
    ) -> None: ...
    def tombstone_job(self, job_id: str, reason: str, now: datetime) -> None: ...
    def release_lease(self, lease_id: str, now: datetime) -> None: ...


# ─────────────────────────────── Retry policy ─────────────────────────────────


@dataclass(frozen=True)
class RetryPolicy:
    """
    Backoff policy parameters and error classification.

    attempts_cap: Maximum number of (re)tries allowed; reaching this results in tombstone.
    base_delay: Base delay for first retry.
    multiplier: Exponential scale factor per attempt.
    max_delay: Upper bound for backoff.
    jitter_fraction: +/- fraction applied as random jitter to avoid herd effects.
    transient_errors: Error codes considered transient (retryable).
    permanent_errors: Error codes considered permanent (not retryable).

    Error code guidance (examples)
    ------------------------------
    Transient:
        - "provider_unreachable", "deadline_exceeded", "internal_error",
          "lease_lost", "network_error", "temporarily_unavailable"
    Permanent:
        - "proof_invalid", "attestation_invalid", "job_too_large",
          "schema_invalid", "unsupported_algorithm"
    """

    attempts_cap: int = 6
    base_delay: float = 2.0
    multiplier: float = 1.8
    max_delay: float = 60.0
    jitter_fraction: float = 0.20
    transient_errors: frozenset[str] = field(
        default_factory=lambda: frozenset(
            {
                "provider_unreachable",
                "deadline_exceeded",
                "internal_error",
                "lease_lost",
                "network_error",
                "temporarily_unavailable",
                "queue_overloaded",
            }
        )
    )
    permanent_errors: frozenset[str] = field(
        default_factory=lambda: frozenset(
            {
                "proof_invalid",
                "attestation_invalid",
                "job_too_large",
                "schema_invalid",
                "unsupported_algorithm",
                "forbidden",
                "payment_required",
            }
        )
    )

    def classify(self, error_code: str) -> str:
        ec = (error_code or "").strip().lower()
        # Heuristic prefixes for permanence
        if ec in self.permanent_errors or ec.startswith(
            ("validation/", "proof/", "attestation/")
        ):
            return "permanent"
        if ec in self.transient_errors:
            return "transient"
        # Default to transient (safer to retry) unless clearly permanent.
        return "transient"

    def backoff_seconds(self, attempts: int) -> float:
        """
        Compute delay for the given attempts count (1-based after first failure).
        attempts = 1 → first retry delay = base_delay
        """
        # Protect against negative/zero attempts
        a = max(1, int(attempts))
        raw = self.base_delay * (self.multiplier ** (a - 1))
        return float(min(self.max_delay, raw))

    def with_jitter(self, seconds: float) -> float:
        jf = self.jitter_fraction
        jitter = seconds * jf * (2.0 * random.random() - 1.0)
        return max(0.0, seconds + jitter)


# ─────────────────────────────── Retry engine ─────────────────────────────────


class RetryEngine:
    """
    Implements timeout/failure handling according to a RetryPolicy.
    """

    def __init__(
        self, storage: _StorageProtocol, policy: Optional[RetryPolicy] = None
    ) -> None:
        self.storage = storage
        self.policy = policy or RetryPolicy()

    # Public API ---------------------------------------------------------------

    def on_timeout(
        self,
        job_id: str,
        lease_id: Optional[str] = None,
        now: Optional[datetime] = None,
    ) -> Tuple[bool, Optional[float]]:
        """
        Handle a lease timeout (no proof before deadline). We treat this as
        a transient "deadline_exceeded" error and schedule a retry with backoff.

        Returns (requeued, delay_seconds)
        """
        return self._retry(
            job_id,
            error_code="deadline_exceeded",
            message="lease expired without proof",
            lease_id=lease_id,
            now=now,
        )

    def on_failure(
        self,
        job_id: str,
        error_code: str,
        message: str = "",
        lease_id: Optional[str] = None,
        now: Optional[datetime] = None,
    ) -> Tuple[bool, Optional[float], bool]:
        """
        Handle an explicit failure (provider responded or verifier rejected).

        Returns (requeued, delay_seconds, tombstoned)
        """
        classification = self.policy.classify(error_code)
        if classification == "permanent":
            # No retry; tombstone immediately
            self._release_lease_safe(lease_id, now)
            self._tombstone(job_id, reason=f"{error_code}:{message}", now=now)
            _C_RETRY_TOMB.inc(1)  # type: ignore
            return (False, None, True)

        # Otherwise retry as transient
        requeued, delay = self._retry(
            job_id, error_code=error_code, message=message, lease_id=lease_id, now=now
        )
        return (requeued, delay, False)

    # Internal -----------------------------------------------------------------

    def _retry(
        self,
        job_id: str,
        error_code: str,
        message: str,
        lease_id: Optional[str],
        now: Optional[datetime],
    ) -> Tuple[bool, Optional[float]]:
        ts = _utc(now)
        job = self.storage.get_job(job_id)
        attempts = int(job.get("attempts", 0)) + 1

        if attempts > self.policy.attempts_cap:
            log.warning(
                "retry: attempts cap exceeded (job_id=%s attempts=%d) → tombstone",
                job_id,
                attempts,
            )
            self._release_lease_safe(lease_id, ts)
            self._tombstone(job_id, reason=f"attempts_cap:{error_code}", now=ts)
            _C_RETRY_TOMB.inc(1)  # type: ignore
            return (False, None)

        delay = self.policy.backoff_seconds(attempts)
        delay = self.policy.with_jitter(delay)
        available_at = ts + timedelta(seconds=delay)

        # Release lease (best-effort) before rescheduling
        self._release_lease_safe(lease_id, ts)

        last_error = _format_error(error_code, message)
        self.storage.schedule_retry(
            job_id=job_id,
            available_at=available_at,
            last_error=last_error,
            attempts=attempts,
            now=ts,
        )
        log.info(
            "retry: scheduled (job_id=%s attempts=%d delay=%.2fs error=%s)",
            job_id,
            attempts,
            delay,
            error_code,
        )
        _C_RETRY_SCHED.inc(1)  # type: ignore
        _H_RETRY_DELAY.observe(float(delay))  # type: ignore
        return (True, delay)

    def _tombstone(self, job_id: str, reason: str, now: Optional[datetime]) -> None:
        ts = _utc(now)
        self.storage.tombstone_job(job_id=job_id, reason=reason, now=ts)
        log.error("retry: tombstoned (job_id=%s reason=%s)", job_id, reason)

    def _release_lease_safe(
        self, lease_id: Optional[str], now: Optional[datetime]
    ) -> None:
        if not lease_id:
            return
        try:
            self.storage.release_lease(lease_id=lease_id, now=_utc(now))
        except Exception as e:  # pragma: no cover - defensive
            log.debug(
                "retry: release_lease failed (lease_id=%s err=%r) — ignoring",
                lease_id,
                e,
            )


# ──────────────────────────────── Utilities ───────────────────────────────────


def _utc(dt: Optional[datetime]) -> datetime:
    if dt is None:
        return datetime.now(timezone.utc)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _format_error(code: str, message: str) -> str:
    code = (code or "").strip().lower()
    msg = (message or "").strip()
    if not msg:
        return code
    return f"{code}:{msg}"


__all__ = ["RetryPolicy", "RetryEngine"]
