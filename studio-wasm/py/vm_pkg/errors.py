from __future__ import annotations

"""
Lightweight error types for the in-browser VM package.

These mirror a subset of the full `vm_py.errors` to keep the studio-wasm
distribution small and dependency-free while still providing structured
exceptions that higher layers (loader/simulator/UI) can catch and render.

Hierarchy
---------
VmError
 ├─ ValidationError  : static/semantic validation failures (manifest/IR/etc.)
 ├─ OOG              : out-of-gas during execution (includes optional counters)
 └─ Revert           : contract-triggered revert with optional message/data
"""

from typing import Optional


class VmError(Exception):
    """Base class for VM-related errors."""

    code: str = "VmError"

    def __init__(self, message: str = "") -> None:
        super().__init__(message or self.__class__.__name__)
        self.message = message or self.__class__.__name__

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.message


class ValidationError(VmError):
    """Configuration/schema/IR validation failure (non-runtime)."""

    code: str = "ValidationError"


class OOG(VmError):
    """Out-of-gas: execution exceeded the allowed gas limit."""

    code: str = "OOG"

    def __init__(
        self,
        message: str = "out of gas",
        *,
        gas_limit: Optional[int] = None,
        gas_used: Optional[int] = None,
    ) -> None:
        if gas_limit is not None or gas_used is not None:
            details = f" (used={gas_used}, limit={gas_limit})"
        else:
            details = ""
        super().__init__(f"{message}{details}")
        self.gas_limit = gas_limit
        self.gas_used = gas_used


class Revert(VmError):
    """
    Contract-triggered revert.

    `reason` is a human-readable explanation (if provided by the contract).
    `data` can hold an encoded payload for higher-level decoders.
    """

    code: str = "Revert"

    def __init__(
        self,
        reason: Optional[str] = None,
        *,
        data: Optional[bytes] = None,
    ) -> None:
        msg = "revert" if not reason else f"revert: {reason}"
        super().__init__(msg)
        self.reason = reason
        self.data = data


__all__ = [
    "VmError",
    "ValidationError",
    "OOG",
    "Revert",
]
