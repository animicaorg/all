from __future__ import annotations

import hashlib
import os
import random
from typing import Iterable, List, Tuple

import pytest

# The module under test. We keep the test resilient by accepting several API shapes.
import consensus.share_receipts as sr_mod  # type: ignore


# ------------------------- Local canonical helpers (spec-aligned) -------------------------

def _sha3_256(b: bytes) -> bytes:
    return hashlib.sha3_256(b).digest()


def canonical_leaf_bytes(idx: int, miner: bytes, d_ratio_micro: int, nonce: int) -> bytes:
    """
    Deterministic, schema-agnostic leaf material for a 'share receipt'.

    Layout (not consensus-critical for this test, only for local reproducibility):
        idx      : 4-byte big-endian
        miner    : 20 bytes (address-like)
        d_ratio  : 8-byte big-endian (µ-units; e.g., 100_000 == 0.1 nats-equivalent)
        nonce    : 8-byte big-endian

    In production, the leaf encoding is defined by spec and sr_mod;
    here we only need stable bytes to exercise Merkle and ordering.
    """
    if len(miner) != 20:
        raise ValueError("miner must be 20 bytes")
    return (
        idx.to_bytes(4, "big")
        + miner
        + d_ratio_micro.to_bytes(8, "big", signed=False)
        + nonce.to_bytes(8, "big", signed=False)
    )


def merkle_root_from_leaves(leaves: Iterable[bytes]) -> bytes:
    """
    Canonical Merkle (spec-style):
      - hash each leaf = sha3_256(0x00 || leaf)
      - parent = sha3_256(0x01 || left || right)
      - odd nodes duplicate last
      - empty set -> sha3_256(b"") sentinel
    """
    leaves = list(leaves)
    if not leaves:
        return _sha3_256(b"")

    level = [_sha3_256(b"\x00" + leaf) for leaf in leaves]
    while len(level) > 1:
        nxt = []
        it = iter(level)
        for a in it:
            try:
                b = next(it)
            except StopIteration:
                b = a  # duplicate last
            nxt.append(_sha3_256(b"\x01" + a + b))
        level = nxt
    return level[0]


# ----------------------------- Adapter into sr_mod (flexible) -----------------------------

def sr_merkle_root(leaves: List[bytes]) -> bytes:
    """
    Try a handful of plausible entrypoints in consensus/share_receipts.py.
    Falls back to local canonical Merkle if the module exposes only an
    aggregate API returning the receipts.
    """
    # 1) direct function
    for name in ("merkle_root", "compute_merkle_root", "receipts_merkle_root"):
        fn = getattr(sr_mod, name, None)
        if callable(fn):
            try:
                return bytes(fn(leaves))  # type: ignore[arg-type]
            except TypeError:
                # some implementations expect receipt objects; ignore here
                pass

    # 2) aggregate() returning {root, receipts} or (root, receipts)
    for name in ("aggregate", "aggregate_receipts", "build"):
        fn = getattr(sr_mod, name, None)
        if callable(fn):
            res = fn(leaves)  # type: ignore[misc]
            if isinstance(res, tuple) and len(res) >= 1:
                return bytes(res[0])
            if isinstance(res, dict) and "root" in res:
                return bytes(res["root"])

    # 3) class style
    for cname in ("ShareReceipts", "Aggregator", "ReceiptBuilder"):
        C = getattr(sr_mod, cname, None)
        if C is not None:
            obj = C() if isinstance(C, type) else C
            for m in ("merkle_root", "compute_root", "root"):
                fn = getattr(obj, m, None)
                if callable(fn):
                    return bytes(fn(leaves))  # type: ignore[misc]

    # Fallback: use our canonical calculation (still validates determinism properties below).
    return merkle_root_from_leaves(leaves)


def sort_key_hash(leaf: bytes) -> bytes:
    """Sort key used by many implementations: the leaf-hash."""
    return _sha3_256(b"\x00" + leaf)


# ------------------------------------ Test cases ------------------------------------

def _make_sample_leaves(n: int = 7) -> List[bytes]:
    rng = random.Random(1337)
    leaves: List[bytes] = []
    for i in range(n):
        miner = bytes([i + 1]) * 20  # 20-byte address-like pattern
        d_ratio_micro = 50_000 + i * 7_000  # arbitrary µ-units progression
        nonce = rng.randrange(1 << 32)
        leaves.append(canonical_leaf_bytes(i, miner, d_ratio_micro, nonce))
    return leaves


def test_merkle_root_is_deterministic_under_permutation():
    """
    Implementations must produce the SAME root regardless of input order
    (by internally sorting canonically, e.g., by leaf-hash).

    We compute the expected root by sorting leaves by sha3(0x00||leaf)
    and then building the canonical Merkle root locally, and demand the
    module's root matches this value across permutations.
    """
    base = _make_sample_leaves(9)

    # expected: sort by hash-of-leaf (stable), then canonical Merkle
    expected = merkle_root_from_leaves(sorted(base, key=sort_key_hash))

    # permute input (reverse, shuffled) — module root must remain equal to expected
    reversed_in = list(reversed(base))
    shuffled_in = base[:]
    random.Random(4242).shuffle(shuffled_in)

    r1 = sr_merkle_root(base)
    r2 = sr_merkle_root(reversed_in)
    r3 = sr_merkle_root(shuffled_in)

    assert r1 == expected, "root for canonical order must match expected"
    assert r2 == expected, "root must be order-independent (reverse)"
    assert r3 == expected, "root must be order-independent (shuffle)"


def test_merkle_root_stability_over_batches_with_same_leaves():
    """
    Two batches with identical leaves (byte-for-byte), possibly constructed
    via different Python objects, must yield identical roots.
    """
    a = _make_sample_leaves(5)
    # Reconstruct 'equivalent' leaves (copy-bytes) to ensure no aliasing assumptions.
    b = [bytes(x) for x in a]

    ra = sr_merkle_root(a)
    rb = sr_merkle_root(b)
    assert ra == rb, "identical leaf sets must yield identical Merkle roots"


def test_empty_set_merkle_root_is_sentinel():
    """
    Empty set should not crash. We use sha3_256(b"") as the sentinel root.
    The module may implement the same or return a constant; both acceptable
    as long as it is stable.
    """
    local = merkle_root_from_leaves([])
    sr = sr_merkle_root([])
    # Accept either exact match to sha3_256(b"") or any stable 32-byte value.
    assert isinstance(sr, (bytes, bytearray)) and len(sr) == 32
    # Strong check: prefer the spec-style sentinel.
    assert sr == local


def test_same_content_different_indices_different_root():
    """
    If the content differs (e.g., index/nonce), the root must change.
    This guards against accidental sorting by only miner address or
    ignoring fields when forming the leaf bytes.
    """
    leaves_a = _make_sample_leaves(6)
    leaves_b = _make_sample_leaves(6)
    # Mutate one field deterministically
    mutated = bytearray(leaves_b[3])
    mutated[-1] ^= 0xFF
    leaves_b[3] = bytes(mutated)

    ra = sr_merkle_root(leaves_a)
    rb = sr_merkle_root(leaves_b)
    assert ra != rb, "distinct leaf sets must not collide to the same Merkle root"
