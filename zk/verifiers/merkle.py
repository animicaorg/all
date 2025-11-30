"""
Animica zk.verifiers.merkle
==========================

Generic Merkle helpers for STARK public I/O and commitments.

Goals
-----
- **Portable hashing**: SHA3-256 (default), SHA-256, or BLAKE2s-256.
- **Flexible leaf encodings** commonly used in zk/STARK systems:
    * 32-byte big-endian of an unsigned integer (u256).
    * 32-byte big-endian of a field element modulo `modulus`.
    * Raw bytes (pre-hashed or already 32 bytes).
- **Simple verification** of inclusion branches with left/right determined by
  the query `index`'s bits (classic Merkle convention).
- Optional *position binding* (include the leaf's row index in the leaf hash)
  which some protocols adopt to prevent swapping leaves with identical values.

API
---
Hashers (32-byte digests):
    - sha3_256, sha2_256, blake2s_256

Encoding helpers:
    - be_bytes(x: int, n=32) -> bytes
    - encode_u256(x: int) -> bytes
    - encode_field(x: int, modulus: int) -> bytes
    - ensure_bytes32(b: bytes) -> bytes

Core:
    - merkle_root(leaves: Iterable[bytes], *, hasher=sha3_256) -> bytes
    - merkle_verify(
          root: bytes|str,
          leaf_value: int|bytes|str,
          path: Sequence[bytes|str],
          index: int,
          *,
          hasher=sha3_256,
          encoding: str = "u256",        # "u256" | "field" | "bytes"
          modulus: int | None = None,    # required when encoding == "field"
          bind_position: bool = False,   # leaf = H(encode(v) || be32(index)) if True
          prehashed_leaf: bool = False   # if True, leaf_value is already a 32-byte digest
      ) -> bool

Utilities (dev/test):
    - build_tree(leaves: Iterable[bytes], *, hasher=sha3_256) -> list[list[bytes]]
    - hexify(b: bytes) -> str
    - parse_hex(s: str) -> bytes

Notes
-----
- Leaves/branches are always combined as `H(left || right)`.
- `index` is interpreted little-endian by bit position (LSB decides whether the
  current node is a *right* child (`index & 1 == 1`) or *left* child).
- All encodings produce **32-byte** big-endian leaves to keep consistency with
  common STARK/zk conventions.

License: MIT
"""

from __future__ import annotations

import hashlib
from typing import Callable, Iterable, List, Optional, Sequence, Tuple, Union

# -----------------------------------------------------------------------------
# Hashers (32-byte digests)
# -----------------------------------------------------------------------------

Hasher = Callable[[bytes], bytes]


def sha3_256(b: bytes) -> bytes:
    return hashlib.sha3_256(b).digest()


def sha2_256(b: bytes) -> bytes:
    return hashlib.sha256(b).digest()


def blake2s_256(b: bytes) -> bytes:
    return hashlib.blake2s(b, digest_size=32).digest()


# -----------------------------------------------------------------------------
# Encoding helpers
# -----------------------------------------------------------------------------


def be_bytes(x: int, n: int = 32) -> bytes:
    """Big-endian, fixed-length encoding of an integer (wraps negative via mod 2^(8n))."""
    if n <= 0:
        raise ValueError("n must be > 0")
    return int(x % (1 << (8 * n))).to_bytes(n, "big")


def encode_u256(x: int) -> bytes:
    """Encode integer as 32-byte big-endian (mod 2^256)."""
    return be_bytes(int(x), 32)


def encode_field(x: int, modulus: int) -> bytes:
    """Encode field element (reduced mod `modulus`) as 32-byte big-endian."""
    if not isinstance(modulus, int) or modulus <= 1:
        raise ValueError("modulus must be an integer > 1")
    return be_bytes(int(x) % modulus, 32)


def ensure_bytes32(b: bytes) -> bytes:
    """Ensure `b` is exactly 32 bytes (left-pad with zeros if shorter)."""
    if not isinstance(b, (bytes, bytearray, memoryview)):
        raise TypeError("expected bytes-like for ensure_bytes32")
    b = bytes(b)
    if len(b) == 32:
        return b
    if len(b) < 32:
        return (b"\x00" * (32 - len(b))) + b
    # If longer, hash to 32 bytes (sane default for dev tools)
    return sha3_256(b)


def hexify(b: bytes) -> str:
    return "0x" + bytes(b).hex()


def parse_hex(s: str) -> bytes:
    s = str(s).strip()
    if s.startswith("0x") or s.startswith("0X"):
        s = s[2:]
    if len(s) % 2 == 1:
        s = "0" + s
    return bytes.fromhex(s)


# -----------------------------------------------------------------------------
# Core Merkle utilities
# -----------------------------------------------------------------------------


def _combine(left: bytes, right: bytes, *, hasher: Hasher) -> bytes:
    return hasher(left + right)


def _leaf_bytes(
    leaf_value: Union[int, bytes, str],
    *,
    encoding: str,
    modulus: Optional[int],
    bind_position_index: Optional[int],
) -> bytes:
    """
    Normalize leaf to the 32-byte payload BEFORE hashing.
    If `bind_position_index` is set, append be32(index) to the payload.
    """
    if encoding not in ("u256", "field", "bytes"):
        raise ValueError("encoding must be one of: 'u256', 'field', 'bytes'")

    if isinstance(leaf_value, (bytes, bytearray, memoryview)):
        raw = (
            ensure_bytes32(bytes(leaf_value))
            if encoding == "bytes"
            else ensure_bytes32(bytes(leaf_value))
        )
    elif isinstance(leaf_value, str):
        # Treat as hex string when encoding=bytes; as int otherwise
        if encoding == "bytes":
            raw = ensure_bytes32(parse_hex(leaf_value))
        else:
            raw = encode_u256(int(leaf_value, 0)) if encoding == "u256" else encode_field(int(leaf_value, 0), int(modulus))  # type: ignore[arg-type]
    else:
        # int
        if encoding == "u256":
            raw = encode_u256(int(leaf_value))
        elif encoding == "field":
            if modulus is None:
                raise ValueError("modulus is required for encoding='field'")
            raw = encode_field(int(leaf_value), int(modulus))
        else:  # bytes
            raise TypeError("bytes encoding requires bytes/hex input")

    if bind_position_index is not None:
        raw = raw + be_bytes(int(bind_position_index), 32)

    return raw


def merkle_root(leaves: Iterable[bytes], *, hasher: Hasher = sha3_256) -> bytes:
    """
    Compute Merkle root (H(left||right) at each internal node).
    Leaves are *raw 32-byte payloads that are individually hashed once*.
    """
    level: List[bytes] = [hasher(ensure_bytes32(leaf)) for leaf in leaves]
    if not level:
        # Convention: empty tree → hash of empty string
        return hasher(b"")
    while len(level) > 1:
        nxt: List[bytes] = []
        it = iter(level)
        for a in it:
            b = next(it, None)
            if b is None:
                # If odd, duplicate last node (classic convention)
                b = a
            nxt.append(_combine(a, b, hasher=hasher))
        level = nxt
    return level[0]


def build_tree(
    leaves: Iterable[bytes], *, hasher: Hasher = sha3_256
) -> List[List[bytes]]:
    """
    Build the full tree levels (for testing):
    returns [L0 leaves_hashed, L1, ..., root_level] where root_level has len==1.
    """
    L0 = [hasher(ensure_bytes32(leaf)) for leaf in leaves]
    if not L0:
        return [[hasher(b"")]]
    levels: List[List[bytes]] = [L0]
    while len(levels[-1]) > 1:
        cur = levels[-1]
        nxt: List[bytes] = []
        it = iter(cur)
        for a in it:
            b = next(it, None)
            if b is None:
                b = a
            nxt.append(_combine(a, b, hasher=hasher))
        levels.append(nxt)
    return levels


def merkle_verify(
    root: Union[bytes, str],
    leaf_value: Union[int, bytes, str],
    path: Sequence[Union[bytes, str]],
    index: int,
    *,
    hasher: Hasher = sha3_256,
    encoding: str = "u256",  # "u256" | "field" | "bytes"
    modulus: Optional[int] = None,  # required when encoding == "field"
    bind_position: bool = False,
    prehashed_leaf: bool = False,
) -> bool:
    """
    Verify an inclusion proof.

    - If `prehashed_leaf=False` (default), we compute `leaf_hash = H(encode(leaf_value))`
      (or `H(encode(v) || be32(index))` if `bind_position=True`).
    - If `prehashed_leaf=True`, `leaf_value` must be exactly a 32-byte digest (bytes or 0x-hex).

    Returns True on success, False otherwise.
    """
    try:
        if isinstance(root, (bytes, bytearray, memoryview)):
            root_bytes = bytes(root)
        else:
            root_bytes = parse_hex(str(root))

        if prehashed_leaf:
            if isinstance(leaf_value, (bytes, bytearray, memoryview)):
                node = bytes(leaf_value)
            else:
                node = parse_hex(str(leaf_value))
            if len(node) != 32:
                return False
        else:
            payload = _leaf_bytes(
                leaf_value,
                encoding=encoding,
                modulus=modulus,
                bind_position_index=(index if bind_position else None),
            )
            node = hasher(payload)

        idx = int(index)
        for sib in path:
            sib_bytes = (
                bytes(sib)
                if isinstance(sib, (bytes, bytearray, memoryview))
                else parse_hex(str(sib))
            )
            if len(sib_bytes) != 32:
                return False
            if (idx & 1) == 1:
                # current node is right child → parent = H(sibling || node)
                node = _combine(sib_bytes, node, hasher=hasher)
            else:
                # current node is left child  → parent = H(node || sibling)
                node = _combine(node, sib_bytes, hasher=hasher)
            idx >>= 1

        return node == root_bytes
    except Exception:
        return False


# -----------------------------------------------------------------------------
# Minimal CLI (dev)
# -----------------------------------------------------------------------------

if __name__ == "__main__":  # pragma: no cover
    # Simple self-check: build a tree for leaves [1,2,3], verify leaf 2 path.
    leaves = [encode_u256(1), encode_u256(2), encode_u256(3)]
    tree = build_tree(leaves)
    root = tree[-1][0]
    # path for index 1 (leaf=2): sibling at level0 is H(3) because we padded (odd duplication makes (3,3) for last pair).
    # Build path directly from built tree:
    path = []
    idx = 1
    for lvl in range(len(tree) - 1):
        cur = tree[lvl]
        sib = cur[idx ^ 1 if (idx ^ 1) < len(cur) else idx]  # duplicate last if odd
        path.append(sib)
        idx >>= 1
    ok = merkle_verify(root, 2, path, 1, hasher=sha3_256, encoding="u256")
    print("Self-test merkle_verify ->", ok)
    print("Root (hex):", hexify(root))
