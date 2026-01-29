"""
Animica • DA utilities — Hashing helpers (SHA3 + domain tags)

This module provides:

  • Thin wrappers around SHA3-256 / SHA3-512
  • Safe concatenation helpers
  • Domain-separated hashing (`hash_domain`) that length-prefixes parts
  • NMT-specific helpers for computing leaf/inner hashes with 1-byte tags
    (see da/schemas/nmt.cddl for wire semantics)

Rationale
---------
Animica relies on explicit, unambiguous domain separation. For generic
purposes use `hash_domain(tag, *parts)`, which prefixes the preimage with a
fixed ASCII prologue and the caller-supplied `tag`, and length-prefixes each
part to avoid ambiguity. For the DA Namespaced Merkle Tree (NMT), use
`nmt_leaf_hash(...)` and `nmt_inner_hash(...)`, which follow the exact
byte layouts described in the NMT spec:

  leaf_preimage  = 0x00 | varuint(ns) | varuint(size) | data
  inner_preimage = 0x01 | left_hash | right_hash
                         | varuint(ns_min) | varuint(ns_max)

All functions return raw bytes. `*_hex` variants return lowercase "0x" hex.
"""

from __future__ import annotations

from hashlib import sha3_256 as _sha3_256
from hashlib import sha3_512 as _sha3_512
from typing import Iterable, Union

BytesLike = Union[bytes, bytearray, memoryview]

# ------------------------------ Basic wrappers -------------------------------


def sha3_256(data: BytesLike) -> bytes:
    """Return SHA3-256(bytes(data))."""
    return _sha3_256(_b(data)).digest()


def sha3_256_hex(data: BytesLike) -> str:
    """Return '0x' + lowercase hex of SHA3-256(bytes(data))."""
    return "0x" + _sha3_256(_b(data)).hexdigest()


def sha3_512(data: BytesLike) -> bytes:
    """Return SHA3-512(bytes(data))."""
    return _sha3_512(_b(data)).digest()


def sha3_512_hex(data: BytesLike) -> str:
    """Return '0x' + lowercase hex of SHA3-512(bytes(data))."""
    return "0x" + _sha3_512(_b(data)).hexdigest()


# -------------------------- Domain-separated hashing -------------------------

_ANIMICA_DS_PREFIX = b"Animica|DS|"


def hash_domain(tag: Union[str, bytes], *parts: BytesLike, bits: int = 256) -> bytes:
    """
    Domain-separated hash with robust framing.

    Preimage layout:
        b"Animica|DS|" || tag || b"|" || 0x00 ||
        for part in parts:
            varuint(len(part)) || part

    Args:
        tag:  Short human-readable tag (e.g., "tx.signbytes") or bytes.
        parts: Byte-like segments to frame and hash.
        bits:  256 (default) or 512 to choose SHA3 variant.

    Returns:
        Raw hash bytes.
    """
    if isinstance(tag, str):
        tag_b = tag.encode("ascii")
    else:
        tag_b = bytes(tag)

    pre = bytearray()
    pre += _ANIMICA_DS_PREFIX
    pre += tag_b + b"|" + b"\x00"
    for p in parts:
        pb = _b(p)
        pre += _varuint(len(pb))
        pre += pb

    if bits == 256:
        return _sha3_256(pre).digest()
    elif bits == 512:
        return _sha3_512(pre).digest()
    else:
        raise ValueError("Unsupported 'bits' value; use 256 or 512")


def hash_domain_hex(tag: Union[str, bytes], *parts: BytesLike, bits: int = 256) -> str:
    """Hex string ('0x'…) form of `hash_domain`."""
    return (
        "0x"
        + (_sha3_256 if bits == 256 else _sha3_512)(  # type: ignore[misc]
            bytearray(_ANIMICA_DS_PREFIX)
            + (tag.encode("ascii") if isinstance(tag, str) else bytes(tag))
            + b"|\x00"
            + b"".join(_varuint(len(_b(p))) + _b(p) for p in parts)
        ).hexdigest()
    )


# ---------------------------- NMT-specific helpers ---------------------------

# Single-byte domain tags used by NMT hashing (see nmt.cddl):
TAG_NMT_LEAF = b"\x00"
TAG_NMT_INNER = b"\x01"


def nmt_leaf_preimage(ns: int, size: int, data: BytesLike) -> bytes:
    """
    Build the exact preimage used by the NMT for a leaf.

        preimage = 0x00 | varuint(ns) | varuint(size) | data
    """
    if ns < 0 or size < 0:
        raise ValueError("ns and size must be non-negative")
    db = _b(data)
    if size != len(db):
        # Enforce canonical size declaration.
        raise ValueError(f"declared size {size} != len(data) {len(db)}")
    return TAG_NMT_LEAF + _varuint(ns) + _varuint(size) + db


def nmt_leaf_hash(ns: int, data: BytesLike) -> bytes:
    """
    Compute SHA3-256 hash of an NMT leaf with domain tag 0x00.
    """
    db = _b(data)
    pre = TAG_NMT_LEAF + _varuint(ns) + _varuint(len(db)) + db
    return _sha3_256(pre).digest()


def nmt_leaf_hash_hex(ns: int, data: BytesLike) -> str:
    """Hex form of `nmt_leaf_hash`."""
    return "0x" + _sha3_256(nmt_leaf_preimage(ns, len(_b(data)), _b(data))).hexdigest()


def nmt_inner_preimage(
    left_hash: BytesLike, right_hash: BytesLike, ns_min: int, ns_max: int
) -> bytes:
    """
    Build the exact preimage used by the NMT for an inner node.

        preimage = 0x01 | left_hash | right_hash | varuint(ns_min) | varuint(ns_max)
    """
    if not (0 <= ns_min <= ns_max):
        raise ValueError("Namespace bounds must satisfy 0 <= ns_min <= ns_max")
    lh = _b(left_hash)
    rh = _b(right_hash)
    if len(lh) != 32 or len(rh) != 32:
        raise ValueError("left_hash and right_hash must be 32 bytes (SHA3-256)")
    return TAG_NMT_INNER + lh + rh + _varuint(ns_min) + _varuint(ns_max)


def nmt_inner_hash(
    left_hash: BytesLike, right_hash: BytesLike, ns_min: int, ns_max: int
) -> bytes:
    """
    Compute SHA3-256 hash of an NMT inner node with domain tag 0x01.
    """
    return _sha3_256(nmt_inner_preimage(left_hash, right_hash, ns_min, ns_max)).digest()


def nmt_inner_hash_hex(
    left_hash: BytesLike, right_hash: BytesLike, ns_min: int, ns_max: int
) -> str:
    """Hex form of `nmt_inner_hash`."""
    return (
        "0x"
        + _sha3_256(
            nmt_inner_preimage(left_hash, right_hash, ns_min, ns_max)
        ).hexdigest()
    )


# ------------------------------- Misc helpers --------------------------------


def _b(x: BytesLike) -> bytes:
    """Coerce common byte-likes (bytes/bytearray/memoryview) to `bytes`."""
    if isinstance(x, bytes):
        return x
    if isinstance(x, bytearray):
        return bytes(x)
    if isinstance(x, memoryview):
        return x.tobytes()
    # Defensive fallback (should not happen due to typing)
    return bytes(x)  # type: ignore[arg-type]


def _varuint(n: int) -> bytes:
    """
    Unsigned LEB128 (little-endian base-128) encoding.
    Deterministic and compact; matches the encoding referenced in the NMT spec.
    """
    if n < 0:
        raise ValueError("varuint requires a non-negative integer")
    out = bytearray()
    while True:
        byte = n & 0x7F
        n >>= 7
        if n:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            break
    return bytes(out)


def concat(parts: Iterable[BytesLike]) -> bytes:
    """Concatenate iterable of byte-like objects efficiently."""
    return b"".join(_b(p) for p in parts)


__all__ = [
    # basic
    "sha3_256",
    "sha3_256_hex",
    "sha3_512",
    "sha3_512_hex",
    # domain-separated
    "hash_domain",
    "hash_domain_hex",
    # NMT helpers
    "TAG_NMT_LEAF",
    "TAG_NMT_INNER",
    "nmt_leaf_preimage",
    "nmt_leaf_hash",
    "nmt_leaf_hash_hex",
    "nmt_inner_preimage",
    "nmt_inner_hash",
    "nmt_inner_hash_hex",
    # misc
    "concat",
]
