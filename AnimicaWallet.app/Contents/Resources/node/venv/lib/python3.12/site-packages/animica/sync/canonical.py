from __future__ import annotations

import hashlib
from typing import Any

from core.encoding.cbor import dumps as cbor_dumps


def canonical_bytes(obj: Any) -> bytes:
    if isinstance(obj, bytes):
        return obj
    if hasattr(obj, "to_cbor"):
        return obj.to_cbor()
    if hasattr(obj, "to_obj"):
        return cbor_dumps(obj.to_obj())
    if isinstance(obj, dict):
        return cbor_dumps(obj)
    raise TypeError(f"unsupported canonical object: {type(obj)!r}")


def header_bytes(header: Any) -> bytes:
    return canonical_bytes(header)


def block_bytes(block: Any) -> bytes:
    return canonical_bytes(block)


def hash_header(header: Any) -> bytes:
    return hashlib.sha256(header_bytes(header)).digest()


def hash_block(block: Any) -> bytes:
    return hashlib.sha256(block_bytes(block)).digest()
