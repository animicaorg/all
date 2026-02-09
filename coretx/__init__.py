"""
coretx - Core Transaction System
==================================

This package provides the canonical transaction model, encoding, signing,
and verification for the Animica blockchain.

Key components:
- types: Core transaction data structures (TxBody, TxAuth, TxEnvelope, TxId)
- canonical: Deterministic encoding and hashing
- crypto: PQ cryptography registry
- signing: Transaction signing and verification
- errors: Stable error types for transaction rejection
"""

__version__ = "1.0.0"

from .types import TxBody, TxAuth, TxEnvelope, TxId, TxKind
from .errors import RejectReason, TxReject, VerifyResult
from .signing import sign_tx, verify_tx

__all__ = [
    "TxBody",
    "TxAuth",
    "TxEnvelope",
    "TxId",
    "TxKind",
    "RejectReason",
    "TxReject",
    "VerifyResult",
    "sign_tx",
    "verify_tx",
]
