"""
execution.receipts.logs_hash — helpers for logs bloom and logs Merkle/root.

Deterministic, domain-separated hashing for receipt logs.

Design choices (documented for cross-impl parity)
-------------------------------------------------
• Hash function: SHA3-256 (NIST) with explicit domain tags to avoid cross-domain
  collisions. We *do not* use Keccak-256 here; if a future spec chooses Keccak,
  swap `_sha3_256` with a Keccak variant everywhere in this file.

• Log leaf hashing: canonical CBOR of {address, topics, data} with keys exactly
  as written, encoded using RFC 7049bis canonical ordering. This guarantees
  stable leaf hashes across languages.

• Merkle tree: binary, pairwise. If a level has an odd number of nodes we
  duplicate the last hash (classic "Bitcoin-style" padding) to keep the tree
  balanced. Node hash = H("logs/node" || left || right). Leaf hash =
  H("logs/leaf" || cbor(log)). Empty tree root = H("logs/empty").

• Bloom filter: 2048-bit (256 bytes) bitset by default. For each log we OR bits
  derived from the address and each topic (data is intentionally excluded).
  Indices are derived by taking k=3 16-bit chunks from SHA3-256(domain || item)
  and masking with (bits-1). `bits` MUST be a power of two (default 2048).

Public API
----------
- compute_logs_bloom(logs: Iterable[LogEvent], bits: int = 2048, k: int = 3) -> bytes
- compute_logs_root(logs: Iterable[LogEvent]) -> bytes
"""

from __future__ import annotations

from typing import Iterable, List, Mapping, Sequence, Tuple
import hashlib

try:
    import cbor2  # type: ignore
except Exception as e:  # pragma: no cover
    raise ImportError(
        "execution.receipts.logs_hash requires 'cbor2'. Install with: pip install cbor2"
    ) from e

# Prefer the canonical LogEvent from execution.types
from execution.types.events import LogEvent


# ---------------------------- Byte helpers ----------------------------------


def _is_bytes_like(x) -> bool:
    return isinstance(x, (bytes, bytearray, memoryview))


def _to_bytes(x) -> bytes:
    if isinstance(x, bytes):
        return x
    if isinstance(x, bytearray):
        return bytes(x)
    if isinstance(x, memoryview):
        return bytes(x)
    if isinstance(x, str) and x.startswith("0x"):
        h = x[2:]
        if len(h) % 2:
            h = "0" + h
        return bytes.fromhex(h)
    raise TypeError(f"Expected bytes-like, got {type(x)!r}")


# ---------------------------- Hash primitives -------------------------------

_D_LEAF = b"animica:logs:leaf"
_D_NODE = b"animica:logs:node"
_D_EMPTY = b"animica:logs:empty"
_D_BLOOM = b"animica:logs:bloom"


def _sha3_256(data: bytes) -> bytes:
    return hashlib.sha3_256(data).digest()


def _h(domain: bytes, *parts: bytes) -> bytes:
    """Domain-separated hash: H(domain || 0x00 || part0 || part1 || ...)."""
    return _sha3_256(domain + b"\x00" + b"".join(parts))


# ---------------------------- Log canonicalization --------------------------


def _log_to_canonical_map(ev: LogEvent) -> Mapping[str, bytes | list[bytes]]:
    addr = _to_bytes(ev.address)
    topics_list = [ _to_bytes(t) for t in list(ev.topics) ]
    data = _to_bytes(ev.data)
    # Canonical field names for wire/leaf encoding
    return {"address": addr, "topics": topics_list, "data": data}


def _hash_log_leaf(ev: LogEvent) -> bytes:
    obj = _log_to_canonical_map(ev)
    enc = cbor2.dumps(obj, canonical=True)
    return _h(_D_LEAF, enc)


# ---------------------------- Merkle (logs root) ----------------------------


def _merkle_pair(l: bytes, r: bytes) -> bytes:
    return _h(_D_NODE, l + r)


def _merkle_root(leaves: Sequence[bytes]) -> bytes:
    if not leaves:
        return _h(_D_EMPTY)
    level = list(leaves)
    while len(level) > 1:
        nxt: List[bytes] = []
        it = iter(level)
        for left in it:
            try:
                right = next(it)
            except StopIteration:
                right = left  # duplicate last
            nxt.append(_merkle_pair(left, right))
        level = nxt
    return level[0]


def compute_logs_root(logs: Iterable[LogEvent]) -> bytes:
    """
    Compute the Merkle root over the given logs.

    Returns:
        32-byte SHA3-256 digest (bytes).
    """
    leaves = [_hash_log_leaf(ev) for ev in logs]
    return _merkle_root(leaves)


# ---------------------------- Bloom (2048-bit default) ----------------------


def _set_bit(bitset: bytearray, idx: int) -> None:
    byte_index = idx // 8
    bit_index = idx % 8
    bitset[byte_index] |= (1 << (7 - bit_index))  # big-endian within byte


def _bloom_indices(item: bytes, bits: int, k: int) -> Tuple[int, ...]:
    digest = _h(_D_BLOOM, item)
    # Use 2-byte windows for indices; wrap if needed.
    out: List[int] = []
    mask = bits - 1
    needed = k
    i = 0
    while needed > 0:
        j = (2 * i) % (len(digest) - 1)  # ensure j+1 is in range
        idx = int.from_bytes(digest[j : j + 2], "big") & mask
        out.append(idx)
        needed -= 1
        i += 1
    return tuple(out)


def compute_logs_bloom(logs: Iterable[LogEvent], bits: int = 2048, k: int = 3) -> bytes:
    """
    Build a bloom filter over (address, topics) of all logs.

    Args:
        logs: iterable of LogEvent
        bits: size of bloom bitset; MUST be a power of two (default 2048)
        k:    number of hash functions per item (default 3)

    Returns:
        bytes of length bits//8
    """
    if bits <= 0 or bits & (bits - 1) != 0:
        raise ValueError("bits must be a positive power of two")
    if k <= 0:
        raise ValueError("k must be >= 1")

    bloom = bytearray(bits // 8)
    for ev in logs:
        addr = _to_bytes(ev.address)
        for idx in _bloom_indices(addr, bits, k):
            _set_bit(bloom, idx)
        for t in ev.topics:
            for idx in _bloom_indices(_to_bytes(t), bits, k):
                _set_bit(bloom, idx)
    return bytes(bloom)


__all__ = ["compute_logs_bloom", "compute_logs_root"]
