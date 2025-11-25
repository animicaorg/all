"""
execution.errors — execution-layer exceptions for the Animica node.

The execution engine communicates failures via *typed exceptions* that are converted
into receipts and structured error payloads at higher layers. These exceptions are
pure-Python, dependency-free, and deliberately small.

Hierarchy
---------
ExecError (base)
 ├─ OOG             : Out-of-gas during execution
 ├─ Revert          : Contract-triggered revert (explicit failure, may carry return data)
 ├─ InvalidAccess   : Illegal state/code access per rules or capabilities
 └─ StateConflict   : Write/write or read/write conflict detected by the scheduler

Notes
-----
* Raising `Revert` and `OOG` is considered a *semantic* failure of the transaction,
  not a node bug; these map to deterministic receipt statuses.
* `InvalidAccess` indicates a rules violation (e.g., forbidden syscalls in strict mode,
  disallowed storage access, or size/limit breaches).
* `StateConflict` is used by optimistic schedulers to signal that a speculative run
  must be re-executed serially; it does not imply the transaction is invalid.

These classes intentionally avoid importing types from other packages so they can be
used from low-level modules (VM runtime, gas meter, scheduler) without cycles.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class ExecError(Exception):
    """
    Base execution error.

    Attributes:
        message: Human-readable explanation.
        code:    Stable machine code string (e.g., 'OUT_OF_GAS', 'REVERT').
        data:    Optional structured details (kept JSON-serializable).
    """
    message: str = "execution error"
    code: str = "EXEC_ERROR"
    data: Optional[Dict[str, Any]] = field(default=None)

    def __str__(self) -> str:  # pragma: no cover - trivial
        if self.data:
            return f"{self.code}: {self.message} ({self.data})"
        return f"{self.code}: {self.message}"

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to a JSON-safe dict for receipts/logs/RPC errors."""
        out: Dict[str, Any] = {"code": self.code, "message": self.message}
        if self.data is not None:
            out["data"] = self.data
        return out


class OOG(ExecError):
    """
    Out-of-gas during execution.

    Typical triggers:
      - GasMeter debit would underflow
      - Static gas exceeds provided limit (intrinsic + call)
    """
    def __init__(self, message: str = "out of gas", *, data: Optional[Dict[str, Any]] = None):
        super().__init__(message=message, code="OUT_OF_GAS", data=data)


class Revert(ExecError):
    """
    Contract-triggered revert.

    Optional fields:
        reason:       UTF-8 string, if contract provided a textual message.
        return_data:  Raw bytes (hex string recommended at call sites) carrying ABI-encoded
                      error details; preserved for developer tooling.

    Usage:
        raise Revert("require failed", data={"reason": "insufficient balance"})
    """
    def __init__(
        self,
        message: str = "reverted",
        *,
        reason: Optional[str] = None,
        return_data: Optional[bytes] = None,
        data: Optional[Dict[str, Any]] = None,
    ):
        d: Dict[str, Any] = {}
        if data:
            d.update(data)
        if reason is not None:
            d.setdefault("reason", reason)
        if return_data is not None:
            # Defer encoding choice to caller; here we store hex for JSON friendliness.
            d.setdefault("return_data", return_data.hex())
        super().__init__(message=message, code="REVERT", data=d or None)


class InvalidAccess(ExecError):
    """
    Illegal access or forbidden operation under deterministic rules.

    Examples:
      - Disallowed syscall in strict mode
      - Storage/code size limits exceeded
      - Cross-account access not permitted by context/access list
    """
    def __init__(
        self,
        message: str = "invalid access",
        *,
        op: Optional[str] = None,
        address: Optional[str] = None,
        slot: Optional[str] = None,
        data: Optional[Dict[str, Any]] = None,
    ):
        d: Dict[str, Any] = {}
        if data:
            d.update(data)
        if op is not None:
            d.setdefault("op", op)
        if address is not None:
            d.setdefault("address", address)
        if slot is not None:
            d.setdefault("slot", slot)
        super().__init__(message=message, code="INVALID_ACCESS", data=d or None)


class StateConflict(ExecError):
    """
    Speculative execution conflict detected by the scheduler.

    Indicates that observed read/write sets conflict with another concurrent execution,
    requiring a rerun (usually serially) to preserve determinism.
    """
    def __init__(
        self,
        message: str = "state conflict",
        *,
        tx_hash: Optional[str] = None,
        conflicting_with: Optional[str] = None,
        data: Optional[Dict[str, Any]] = None,
    ):
        d: Dict[str, Any] = {}
        if data:
            d.update(data)
        if tx_hash is not None:
            d.setdefault("tx_hash", tx_hash)
        if conflicting_with is not None:
            d.setdefault("conflicting_with", conflicting_with)
        super().__init__(message=message, code="STATE_CONFLICT", data=d or None)


# -------- helper utilities (optional) ---------------------------------------


def error_to_receipt_fields(err: ExecError) -> Dict[str, Any]:
    """
    Map an ExecError to canonical receipt-like fields.
    This avoids importing execution.types.* to keep this module light.

    Returns:
        {
          "status": "OOG" | "REVERT" | "ERROR",
          "error":  {code, message, data?}
        }
    """
    if isinstance(err, OOG):
        status = "OOG"
    elif isinstance(err, Revert):
        status = "REVERT"
    else:
        status = "ERROR"
    return {"status": status, "error": err.to_dict()}


__all__ = [
    "ExecError",
    "OOG",
    "Revert",
    "InvalidAccess",
    "StateConflict",
    "error_to_receipt_fields",
]
