"""
capabilities.errors
-------------------

Exception hierarchy for the Animica capabilities subsystem.

These errors are raised by host-side syscall providers (blob, AI/Quantum enqueue,
zk.verify, deterministic randomness, treasury hooks) and are expected to be
caught at the runtime boundary (vm_py/execution adapters) to produce stable,
structured failure reports.

Design goals
~~~~~~~~~~~~
- Lightweight: no non-stdlib dependencies.
- Deterministic payloads: error dicts avoid including nondeterministic/system
  details (timestamps, PIDs). Large blobs are summarized, not embedded.
- Stable codes: upper-snake ASCII identifiers suitable for RPC/ABI surfaces.
"""

from __future__ import annotations

from typing import Any, Dict, Optional


def _truncate(data: Any, max_len: int = 256) -> Any:
    """
    Truncate large strings/bytes for safe inclusion in diagnostics.
    Containers (list/tuple/dict) are shallowly summarized.
    """
    try:
        if isinstance(data, (bytes, bytearray)):
            if len(data) <= max_len:
                return bytes(data)
            return bytes(data[:max_len]) + b"..."
        if isinstance(data, str):
            if len(data) <= max_len:
                return data
            return data[:max_len] + "..."
        if isinstance(data, (list, tuple)):
            return [_truncate(x, max_len) for x in data[:16]] + (
                ["..."] if len(data) > 16 else []
            )
        if isinstance(data, dict):
            out: Dict[str, Any] = {}
            for i, (k, v) in enumerate(data.items()):
                if i >= 16:
                    out["..."] = "truncated"
                    break
                out[str(k)] = _truncate(v, max_len)
            return out
        return data
    except Exception:
        # Never let diagnostic formatting raise
        return "<unprintable>"


class CapError(Exception):
    """
    Base class for capability errors.

    Attributes
    ----------
    code : str
        Stable, upper-snake ASCII identifier (e.g., 'LIMIT_EXCEEDED').
    message : str
        Human-friendly explanation (single line preferred).
    details : dict | None
        Optional structured data safe to expose over RPC/ABI.
    retryable : bool
        Hint to callers whether retrying later *might* succeed.
    """

    code: str = "CAP_ERROR"

    def __init__(
        self,
        message: str = "capability error",
        *,
        details: Optional[Dict[str, Any]] = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.details = _truncate(details or {})
        self.retryable = bool(retryable)

    def to_dict(self) -> Dict[str, Any]:
        """
        Structured representation safe for logs/RPC.
        """
        return {
            "code": self.code,
            "message": self.message,
            "details": self.details,
            "retryable": self.retryable,
        }

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"{self.code}: {self.message}"


class NotDeterministic(CapError):
    """
    Raised when a syscall request would violate determinism guarantees
    (e.g., missing seeds, non-whitelisted options, time-dependent input).
    """

    code = "NOT_DETERMINISTIC"

    def __init__(
        self,
        message: str = "request violates determinism constraints",
        *,
        source: Optional[str] = None,
        field: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        dd = {"source": source, "field": field}
        if details:
            dd.update(details)
        super().__init__(message, details=dd, retryable=False)


class LimitExceeded(CapError):
    """
    Raised when configured ceilings are exceeded (payload/result/queue/rate).
    """

    code = "LIMIT_EXCEEDED"

    def __init__(
        self,
        *,
        limit_name: str,
        limit_value: int,
        observed: Optional[int] = None,
        message: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        msg = message or f"limit exceeded: {limit_name} (max {limit_value})"
        dd = {"limit": limit_name, "max": int(limit_value)}
        if observed is not None:
            dd["observed"] = int(observed)
        if details:
            dd.update(details)
        super().__init__(msg, details=dd, retryable=False)


class NoResultYet(CapError):
    """
    Raised when a result for a deterministic task_id is not available yet.
    This is *not* a hard failure: the canonical contract flow is to attempt
    consumption next block/round.

    Hints:
    - 'retry_after_blocks' suggests when it may become available.
    - 'status' can be 'queued' | 'leased' | 'running' | 'pending_proof'.
    """

    code = "NO_RESULT_YET"

    def __init__(
        self,
        *,
        task_id: str,
        status: str = "queued",
        retry_after_blocks: int = 1,
        message: str = "result not available yet",
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        dd = {
            "task_id": str(task_id),
            "status": status,
            "retry_after_blocks": int(max(0, retry_after_blocks)),
        }
        if details:
            dd.update(details)
        super().__init__(message, details=dd, retryable=True)


class AttestationError(CapError):
    """
    Raised when provider attestation/evidence fails policy checks
    (TEE/QPU identity, measurements, certificates, traps/QoS thresholds).
    """

    code = "ATTESTATION_ERROR"

    def __init__(
        self,
        *,
        provider_id: Optional[str] = None,
        reason: Optional[str] = None,
        evidence_ref: Optional[str] = None,
        message: str = "attestation verification failed",
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        dd = {
            "provider_id": provider_id,
            "reason": reason,
            "evidence": evidence_ref,
        }
        if details:
            dd.update(details)
        super().__init__(message, details=dd, retryable=False)


__all__ = [
    "CapError",
    "NotDeterministic",
    "LimitExceeded",
    "NoResultYet",
    "AttestationError",
]
