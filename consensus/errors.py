"""
Animica Consensus Errors

This module defines structured exceptions used across the consensus stack.
They are lightweight, serializable, and include machine-readable error codes
so that other modules (e.g., RPC, mining, tests) can classify failures
without string-matching.

Design goals
------------
- Stable, integer error codes (see `ErrorCode`).
- Human-friendly messages with optional rich context.
- Safe to log: context is shallow-copied and can be redacted upstream.
- Play nicely with `raise ... from cause` and `__cause__`.

Subclasses
----------
- PolicyError         : PoIES policy loading/violation issues.
- ThetaScheduleError  : Difficulty/Θ schedule or retarget anomalies.
- NullifierError      : Reuse/double-consumption of proof nullifiers.

NOTE: Keep this module free of heavy imports so it can be used in hot paths.
"""

from __future__ import annotations

from enum import IntEnum
from typing import Any, Dict, Mapping, Optional


class ErrorCode(IntEnum):
    """Stable error codes for consensus-layer exceptions."""
    CONSENSUS_GENERIC = 2000
    POLICY            = 2001
    THETA_SCHEDULE    = 2002
    NULLIFIER         = 2003


class ConsensusError(Exception):
    """
    Base class for consensus-layer exceptions.

    Parameters
    ----------
    message : str
        Human-readable description.
    code : ErrorCode | int
        Stable code for programmatic handling (default: CONSENSUS_GENERIC).
    context : Mapping[str, Any] | None
        Optional structured fields (small dict). Avoid large blobs in hot paths.
    cause : BaseException | None
        Optional underlying exception; also set via `raise ... from ...`.
    """

    def __init__(
        self,
        message: str,
        *,
        code: ErrorCode | int = ErrorCode.CONSENSUS_GENERIC,
        context: Optional[Mapping[str, Any]] = None,
        cause: Optional[BaseException] = None,
    ) -> None:
        super().__init__(message)
        self.message: str = message
        self.code: int = int(code)
        self.context: Dict[str, Any] = dict(context) if context else {}
        # Respect Python's exception chaining
        if cause is not None:
            self.__cause__ = cause  # type: ignore[attr-defined]

    def __str__(self) -> str:  # pragma: no cover - trivial
        tail = f" context={self.context}" if self.context else ""
        return f"[{self.code}] {self.message}{tail}"

    def to_dict(self) -> Dict[str, Any]:
        """Structured view suitable for logs or JSON-RPC error `data` fields."""
        out = {
            "code": self.code,
            "message": self.message,
        }
        if self.context:
            out["context"] = self.context
        return out


class PolicyError(ConsensusError):
    """
    Raised when PoIES policy fails to load/validate or a block/header violates it.

    Common fields
    -------------
    - section : which subpolicy (e.g., "caps.hashshare", "escort.q")
    - path    : dotted JSON/YAML path that failed
    - expected, actual : for validation mismatches
    - policy_root : hex of alg/policy Merkle root (if relevant)
    """

    def __init__(
        self,
        message: str,
        *,
        section: Optional[str] = None,
        path: Optional[str] = None,
        expected: Any = None,
        actual: Any = None,
        policy_root: Optional[str] = None,
        context: Optional[Mapping[str, Any]] = None,
        cause: Optional[BaseException] = None,
    ) -> None:
        base: Dict[str, Any] = {}
        if section is not None:
            base["section"] = section
        if path is not None:
            base["path"] = path
        if expected is not None:
            base["expected"] = expected
        if actual is not None:
            base["actual"] = actual
        if policy_root is not None:
            base["policy_root"] = policy_root
        if context:
            base.update(context)
        super().__init__(message, code=ErrorCode.POLICY, context=base, cause=cause)

    @classmethod
    def mismatch(
        cls,
        *,
        section: str,
        path: str,
        expected: Any,
        actual: Any,
        policy_root: Optional[str] = None,
    ) -> "PolicyError":
        return cls(
            f"Policy mismatch at {path}: expected={expected!r} actual={actual!r}",
            section=section,
            path=path,
            expected=expected,
            actual=actual,
            policy_root=policy_root,
        )


class ThetaScheduleError(ConsensusError):
    """
    Raised when Θ (Theta) scheduling / retarget computation detects an anomaly:
    - invalid window parameters
    - EMA/retarget clamping overflow
    - inconsistent observed inter-block interval λ_obs

    Context fields
    --------------
    - theta_prev, theta_next : previous/next micro-thresholds
    - interval_obs, interval_target : seconds (or slots) used in computation
    - window, clamp : retarget window/cap parameters
    - height : block height where it occurred (if known)
    """

    def __init__(
        self,
        message: str,
        *,
        theta_prev: Optional[int] = None,
        theta_next: Optional[int] = None,
        interval_obs: Optional[float] = None,
        interval_target: Optional[float] = None,
        window: Optional[int] = None,
        clamp: Optional[float] = None,
        height: Optional[int] = None,
        context: Optional[Mapping[str, Any]] = None,
        cause: Optional[BaseException] = None,
    ) -> None:
        base: Dict[str, Any] = {}
        if theta_prev is not None:
            base["theta_prev"] = theta_prev
        if theta_next is not None:
            base["theta_next"] = theta_next
        if interval_obs is not None:
            base["interval_obs"] = interval_obs
        if interval_target is not None:
            base["interval_target"] = interval_target
        if window is not None:
            base["window"] = window
        if clamp is not None:
            base["clamp"] = clamp
        if height is not None:
            base["height"] = height
        if context:
            base.update(context)
        super().__init__(message, code=ErrorCode.THETA_SCHEDULE, context=base, cause=cause)

    @classmethod
    def invalid_window(cls, *, window: int, height: Optional[int] = None) -> "ThetaScheduleError":
        return cls(
            "Theta retarget window must be > 0",
            window=window,
            height=height,
        )

    @classmethod
    def clamp_overflow(
        cls,
        *,
        theta_prev: int,
        computed: int,
        clamp: float,
        height: Optional[int] = None,
    ) -> "ThetaScheduleError":
        return cls(
            "Theta retarget exceeded clamp bounds",
            theta_prev=theta_prev,
            theta_next=computed,
            clamp=clamp,
            height=height,
        )


class NullifierError(ConsensusError):
    """
    Raised when a proof's nullifier is reused or otherwise invalid.

    Context fields
    --------------
    - proof_type : e.g., "hashshare", "ai", "quantum", "storage", "vdf"
    - nullifier  : hex string (domain-separated hash)
    - first_seen_height : height where nullifier was first accepted (if known)
    - ttl_blocks : number of blocks the nullifier is quarantined (policy)
    - reason     : short machine-friendly reason ('reused', 'expired', 'domain-mismatch', ...)
    """

    def __init__(
        self,
        message: str,
        *,
        proof_type: Optional[str] = None,
        nullifier: Optional[str] = None,
        first_seen_height: Optional[int] = None,
        ttl_blocks: Optional[int] = None,
        reason: Optional[str] = None,
        context: Optional[Mapping[str, Any]] = None,
        cause: Optional[BaseException] = None,
    ) -> None:
        base: Dict[str, Any] = {}
        if proof_type is not None:
            base["proof_type"] = proof_type
        if nullifier is not None:
            base["nullifier"] = nullifier
        if first_seen_height is not None:
            base["first_seen_height"] = first_seen_height
        if ttl_blocks is not None:
            base["ttl_blocks"] = ttl_blocks
        if reason is not None:
            base["reason"] = reason
        if context:
            base.update(context)
        super().__init__(message, code=ErrorCode.NULLIFIER, context=base, cause=cause)

    @classmethod
    def reused(
        cls,
        *,
        proof_type: str,
        nullifier: str,
        first_seen_height: int,
        ttl_blocks: int,
    ) -> "NullifierError":
        return cls(
            "Nullifier already used within TTL window",
            proof_type=proof_type,
            nullifier=nullifier,
            first_seen_height=first_seen_height,
            ttl_blocks=ttl_blocks,
            reason="reused",
        )

    @classmethod
    def domain_mismatch(cls, *, proof_type: str, nullifier: str) -> "NullifierError":
        return cls(
            "Nullifier domain mismatch for proof type",
            proof_type=proof_type,
            nullifier=nullifier,
            reason="domain-mismatch",
        )


__all__ = [
    "ErrorCode",
    "ConsensusError",
    "PolicyError",
    "ThetaScheduleError",
    "NullifierError",
]
