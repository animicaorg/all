"""
mempool.errors
--------------

Typed exceptions for mempool admission and related flows. These are designed
to be:
- Richly structured (carry machine-parsable context via `.to_dict()`).
- JSON-RPC friendly (stable integer `code` values).
- Easy to log (clean __str__ plus a compact `reason`).

Hierarchy:

    MempoolError (base)
    ├── AdmissionError
    │   ├── FeeTooLow
    │   ├── NonceGap
    │   └── Oversize
    ├── ReplacementError
    └── DoSError

Notes
-----
* Keep error *reasons* short and stable for metrics.
* Avoid including sensitive material (e.g., raw tx contents) in context.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional

__all__ = [
    "MempoolError",
    "AdmissionError",
    "ReplacementError",
    "DoSError",
    "FeeTooLow",
    "NonceGap",
    "Oversize",
    "MempoolErrorCode",
]


class MempoolErrorCode:
    """
    Stable numeric error codes reserved for mempool errors.

    Range 1000–1199 is reserved for mempool.
    """

    ADMISSION = 1000
    FEE_TOO_LOW = 1001
    NONCE_GAP = 1002
    OVERSIZE = 1003
    REPLACEMENT = 1004
    DOS = 1099


@dataclass(eq=False)
class MempoolError(Exception):
    """
    Base class for mempool errors.

    Attributes
    ----------
    code : int
        Stable integer code (see MempoolErrorCode).
    reason : str
        Short, machine-friendly reason (snake_case).
    message : str
        Human-readable message.
    context : Dict[str, Any]
        Structured details safe for logs/telemetry and JSON-RPC data payloads.
    """

    code: int
    reason: str
    message: str
    context: Dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        ctx = ""
        if self.context:
            # Keep context compact in str(); full detail available via to_dict()
            parts = []
            for k, v in self.context.items():
                if v is None:
                    continue
                s = str(v)
                if len(s) > 64:
                    s = s[:61] + "..."
                parts.append(f"{k}={s}")
            if parts:
                ctx = " [" + ", ".join(parts) + "]"
        return f"{self.reason}: {self.message}{ctx}"

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-serializable error object."""
        return {
            "code": self.code,
            "reason": self.reason,
            "message": self.message,
            "context": dict(self.context) if self.context else {},
        }


@dataclass(eq=False)
class AdmissionError(MempoolError):
    """
    Parent for all admission failures (policy/consistency).
    """

    def __init__(
        self, message: str, *, context: Optional[Dict[str, Any]] = None
    ) -> None:
        super().__init__(
            code=MempoolErrorCode.ADMISSION,
            reason="admission_failed",
            message=message,
            context=context or {},
        )


@dataclass(eq=False)
class FeeTooLow(AdmissionError):
    """
    The transaction's offered gas price is below the current minimum admission threshold.
    """

    def __init__(
        self,
        *,
        offered_gas_price_wei: int,
        min_required_wei: int,
        tx_hash: Optional[str] = None,
        sender: Optional[str] = None,
    ) -> None:
        gwei = lambda w: float(w) / 1_000_000_000
        msg = (
            f"gas price too low: offered {gwei(offered_gas_price_wei):.9g} gwei "
            f"< required {gwei(min_required_wei):.9g} gwei"
        )
        super(MempoolError, self).__init__(  # type: ignore[misc]
            code=MempoolErrorCode.FEE_TOO_LOW,
            reason="fee_too_low",
            message=msg,
            context={
                "tx_hash": tx_hash,
                "sender": sender,
                "offered_gas_price_wei": offered_gas_price_wei,
                "min_required_wei": min_required_wei,
            },
        )


@dataclass(eq=False)
class NonceGap(AdmissionError):
    """
    The transaction nonce is not the next expected nonce for the sender.

    Typical handling is to classify as an orphan (short TTL) until the gap is filled.
    """

    def __init__(
        self,
        *,
        expected_nonce: int,
        got_nonce: int,
        sender: Optional[str] = None,
        tx_hash: Optional[str] = None,
    ) -> None:
        msg = f"nonce gap: expected {expected_nonce}, got {got_nonce}"
        super(MempoolError, self).__init__(  # type: ignore[misc]
            code=MempoolErrorCode.NONCE_GAP,
            reason="nonce_gap",
            message=msg,
            context={
                "sender": sender,
                "tx_hash": tx_hash,
                "expected_nonce": expected_nonce,
                "got_nonce": got_nonce,
            },
        )


@dataclass(eq=False)
class Oversize(AdmissionError):
    """
    The transaction is larger than the configured maximum size.
    """

    def __init__(
        self,
        *,
        size_bytes: int,
        max_bytes: int,
        tx_hash: Optional[str] = None,
        sender: Optional[str] = None,
    ) -> None:
        msg = f"transaction too large: {size_bytes} bytes > limit {max_bytes} bytes"
        super(MempoolError, self).__init__(  # type: ignore[misc]
            code=MempoolErrorCode.OVERSIZE,
            reason="tx_too_large",
            message=msg,
            context={
                "tx_hash": tx_hash,
                "sender": sender,
                "size_bytes": size_bytes,
                "max_bytes": max_bytes,
            },
        )


@dataclass(eq=False)
class ReplacementError(MempoolError):
    """
    Replacement (same sender & nonce) rejected.

    Common causes:
      - Offered price bump below required factor.
      - Attempt to replace with equal or lower effective gas price.

    Fields:
      required_bump (float): e.g., 1.10 means +10% required.
      current_effective_gas_price_wei: price of the tx currently held.
      offered_effective_gas_price_wei: price of the replacement tx.
    """

    def __init__(
        self,
        *,
        required_bump: float,
        current_effective_gas_price_wei: int,
        offered_effective_gas_price_wei: int,
        tx_hash_new: Optional[str] = None,
        tx_hash_old: Optional[str] = None,
        sender: Optional[str] = None,
    ) -> None:
        bump_pct = (required_bump - 1.0) * 100.0
        msg = (
            f"replacement underpriced: offered bump "
            f"{offered_effective_gas_price_wei}/{current_effective_gas_price_wei} "
            f"< required {required_bump:.3f}x (+{bump_pct:.1f}%)"
        )
        super().__init__(
            code=MempoolErrorCode.REPLACEMENT,
            reason="replacement_underpriced",
            message=msg,
            context={
                "sender": sender,
                "tx_hash_old": tx_hash_old,
                "tx_hash_new": tx_hash_new,
                "required_bump": required_bump,
                "current_effective_gas_price_wei": current_effective_gas_price_wei,
                "offered_effective_gas_price_wei": offered_effective_gas_price_wei,
            },
        )


@dataclass(eq=False)
class DoSError(MempoolError):
    """
    Generic DoS/abuse related rejection (rate limit, quota exceeded, malformed bursts, etc.).
    """

    def __init__(
        self,
        message: str,
        *,
        peer_id: Optional[str] = None,
        remote_addr: Optional[str] = None,
        reason: str = "dos_violation",
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        ctx: Dict[str, Any] = {"peer_id": peer_id, "remote_addr": remote_addr}
        if extra:
            ctx.update(extra)
        super().__init__(
            code=MempoolErrorCode.DOS,
            reason=reason,
            message=message,
            context=ctx,
        )


# ---- Utilities --------------------------------------------------------------


def err_payload(exc: MempoolError) -> Dict[str, Any]:
    """
    Return a compact JSON-serializable payload suitable for RPC error `data`.
    """
    return exc.to_dict()


# ---------------------------------------------------------------------------
# Override Oversize with a simpler, explicit implementation
# ---------------------------------------------------------------------------


class Oversize(MempoolError):
    """
    Simple size-limit error used by AdmissionPolicy.

    This class overrides any earlier Oversize definition in this module and
    provides a direct constructor that sets code/reason/message/context in a
    stable, JSON/RPC-friendly way.
    """

    def __init__(
        self,
        *,
        size_bytes: int,
        max_bytes: int,
        tx_hash: Optional[str] = None,
        sender: Optional[str] = None,
    ) -> None:
        msg = f"transaction too large: {size_bytes} bytes > limit {max_bytes} bytes"
        super().__init__(
            code=MempoolErrorCode.OVERSIZE,
            reason="tx_too_large",
            message=msg,
            context={
                "tx_hash": tx_hash,
                "sender": sender,
                "size_bytes": size_bytes,
                "max_bytes": max_bytes,
            },
        )


# ---------------------------------------------------------------------------
# Override FeeTooLow with a simpler, explicit implementation
# ---------------------------------------------------------------------------


class FeeTooLow(MempoolError):
    """
    Simple fee-floor error used by AdmissionPolicy.

    This class overrides any earlier FeeTooLow definition in this module and
    provides a direct constructor that sets code/reason/message/context in a
    stable, JSON/RPC-friendly way.
    """

    def __init__(
        self,
        *,
        offered_gas_price_wei: int,
        min_required_wei: int,
        tx_hash: Optional[str] = None,
        sender: Optional[str] = None,
    ) -> None:
        gwei = lambda w: float(w) / 1_000_000_000
        msg = (
            f"gas price too low: offered {gwei(offered_gas_price_wei):.9g} gwei "
            f"< required {gwei(min_required_wei):.9g} gwei"
        )
        super().__init__(
            code=MempoolErrorCode.FEE_TOO_LOW,
            reason="fee_too_low",
            message=msg,
            context={
                "tx_hash": tx_hash,
                "sender": sender,
                "offered_gas_price_wei": offered_gas_price_wei,
                "min_required_wei": min_required_wei,
            },
        )


# ---------------------------------------------------------------------------
# Override NonceGap with a simpler, explicit implementation
# ---------------------------------------------------------------------------


class NonceGap(MempoolError):
    """
    The transaction nonce is not the next expected nonce for the sender.

    This override uses a direct constructor that accepts keyword arguments and
    fills a stable JSON-serializable context.
    """

    def __init__(
        self,
        *,
        expected_nonce: int,
        got_nonce: int,
        sender: Optional[str] = None,
        tx_hash: Optional[str] = None,
    ) -> None:
        msg = f"nonce gap: expected {expected_nonce}, got {got_nonce}"
        super().__init__(
            code=MempoolErrorCode.NONCE_GAP,
            reason="nonce_gap",
            message=msg,
            context={
                "sender": sender,
                "tx_hash": tx_hash,
                "expected_nonce": expected_nonce,
                "got_nonce": got_nonce,
            },
        )
