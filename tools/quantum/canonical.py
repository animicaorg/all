"""Canonical byte helpers for signing/verifying in development.

This provides a deterministic canonical_bytes(obj) function that serializes JSON
with stable ordering (sort_keys=True) and minimal separators. This is not CBOR,
but is deterministic and suitable for early development and for the HMAC-based
mock signer. Replace with canonical CBOR for production.
"""
from __future__ import annotations

import json
from typing import Any


def canonical_bytes(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
