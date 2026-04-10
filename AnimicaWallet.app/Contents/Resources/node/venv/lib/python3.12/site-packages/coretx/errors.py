"""
coretx.errors - Transaction Error Types
========================================

Stable, typed error model for transaction validation and rejection.
Every rejection has a precise reason with deterministic context.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

__all__ = [
    "RejectReason",
    "TxReject",
    "VerifyResult",
    "REJECT_CODE",
]


class RejectReason(str, Enum):
    """Enumeration of all possible transaction rejection reasons"""
    # Signature validation failures
    invalid_signature = "invalid_signature"
    invalid_pubkey = "invalid_pubkey"
    scheme_unsupported = "scheme_unsupported"
    
    # Format/encoding failures
    invalid_format = "invalid_format"
    invalid_field = "invalid_field"
    malformed_envelope = "malformed_envelope"
    
    # Chain validation failures
    chain_id_mismatch = "chain_id_mismatch"
    
    # State/account validation failures
    insufficient_funds = "insufficient_funds"
    nonce_too_low = "nonce_too_low"
    nonce_too_high = "nonce_too_high"
    nonce_gap = "nonce_gap"
    nonce_conflict = "nonce_conflict"
    
    # Fee/gas validation failures
    fee_too_low = "fee_too_low"
    gas_limit_exceeded = "gas_limit_exceeded"
    
    # Policy/spam protection failures
    tx_already_known = "tx_already_known"
    tx_oversize = "tx_oversize"
    rate_limited = "rate_limited"
    policy_reject = "policy_reject"
    
    # Internal/system failures
    internal_error = "internal_error"


# Stable error codes for RPC responses
REJECT_CODE: dict[RejectReason, int] = {
    # Signature failures: 2001-2099
    RejectReason.invalid_signature: 2001,
    RejectReason.invalid_pubkey: 2002,
    RejectReason.scheme_unsupported: 2003,
    
    # Format failures: 2100-2199
    RejectReason.invalid_format: 2100,
    RejectReason.invalid_field: 2101,
    RejectReason.malformed_envelope: 2102,
    
    # Chain failures: 2200-2299
    RejectReason.chain_id_mismatch: 2200,
    
    # State failures: 2300-2399
    RejectReason.insufficient_funds: 2300,
    RejectReason.nonce_too_low: 2301,
    RejectReason.nonce_too_high: 2302,
    RejectReason.nonce_gap: 2303,
    RejectReason.nonce_conflict: 2304,
    
    # Fee/gas failures: 2400-2499
    RejectReason.fee_too_low: 2400,
    RejectReason.gas_limit_exceeded: 2401,
    
    # Policy failures: 2500-2599
    RejectReason.tx_already_known: 2500,
    RejectReason.tx_oversize: 2501,
    RejectReason.rate_limited: 2502,
    RejectReason.policy_reject: 2503,
    
    # Internal failures: 2999
    RejectReason.internal_error: 2999,
}


@dataclass(frozen=True)
class TxReject:
    """
    Structured transaction rejection with stable error codes and context.
    
    This is the canonical error type returned by all tx validation/admission functions.
    It provides:
    - Machine-readable reason (enum)
    - Stable integer code for RPC
    - Human-readable message
    - Actionable hint for resolution
    - Safe context dict for debugging
    - Optional error_class for internal errors
    """
    reason: RejectReason
    code: int
    message: str
    hint: str
    context: dict[str, Any] = field(default_factory=dict)
    error_class: Optional[str] = None
    
    def to_dict(self) -> dict[str, Any]:
        """Convert to JSON-serializable dict for RPC responses"""
        payload: dict[str, Any] = {
            "reason": self.reason.value,
            "code": int(self.code),
            "message": self.message,
            "hint": self.hint,
            "context": dict(self.context),
        }
        if self.error_class:
            payload["error_class"] = self.error_class
            # Also include in context for consistency
            payload["context"].setdefault("error_class", self.error_class)
        return payload
    
    def __str__(self) -> str:
        """Human-readable error message"""
        parts = [f"[{self.reason.value}]", self.message]
        if self.hint:
            parts.append(f"Hint: {self.hint}")
        if self.context:
            # Include key context in string repr
            ctx_parts = []
            for k, v in self.context.items():
                if k == "error_class":
                    continue  # Already shown separately
                ctx_parts.append(f"{k}={v}")
            if ctx_parts:
                parts.append(f"({', '.join(ctx_parts)})")
        return " ".join(parts)


def reject(
    reason: RejectReason,
    *,
    message: str,
    hint: str = "",
    context: Optional[dict[str, Any]] = None,
    error_class: Optional[str] = None,
) -> TxReject:
    """
    Factory function to create a TxReject with automatic code lookup.
    
    Usage:
        reject(RejectReason.invalid_signature,
               message="PQ signature verification failed",
               hint="Check that the signing key matches the from_addr",
               context={"txid": "0x..."})
    """
    return TxReject(
        reason=reason,
        code=REJECT_CODE[reason],
        message=message,
        hint=hint or "",
        context=context or {},
        error_class=error_class,
    )


@dataclass(frozen=True)
class VerifyResult:
    """
    Result of cryptographic signature verification.
    
    Success case: ok=True
    Failure case: ok=False, reason and diagnostics populated
    """
    ok: bool
    reason: Optional[str] = None
    diagnostics: dict[str, Any] = field(default_factory=dict)
    
    @classmethod
    def success(cls) -> VerifyResult:
        """Create a success result"""
        return cls(ok=True)
    
    @classmethod
    def failure(cls, reason: str, **diagnostics: Any) -> VerifyResult:
        """Create a failure result with diagnostic context"""
        return cls(ok=False, reason=reason, diagnostics=diagnostics)
    
    def __bool__(self) -> bool:
        """Allow `if verify_result:` checks"""
        return self.ok
