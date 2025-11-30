"""
core.encoding
=============

Public, stable encoding surface for the Animica core:

- Canonical CBOR (matching spec/*.cddl)
- "SignBytes" domain encoders for transactions and headers
- Convenience helpers for hashing CBOR-encoded objects

Modules
-------
- cbor.py:   canonical CBOR dumps/loads (sorted map keys, deterministic)
- canonical.py: domain-separated SignBytes for cryptographic signing

This package intentionally keeps a *very* small public API to avoid accidental
divergence from the specification.
"""

from __future__ import annotations

from typing import Any

# Hash helpers over canonical CBOR encodings
from ..utils.hash import sha3_256, sha3_512
# Domain-separated SignBytes (used for PQ signing / verification)
from .canonical import signbytes_header, signbytes_tx
# Canonical CBOR API
from .cbor import dumps as cbor_dumps
from .cbor import loads as cbor_loads


def cbor_sha3_256(obj: Any) -> bytes:
    """
    sha3_256(cbor_dumps(obj)) — primary content-addressing helper for small structs.
    """
    return sha3_256(cbor_dumps(obj))


def cbor_sha3_512(obj: Any) -> bytes:
    """
    sha3_512(cbor_dumps(obj)) — stronger digest where longer domain separation is desired.
    """
    return sha3_512(cbor_dumps(obj))


__all__ = [
    # CBOR
    "cbor_dumps",
    "cbor_loads",
    # SignBytes
    "signbytes_tx",
    "signbytes_header",
    # Hash conveniences
    "cbor_sha3_256",
    "cbor_sha3_512",
]
