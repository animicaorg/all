from __future__ import annotations

"""
stdlib.treasury — inert treasury helpers for the browser simulator.

Purpose
-------
Contracts may import a simple treasury surface:

    from stdlib import treasury
    bal = treasury.balance()
    treasury.transfer(to_addr, 100)

In the in-browser simulator, *no real balances are tracked*. This module
validates inputs and gracefully no-ops so example contracts run without a
chain treasury. If the runtime provides a `treasury_api` module (when the
simulator is wired to a richer host), we will delegate to it.

API
---
- balance() -> int
    Returns the current contract balance if the runtime supports it; otherwise 0.

- transfer(to: bytes, amount: int) -> None
    Validates inputs and:
      * delegates to runtime.treasury_api.transfer if available, or
      * no-ops (does nothing) in the basic simulator.
"""

from typing import Optional

from ..errors import ValidationError

# Optional delegation to a runtime implementation if present.
try:
    from ..runtime import treasury_api as _treasury_api  # type: ignore
except Exception:  # pragma: no cover - absence is fine in basic sim
    _treasury_api = None  # type: ignore[assignment]

_U256_MAX = (1 << 256) - 1
_MAX_ADDR_LEN = 64  # conservative bound for simulator


def _ensure_bytes(name: str, v) -> bytes:
    if not isinstance(v, (bytes, bytearray)):
        raise ValidationError(f"{name} must be bytes")
    b = bytes(v)
    if not b:
        raise ValidationError(f"{name} must be non-empty")
    if len(b) > _MAX_ADDR_LEN:
        raise ValidationError(f"{name} too long")
    return b


def _ensure_amount(n) -> int:
    if not isinstance(n, int) or n < 0:
        raise ValidationError("amount must be a non-negative int")
    if n > _U256_MAX:
        raise ValidationError("amount exceeds u256")
    return n


def balance() -> int:
    """
    Return the contract balance if supported by the runtime; 0 otherwise.
    """
    if _treasury_api is not None and hasattr(_treasury_api, "balance"):
        try:
            val = _treasury_api.balance()  # type: ignore[attr-defined]
            if not isinstance(val, int) or val < 0:
                # Be strict: sanitize unexpected host responses.
                return 0
            return val
        except Exception:
            return 0
    return 0


def transfer(to: bytes | bytearray, amount: int) -> None:
    """
    Transfer `amount` units to `to`. In the basic simulator this is a no-op
    after validation. If a runtime treasury is available, delegation occurs.
    """
    to_b = _ensure_bytes("to", to)
    amt = _ensure_amount(amount)

    if _treasury_api is not None and hasattr(_treasury_api, "transfer"):
        # Delegate to host implementation; let it raise on failure.
        _treasury_api.transfer(to_b, amt)  # type: ignore[attr-defined]
        return
    # Inert no-op in the basic simulator.


__all__ = ["balance", "transfer"]
