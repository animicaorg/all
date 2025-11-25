from __future__ import annotations
# aicf/errors.py
"""
Error types for the AI Compute Fund (AICF) and a small event record used when a
provider is slashed. These are lightweight, serializable, and safe to surface
over RPC/logs.

Exports:
- AICFError (base)
- RegistryError
- InsufficientStake
- JobExpired
- LeaseLost
- SlashReason (enum)
- SlashEvent (dataclass)
"""


from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Mapping, Optional
import json


class AICFError(Exception):
    """Base class for AICF domain errors."""

    code: str = "AICF_ERROR"

    def __init__(self, message: str = "", *, details: Optional[Mapping[str, Any]] = None) -> None:
        self.message = message or self.__class__.__name__
        self.details = dict(details or {})
        super().__init__(self.__str__())

    def to_dict(self) -> Dict[str, Any]:
        return {"code": self.code, "message": self.message, "details": self.details}

    def __str__(self) -> str:  # pragma: no cover - trivial
        if self.details:
            # Keep this compact and stable for logs
            try:
                packed = json.dumps(self.details, sort_keys=True, separators=(",", ":"))
            except Exception:
                packed = str(self.details)
            return f"{self.code}: {self.message} [{packed}]"
        return f"{self.code}: {self.message}"


class RegistryError(AICFError):
    """
    Provider registry-related failures: missing provider, bad status, duplicate,
    signature mismatch, etc.
    """
    code = "AICF_REGISTRY_ERROR"

    def __init__(
        self,
        message: str = "registry operation failed",
        *,
        provider_id: Optional[str] = None,
        details: Optional[Mapping[str, Any]] = None,
    ) -> None:
        d = dict(details or {})
        if provider_id is not None:
            d.setdefault("provider_id", provider_id)
        super().__init__(message, details=d)


class InsufficientStake(AICFError):
    """Provider attempted an action without the minimum required stake."""
    code = "AICF_INSUFFICIENT_STAKE"

    def __init__(
        self,
        *,
        required_nano: int,
        actual_nano: int,
        provider_id: Optional[str] = None,
        message: str = "insufficient stake",
        details: Optional[Mapping[str, Any]] = None,
    ) -> None:
        d = dict(details or {})
        d.update({"required_nano": int(required_nano), "actual_nano": int(actual_nano)})
        if provider_id is not None:
            d.setdefault("provider_id", provider_id)
        super().__init__(message, details=d)


class JobExpired(AICFError):
    """A job cannot be processed because its lease/TTL or expiry height passed."""
    code = "AICF_JOB_EXPIRED"

    def __init__(
        self,
        *,
        job_id: str,
        submitted_height: Optional[int] = None,
        expiry_height: Optional[int] = None,
        message: str = "job expired",
        details: Optional[Mapping[str, Any]] = None,
    ) -> None:
        d = dict(details or {})
        d.update({"job_id": job_id})
        if submitted_height is not None:
            d["submitted_height"] = int(submitted_height)
        if expiry_height is not None:
            d["expiry_height"] = int(expiry_height)
        super().__init__(message, details=d)


class LeaseLost(AICFError):
    """
    A worker lost its assignment lease on a job (e.g., heartbeat lapsed or the
    coordinator reassigned it). Retrying or re-acquiring a lease is required.
    """
    code = "AICF_LEASE_LOST"

    def __init__(
        self,
        *,
        job_id: str,
        previous_holder: Optional[str] = None,
        reason: Optional[str] = None,
        message: str = "assignment lease lost",
        details: Optional[Mapping[str, Any]] = None,
    ) -> None:
        d = dict(details or {})
        d.update({"job_id": job_id})
        if previous_holder is not None:
            d["previous_holder"] = previous_holder
        if reason is not None:
            d["reason"] = reason
        super().__init__(message, details=d)


class SlashReason(Enum):
    """Enumerates canonical slashing reasons for providers."""
    TRAPS_FAIL = "traps_fail"           # failed quantum/AI trap-circuit checks
    QOS_FAIL = "qos_fail"               # missed QoS/SLA thresholds
    AVAILABILITY_FAIL = "availability_fail"  # insufficient uptime/heartbeats
    MISBEHAVIOR = "misbehavior"         # explicit double-signing, fraud, etc.
    OTHER = "other"


@dataclass(frozen=True)
class SlashEvent:
    """
    Immutable record describing a slashing action applied to a provider.

    Fields:
      - provider_id: canonical provider identifier (e.g., bech32m).
      - reason: SlashReason.
      - bps: penalty magnitude in basis points (0..10000).
      - amount_nano: concrete amount slashed (nano-tokens).
      - jail_blocks: optional jail duration (blocks).
      - evidence_hash: optional hex-encoded hash of the evidence bundle.
      - at_height: optional block height when slash was effected.
    """
    provider_id: str
    reason: SlashReason
    bps: int
    amount_nano: int
    jail_blocks: int = 0
    evidence_hash: Optional[str] = None
    at_height: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "reason": self.reason.value,
            "bps": int(self.bps),
            "amount_nano": int(self.amount_nano),
            "jail_blocks": int(self.jail_blocks),
            "evidence_hash": self.evidence_hash,
            "at_height": self.at_height if self.at_height is not None else None,
        }


__all__ = [
    "AICFError",
    "RegistryError",
    "InsufficientStake",
    "JobExpired",
    "LeaseLost",
    "SlashReason",
    "SlashEvent",
]
