"""
execution.receipts — helpers for building, encoding, and hashing receipt data.

This package centralizes:
  • builder:    ApplyResult → Receipt construction
  • encoding:   Deterministic CBOR encode/decode for receipts
  • logs_hash:  Logs bloom and logs Merkle/root helpers

Import convenience
------------------
Common helpers are re-exported here for ergonomic imports:

    from execution.receipts import build_receipt
    from execution.receipts import receipt_to_cbor, receipt_from_cbor
    from execution.receipts import compute_logs_bloom, compute_logs_root
"""

from __future__ import annotations

# Always expose submodules
from . import builder as builder
from . import encoding as encoding
from . import logs_hash as logs_hash

# Best-effort function re-exports (don’t hard-fail if names change downstream)
try:
    from .builder import build_receipt  # type: ignore[F401]
except Exception:  # pragma: no cover
    pass

# Support either naming style in encoding module.
try:
    from .encoding import receipt_to_cbor, receipt_from_cbor  # type: ignore[F401]
except Exception:  # pragma: no cover
    # Fallback to alternative names if the module exposes encode_/decode_ variants.
    try:
        from .encoding import encode_receipt as receipt_to_cbor  # type: ignore[F401]
        from .encoding import decode_receipt as receipt_from_cbor  # type: ignore[F401]
    except Exception:
        pass

try:
    from .logs_hash import compute_logs_bloom, compute_logs_root  # type: ignore[F401]
except Exception:  # pragma: no cover
    pass


# Construct __all__ dynamically based on what resolved above.
__all__ = [
    "builder",
    "encoding",
    "logs_hash",
]

for _name in (
    "build_receipt",
    "receipt_to_cbor",
    "receipt_from_cbor",
    "compute_logs_bloom",
    "compute_logs_root",
):
    if _name in globals():
        __all__.append(_name)
