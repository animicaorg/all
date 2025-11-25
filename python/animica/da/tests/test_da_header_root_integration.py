"""
DA header root integration tests.

Spec (for this test):

We model a *canonical* DA root aggregation function over a sequence of
per-blob DA commitments (each commitment is the NMT root for that blob).

Encoding rules for this test/spec:

    daRoot = sha3_256(
        "animica:daRoot:v1|" ||
        u32_be(len(commitments)) ||
        Σ over i in [0..n):
            u32_be(len(commitments[i])) ||
            commitments[i]
    )

Where:
  - "animica:daRoot:v1|" is an ASCII domain-separation tag.
  - u32_be(x) is x encoded as a 4-byte big-endian integer.
  - commitments[i] is an opaque commitment byte string (e.g. 32-byte NMT root).

This matches the intent described in the docs:
  - Deterministic over the *ordered* list of blob commitments.
  - Stable hash function (sha3_256).
  - Length-delimited to avoid ambiguity and allow variable-length encodings.

These tests build a fake block header structure using this spec and ensure that:
  - The computed daRoot is stable.
  - daRoot is sensitive to order and content of commitments.
  - Recomputing from the header's stored commitment list reproduces daRoot.
"""

from __future__ import annotations

import dataclasses
import hashlib
import os
from typing import List


def compute_da_root_for_commitments(commitments: List[bytes]) -> bytes:
    """
    Canonical DA root aggregation, per spec above.

    NOTE: This function is *pure* and does not depend on the rest of the
    Animica codebase; it only encodes the DA root spec that the L1 header
    must follow.
    """
    h = hashlib.sha3_256()
    # Domain separator for DA roots
    h.update(b"animica:daRoot:v1|")

    # Number of commitments
    h.update(len(commitments).to_bytes(4, "big"))

    # Each commitment is length-delimited so we can support arbitrary length
    for c in commitments:
        if not isinstance(c, (bytes, bytearray)):
            raise TypeError(f"commitment must be bytes, got {type(c)!r}")
        h.update(len(c).to_bytes(4, "big"))
        h.update(c)

    return h.digest()


@dataclasses.dataclass
class FakeHeader:
    """
    Minimal fake block header model focused on DA.

    In the real node, this would be part of the full header structure
    (containing prevHash, merkleRoot, etc). For these tests we only care
    about how daRoot is computed from the DA commitments.
    """

    version: int
    height: int
    da_root: bytes
    da_commitments: List[bytes]

    def to_wire_da_root_hex(self) -> str:
        """Hex encoding for daRoot as it would appear on the wire / JSON."""
        return self.da_root.hex()


def _make_random_commitment() -> bytes:
    # Model a 32-byte NMT root / commitment for these tests.
    return os.urandom(32)


def test_da_root_empty_commitments_is_well_defined_and_stable() -> None:
    """
    Even with no blob commitments, daRoot must be well-defined and stable.

    This corresponds to a block that carries no DA blobs; consumers can rely
    on a deterministic "empty DA root" instead of e.g. None.
    """
    commits: list[bytes] = []

    da_root1 = compute_da_root_for_commitments(commits)
    da_root2 = compute_da_root_for_commitments(commits)

    assert isinstance(da_root1, bytes)
    assert len(da_root1) == 32  # sha3_256 digest size
    assert da_root1 == da_root2, "DA root for empty set must be stable"

    header = FakeHeader(
        version=1,
        height=0,
        da_root=da_root1,
        da_commitments=commits,
    )

    # Recompute from header and ensure it matches.
    recomputed = compute_da_root_for_commitments(header.da_commitments)
    assert recomputed == header.da_root
    # Wire-encoding sanity check
    assert header.to_wire_da_root_hex() == da_root1.hex()


def test_da_root_matches_spec_for_single_and_multiple_commitments() -> None:
    """
    Build a fake header with 1 and N commitments and verify:

      * daRoot matches compute_da_root_for_commitments().
      * Recomputing from header.da_commitments is exact.
    """
    # Single commitment
    c1 = _make_random_commitment()
    da_root_single = compute_da_root_for_commitments([c1])

    header_single = FakeHeader(
        version=1,
        height=123,
        da_root=da_root_single,
        da_commitments=[c1],
    )

    recomputed_single = compute_da_root_for_commitments(header_single.da_commitments)
    assert recomputed_single == header_single.da_root

    # Multiple commitments
    c2 = _make_random_commitment()
    c3 = _make_random_commitment()
    commits = [c1, c2, c3]

    da_root_multi = compute_da_root_for_commitments(commits)
    header_multi = FakeHeader(
        version=1,
        height=124,
        da_root=da_root_multi,
        da_commitments=list(commits),
    )

    recomputed_multi = compute_da_root_for_commitments(header_multi.da_commitments)
    assert recomputed_multi == header_multi.da_root
    assert header_multi.to_wire_da_root_hex() == da_root_multi.hex()


def test_da_root_is_order_sensitive_and_content_sensitive() -> None:
    """
    The DA root must change if:

      * The order of commitments changes.
      * Any commitment bytes change.

    This is critical for block validity and DA proofs: reordering or swapping
    blobs must change the header root.
    """
    c1 = _make_random_commitment()
    c2 = _make_random_commitment()
    c3 = _make_random_commitment()

    commits = [c1, c2, c3]
    root_orig = compute_da_root_for_commitments(commits)

    # Reordering commitments must change the DA root
    commits_reordered = [c2, c1, c3]
    root_reordered = compute_da_root_for_commitments(commits_reordered)
    assert root_reordered != root_orig

    # Flipping a single bit in one commitment must change the DA root
    c1_mutated = bytearray(c1)
    c1_mutated[0] ^= 0x01
    root_mutated = compute_da_root_for_commitments([bytes(c1_mutated), c2, c3])
    assert root_mutated != root_orig

    # Headers reflect those differences
    header_orig = FakeHeader(
        version=1,
        height=555,
        da_root=root_orig,
        da_commitments=commits,
    )
    header_reordered = FakeHeader(
        version=1,
        height=555,
        da_root=root_reordered,
        da_commitments=commits_reordered,
    )

    assert header_orig.da_root != header_reordered.da_root
    assert header_orig.to_wire_da_root_hex() != header_reordered.to_wire_da_root_hex()


def test_da_root_roundtrip_from_header_commitments() -> None:
    """
    Given a header with daCommitments stored explicitly, recomputing
    daRoot from those commitments must be exact.

    This models a light client or verification tool that replays only the
    DA aggregation spec over given commitments and compares against the header.
    """
    commits = [_make_random_commitment() for _ in range(5)]
    da_root = compute_da_root_for_commitments(commits)

    header = FakeHeader(
        version=2,
        height=999_999,
        da_root=da_root,
        da_commitments=list(commits),
    )

    recomputed = compute_da_root_for_commitments(header.da_commitments)
    assert recomputed == header.da_root, "Header.daRoot must match spec recomputation"

    # Change a single stored commitment: daRoot must no longer match
    header.da_commitments[2] = _make_random_commitment()
    recomputed_after_mutation = compute_da_root_for_commitments(header.da_commitments)
    assert recomputed_after_mutation != header.da_root
