from __future__ import annotations

"""
Canonical state-root calculation
================================

This module computes the canonical state root from an abstract key/value view.

Design goals
------------
- **Deterministic ordering**: keys are sorted by raw byte order (lexicographic).
- **Ambiguity-free leaves**: each leaf hash binds `(key, value)` with explicit length
  prefixes and a domain tag.
- **Stable internal nodes**: binary Merkle tree with duplication of the last hash
  on odd levels (Bitcoin-style) to achieve power-of-two invariance.
- **Hash**: SHA3-256 (32-byte digest) with domain separation for leaves/nodes/empty.

Hash domains
------------
- LEAF:  b"animica/state/leaf:v1"
- NODE:  b"animica/state/node:v1"
- EMPTY: b"animica/state/empty:v1"

API
---
- `compute_state_root(kv)`:
    Accepts:
      * an iterable of `(key: bytes, value: bytes)` tuples
      * a `Mapping[bytes, bytes]` (uses `.items()`)
      * an object exposing `.iter_kv()` or `.items()` (e.g., core/db/state_db.StateDB)
    Returns: `bytes` (32B root)
- `compute_state_root_from_items(items)`:
    Same as above but requires an iterable of `(key, value)`.

Inputs must be `bytes`; this function raises `TypeError` otherwise.
"""

from typing import Iterable, Iterator, Mapping, Sequence, Tuple, Union, List, Any
from dataclasses import dataclass

# Local hash wrapper (uses core.utils.hash if available)
try:  # prefer shared utils if present
    from core.utils.hash import sha3_256 as _sha3_256  # type: ignore
except Exception:  # pragma: no cover - fallback for isolated runs
    import hashlib as _hl

    def _sha3_256(data: bytes) -> bytes:
        return _hl.sha3_256(data).digest()


# Domain tags (must match spec/domains.yaml)
DOMAIN_LEAF = b"animica/state/leaf:v1"
DOMAIN_NODE = b"animica/state/node:v1"
DOMAIN_EMPTY = b"animica/state/empty:v1"

DIGEST_SIZE = 32


def _u32(n: int) -> bytes:
    if n < 0 or n > 0xFFFFFFFF:
        raise ValueError("length out of range for u32")
    return n.to_bytes(4, "big")


def _hash_leaf(key: bytes, value: bytes) -> bytes:
    return _sha3_256(DOMAIN_LEAF + _u32(len(key)) + key + _u32(len(value)) + value)


def _hash_node(left: bytes, right: bytes) -> bytes:
    # left/right are already fixed-length digests → no extra length tags required
    if len(left) != DIGEST_SIZE or len(right) != DIGEST_SIZE:
        raise ValueError("node children must be 32-byte digests")
    return _sha3_256(DOMAIN_NODE + left + right)


def _hash_empty() -> bytes:
    return _sha3_256(DOMAIN_EMPTY)


def _iter_items_normalized(
    kv: Union[Iterable[Tuple[bytes, bytes]], Mapping[bytes, bytes], Any]
) -> Iterator[Tuple[bytes, bytes]]:
    """
    Yield (key,value) pairs as bytes. Accepts a variety of kv-like inputs.
    """
    # StateDB-style fast path
    if hasattr(kv, "iter_kv") and callable(getattr(kv, "iter_kv")):
        for k, v in kv.iter_kv():  # type: ignore[attr-defined]
            yield _ensure_bytes_pair(k, v)
        return

    # Mapping-like (dict, any mapping)
    if isinstance(kv, Mapping):
        for k, v in kv.items():
            yield _ensure_bytes_pair(k, v)
        return

    # Generic iterable of pairs
    if isinstance(kv, Iterable):
        for item in kv:  # type: ignore[assignment]
            if not (isinstance(item, (tuple, list)) and len(item) == 2):
                raise TypeError("iterable items must be 2-tuples (key, value)")
            k, v = item  # type: ignore[misc]
            yield _ensure_bytes_pair(k, v)
        return

    raise TypeError("unsupported kv input; expected Mapping, iterable of pairs, or object with .iter_kv()")


def _ensure_bytes_pair(k: Any, v: Any) -> Tuple[bytes, bytes]:
    if not isinstance(k, (bytes, bytearray, memoryview)):
        raise TypeError(f"state key must be bytes-like, got {type(k)}")
    if not isinstance(v, (bytes, bytearray, memoryview)):
        raise TypeError(f"state value must be bytes-like, got {type(v)}")
    kb = bytes(k)
    vb = bytes(v)
    return kb, vb


def _sorted_unique(items: Iterable[Tuple[bytes, bytes]]) -> List[Tuple[bytes, bytes]]:
    """
    Sort lexicographically by key (raw bytes). Enforce uniqueness of keys.
    """
    arr = list(items)
    arr.sort(key=lambda kv: kv[0])
    # Check uniqueness
    for i in range(1, len(arr)):
        if arr[i - 1][0] == arr[i][0]:
            raise ValueError(f"duplicate state key detected: {arr[i][0].hex()}")
    return arr


def _build_leaves(pairs: Sequence[Tuple[bytes, bytes]]) -> List[bytes]:
    return [_hash_leaf(k, v) for (k, v) in pairs]


def _reduce_merkle_level(level: List[bytes]) -> List[bytes]:
    n = len(level)
    if n == 0:
        return []
    if n == 1:
        return level
    out: List[bytes] = []
    i = 0
    while i < n:
        left = level[i]
        right = level[i + 1] if (i + 1) < n else left  # duplicate last on odd fanout
        out.append(_hash_node(left, right))
        i += 2
    return out


def merkle_root_from_leaves(leaves: List[bytes]) -> bytes:
    """
    Compute the root from already-hashed leaves. Empty → domain-separated EMPTY hash.
    """
    if not leaves:
        return _hash_empty()
    level = leaves
    while len(level) > 1:
        level = _reduce_merkle_level(level)
    return level[0]


def compute_state_root_from_items(items: Iterable[Tuple[bytes, bytes]]) -> bytes:
    """
    Compute the canonical state root from an iterable of key/value byte pairs.
    """
    sorted_pairs = _sorted_unique(items)
    leaves = _build_leaves(sorted_pairs)
    return merkle_root_from_leaves(leaves)


def compute_state_root(
    kv: Union[Iterable[Tuple[bytes, bytes]], Mapping[bytes, bytes], Any]
) -> bytes:
    """
    Top-level entrypoint. Accepts a kv-like object (Mapping, iterable of pairs,
    or object with `.iter_kv()`), normalizes and sorts keys, and returns the 32B root.
    """
    return compute_state_root_from_items(_iter_items_normalized(kv))


# Optional helper for diagnostics
@dataclass(frozen=True)
class StateRootDebug:
    count: int
    first_key: bytes | None
    last_key: bytes | None
    root: bytes

    def hex(self) -> str:
        return self.root.hex()


def compute_state_root_with_debug(
    kv: Union[Iterable[Tuple[bytes, bytes]], Mapping[bytes, bytes], Any]
) -> StateRootDebug:
    """
    Same as `compute_state_root` but returns a small debug struct with count and edge keys.
    """
    pairs = _sorted_unique(_iter_items_normalized(kv))
    leaves = _build_leaves(pairs)
    root = merkle_root_from_leaves(leaves)
    first = pairs[0][0] if pairs else None
    last = pairs[-1][0] if pairs else None
    return StateRootDebug(count=len(pairs), first_key=first, last_key=last, root=root)
