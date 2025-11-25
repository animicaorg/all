from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import Enum, IntEnum
from typing import Any, Dict, Optional


class MiningErrorCode(IntEnum):
    """Stable, machine-consumable error codes for miner & pool flows."""
    MINER_ERROR = 1000
    WORK_EXPIRED = 1001
    SUBMIT_REJECTED = 1002
    DEVICE_UNAVAILABLE = 1003


class SubmitRejectReason(str, Enum):
    """Why a pool/node rejected a submitted share or block."""
    STALE = "stale"                # arrived after new head/template
    DUPLICATE = "duplicate"        # already seen nullifier/nonce
    LOW_DIFFICULTY = "low_difficulty"  # below micro-target/Θ
    INVALID = "invalid"            # header/proof failed verification
    POLICY = "policy"              # caps/Γ/fairness or fee/policy violation
    OTHER = "other"                # catch-all


@dataclass
class MinerError(Exception):
    """
    Base class for miner-facing errors.

    Attributes
    ----------
    message : str
        Human-friendly explanation (safe to log).
    code : MiningErrorCode
        Programmatic code stable across releases.
    retryable : bool
        Whether an automated retry has a reasonable chance to succeed *without*
        changing inputs (e.g., re-sending the exact same payload).
    context : dict
        Small, JSON-serializable context (non-sensitive) for diagnostics.
    action : Optional[str]
        One-word hint for orchestrators (e.g., "refresh_template", "backoff").
    """
    message: str
    code: MiningErrorCode = MiningErrorCode.MINER_ERROR
    retryable: bool = False
    context: Dict[str, Any] = field(default_factory=dict)
    action: Optional[str] = None

    def __str__(self) -> str:  # pragma: no cover - trivial
        base = f"[{self.code}] {self.message}"
        if self.action:
            base += f" (action={self.action})"
        if self.context:
            base += f" ctx={self.context}"
        return base

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["code"] = int(self.code)
        return d


@dataclass
class WorkExpired(MinerError):
    """
    The work/template used to build a share/block is no longer valid
    (e.g., new head observed, template epoch rolled, Θ updated).
    """
    message: str = "work/template expired; refresh and rebuild candidate"
    code: MiningErrorCode = MiningErrorCode.WORK_EXPIRED
    # Retry is meaningful if we *change inputs* (i.e., fetch new template).
    retryable: bool = True
    action: str = "refresh_template"


@dataclass
class SubmitRejected(MinerError):
    """
    The node/pool rejected a submitted share or block.
    Retryability depends on reason:
      - stale: retry *with new work* (not the same payload)
      - duplicate/low_difficulty/invalid/policy: not retryable as-is
    """
    reason: SubmitRejectReason = SubmitRejectReason.OTHER
    details: Optional[str] = None

    def __post_init__(self) -> None:
        self.code = MiningErrorCode.SUBMIT_REJECTED
        if self.reason == SubmitRejectReason.STALE:
            self.retryable = True
            self.action = "refresh_template"
        else:
            self.retryable = False
            if self.reason in (SubmitRejectReason.LOW_DIFFICULTY,):
                self.action = "raise_difficulty"  # or adjust share_target
            elif self.reason in (SubmitRejectReason.DUPLICATE,):
                self.action = "dedupe"
            elif self.reason in (SubmitRejectReason.INVALID, SubmitRejectReason.POLICY):
                self.action = "inspect"

        # enrich context
        self.context.setdefault("reason", self.reason.value)
        if self.details:
            self.context.setdefault("details", self.details)

        if not getattr(self, "message", None):
            self.message = f"submit rejected: {self.reason.value}"

    def to_dict(self) -> Dict[str, Any]:
        d = super().to_dict()
        d["reason"] = self.reason.value
        if self.details:
            d["details"] = self.details
        return d


@dataclass
class DeviceUnavailable(MinerError):
    """
    The selected device backend is not available (driver/library missing,
    out of memory, device busy, or feature not supported).
    """
    device: str = "cpu"
    message: str = "mining device unavailable"
    code: MiningErrorCode = MiningErrorCode.DEVICE_UNAVAILABLE
    retryable: bool = True  # may succeed after backoff or device switch
    action: str = "backoff"

    def __post_init__(self) -> None:
        self.context.setdefault("device", self.device)


# Helper: map arbitrary exceptions into MinerError (edge-safe)
def normalize_exc(exc: BaseException) -> MinerError:
    if isinstance(exc, MinerError):
        return exc
    # Fallback generic wrapper
    return MinerError(message=str(exc), code=MiningErrorCode.MINER_ERROR, retryable=False, context={"type": type(exc).__name__})


__all__ = [
    "MiningErrorCode",
    "SubmitRejectReason",
    "MinerError",
    "WorkExpired",
    "SubmitRejected",
    "DeviceUnavailable",
    "normalize_exc",
]
