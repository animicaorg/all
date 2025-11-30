"""
Animica Capabilities — CBOR helpers

This subpackage exposes canonical (deterministic) CBOR encode/decode utilities
used by the capabilities schemas (job requests, receipts, result records, zk.verify).

Public API:
- dumps(obj) -> bytes  : canonical encoder (stable map ordering, shortest ints)
- loads(data: bytes) -> object : strict decoder

Aliases:
- encode = dumps
- decode = loads
"""

from .codec import dumps, loads

# Friendly aliases
encode = dumps
decode = loads

__all__ = ["dumps", "loads", "encode", "decode"]
