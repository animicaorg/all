# -*- coding: utf-8 -*-
"""
contracts.stdlib.capabilities.da_blob
=====================================

Deterministic, contract-friendly helpers for **Data Availability** (DA) blob
pinning. These helpers wrap the low-level blob syscall, add strict input
validation, bounded storage-tag utilities, and structured events so indexers
can follow along without reading raw storage.

Typical flow
------------
- Pin once, keep the returned commitment:
    >>> from contracts.stdlib.capabilities import da_blob as da
    >>> commit = da.pin(ns=24, data=b"hello world")

- Or pin and save under a small tag (block N), then read later:
    >>> commit = da.pin_store(b"myblob", ns=24, data=b"...")
    >>> got = da.load_commitment(b"myblob"); assert got == commit
    >>> da.clear_commitment(b"myblob")  # optional cleanup

Determinism & bounds
--------------------
- Inputs are strictly typed and range-checked; violations revert with crisp
  reasons (see below). Length limits mirror the host/provider constraints.
- Tags are bounded to <= 32 bytes and kept under the namespaced key
  ``b"cap:da:" + tag``. We never emit raw blob bytes in events—only hashes.

Events (binary names; fields are bytes)
---------------------------------------
- b"CAP:DA:Pinned"     — {b"ns", b"size", b"data_hash", b"commitment"}
- b"CAP:DA:TagSaved"   — {b"tag", b"commitment"}
- b"CAP:DA:TagCleared" — {b"tag"}

Revert reasons (subset)
-----------------------
- b"CAP:TYPE"  — wrong Python type for an argument
- b"CAP:LEN"   — data length out of bounds (0 or > MAX_BLOB_LEN)
- b"CAP:NS"    — namespace id outside allowed range
- b"CAP:TAG"   — tag invalid (empty or longer than MAX_TAG_LEN)
- b"CAP:NOTAG" — tag had no stored commitment
"""
from __future__ import annotations

from typing import Final

from stdlib import abi, events
from stdlib import hash as _hash  # type: ignore
from stdlib import storage

# The package-local __init__.py exposes the validated low-level wrapper and
# canonical bounds for namespaces and sizes. These mirrors host/provider rules.
from . import MAX_BLOB_LEN  # maximum allowed blob length in bytes
from . import MAX_NAMESPACE  # inclusive upper bound for namespace id
from . import MIN_NAMESPACE  # inclusive lower bound for namespace id
from . import blob_pin  # (ns: int, data: bytes) -> bytes (commitment)

# -----------------------------------------------------------------------------
# Storage-tag utilities (bounded)
# -----------------------------------------------------------------------------

_TAG_NS_PREFIX: Final[bytes] = b"cap:da:"
MAX_TAG_LEN: Final[int] = 32


def _key_for(tag: bytes) -> bytes:
    return _TAG_NS_PREFIX + tag


# -----------------------------------------------------------------------------
# Guards (type & range checks with deterministic reverts)
# -----------------------------------------------------------------------------


def _ensure_bytes(x: object) -> bytes:
    if not isinstance(x, (bytes, bytearray)):
        abi.revert(b"CAP:TYPE")
    return bytes(x)


def _ensure_tag(tag: object) -> bytes:
    t = _ensure_bytes(tag)
    if len(t) == 0 or len(t) > MAX_TAG_LEN:
        abi.revert(b"CAP:TAG")
    return t


def _ensure_int(name: bytes, x: object) -> int:
    if not isinstance(x, int):
        abi.revert(b"CAP:TYPE")
    return x


def _ensure_ns(ns: object) -> int:
    n = _ensure_int(b"ns", ns)
    if n < int(MIN_NAMESPACE) or n > int(MAX_NAMESPACE):
        abi.revert(b"CAP:NS")
    return n


def _ensure_len(data: bytes) -> None:
    if len(data) == 0 or len(data) > int(MAX_BLOB_LEN):
        abi.revert(b"CAP:LEN")


# -----------------------------------------------------------------------------
# Public API
# -----------------------------------------------------------------------------


def pin(*, ns: int, data: bytes) -> bytes:
    """
    Pin a blob to DA under namespace ``ns`` and return its commitment (bytes).

    Parameters
    ----------
    ns : int
        Namespace identifier. Must satisfy MIN_NAMESPACE <= ns <= MAX_NAMESPACE.
    data : bytes
        Blob bytes, non-empty and length <= MAX_BLOB_LEN.

    Returns
    -------
    bytes
        The commitment (e.g., NMT root / commitment hash) as returned by the host.

    Emits
    -----
    b"CAP:DA:Pinned" with fields:
      - b"ns"         : 4-byte big-endian
      - b"size"       : 4-byte big-endian
      - b"data_hash"  : sha3-256(data)
      - b"commitment" : returned commitment bytes
    """
    n = _ensure_ns(ns)
    d = _ensure_bytes(data)
    _ensure_len(d)

    commitment = blob_pin(n, d)

    # Emit a compact, privacy-preserving event (no raw data)
    events.emit(
        b"CAP:DA:Pinned",
        {
            b"ns": n.to_bytes(4, "big", signed=False),
            b"size": len(d).to_bytes(4, "big", signed=False),
            b"data_hash": _hash.sha3_256(d),
            b"commitment": commitment,
        },
    )
    return commitment


def pin_store(tag: bytes, *, ns: int, data: bytes) -> bytes:
    """
    Pin a blob and store the commitment under a small **tag** for later use.

    This is helpful for two-step flows where the commitment is referenced
    across calls or blocks.

    Emits
    -----
    - b"CAP:DA:Pinned"     (from :func:`pin`)
    - b"CAP:DA:TagSaved"   — {b"tag", b"commitment"}
    """
    t = _ensure_tag(tag)
    commitment = pin(ns=ns, data=data)
    storage.set(_key_for(t), commitment)
    events.emit(b"CAP:DA:TagSaved", {b"tag": t, b"commitment": commitment})
    return commitment


def load_commitment(tag: bytes) -> bytes:
    """
    Load a stored commitment for ``tag`` without clearing it.

    Reverts
    -------
    - b"CAP:NOTAG" if no commitment is stored under the tag.
    """
    t = _ensure_tag(tag)
    val = storage.get(_key_for(t))
    if not isinstance(val, (bytes, bytearray)) or len(val) == 0:
        abi.revert(b"CAP:NOTAG")
    return bytes(val)


def clear_commitment(tag: bytes) -> None:
    """
    Clear a stored commitment for ``tag`` (idempotent).

    Emits
    -----
    - b"CAP:DA:TagCleared" — {b"tag"} if a value was present.
    """
    t = _ensure_tag(tag)
    k = _key_for(t)
    val = storage.get(k)
    if isinstance(val, (bytes, bytearray)) and len(val) > 0:
        storage.set(k, b"")
        events.emit(b"CAP:DA:TagCleared", {b"tag": t})


# -----------------------------------------------------------------------------
# Re-exports of bounds for callers/tests
# -----------------------------------------------------------------------------

__all__ = [
    "pin",
    "pin_store",
    "load_commitment",
    "clear_commitment",
    # bounds useful for preflight checks in higher-level contracts/tests
    "MAX_TAG_LEN",
    "MIN_NAMESPACE",
    "MAX_NAMESPACE",
    "MAX_BLOB_LEN",
]
