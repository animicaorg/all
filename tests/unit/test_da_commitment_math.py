# SPDX-License-Identifier: Apache-2.0
"""
Data Availability commitment math: Namespaced Merkle Tree (NMT) + simple erasure.

This test provides a *reference* in-Python NMT implementation with domain
separated hashing and a trivial 2x repetition "erasure" (replication) code.
It checks that:
  - The NMT root computed bottom-up matches a streaming/incremental builder
  - The root reflects correct namespace min/max aggregation
  - Padding to the next power-of-two does not change the effective commitment

Notes
-----
* This is intentionally self-contained and does not depend on the node or DA
  service. It validates the math shape used by the system, not the RPC path.
* The "erasure" here is a repetition (2,1) code to keep the unit test
  dependency-free and deterministic. In production networks you would use a
  Reed–Solomon style extension and then commit the *extended* matrix with an
  NMT; the NMT mechanics (namespace min/max propagation and domain-separated
  hashing) remain the same and are what we validate here.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Iterable, List, Sequence, Tuple

import pytest

# ------------------------------- NMT primitives -------------------------------

HASH_FN = hashlib.sha256  # Swap here if network mandates a different hash
LEAF_DOMAIN = b"NMT:leaf:v1\x00"  # domain separation tags
NODE_DOMAIN = b"NMT:node:v1\x00"

U64_MAX = (1 << 64) - 1
PADDING_NAMESPACE = U64_MAX  # padding uses max-namespace sentinel


def _u64be(x: int) -> bytes:
    return int(x).to_bytes(8, "big")


@dataclass(frozen=True)
class NmtNode:
    ns_min: int
    ns_max: int
    digest: bytes


def nmt_leaf(ns: int, payload: bytes) -> NmtNode:
    """Hash a leaf with namespace and payload."""
    h = HASH_FN()
    h.update(LEAF_DOMAIN)
    h.update(_u64be(ns))  # ns_min
    h.update(_u64be(ns))  # ns_max
    h.update(payload)
    return NmtNode(ns_min=ns, ns_max=ns, digest=h.digest())


def nmt_parent(left: NmtNode, right: NmtNode) -> NmtNode:
    """Combine two children into a parent with namespace range aggregation."""
    ns_min = min(left.ns_min, right.ns_min)
    ns_max = max(left.ns_max, right.ns_max)
    h = HASH_FN()
    h.update(NODE_DOMAIN)
    h.update(_u64be(ns_min))
    h.update(_u64be(ns_max))
    h.update(left.digest)
    h.update(right.digest)
    return NmtNode(ns_min=ns_min, ns_max=ns_max, digest=h.digest())


def _next_pow2(n: int) -> int:
    if n <= 1:
        return 1
    return 1 << (n - 1).bit_length()


def _pad_to_pow2(nodes: List[NmtNode]) -> List[NmtNode]:
    """Pad with empty leaves without changing the observed namespace range."""
    need = _next_pow2(len(nodes)) - len(nodes)
    if need <= 0:
        return nodes
    pad_ns = max((n.ns_max for n in nodes), default=PADDING_NAMESPACE)
    empty_leaf = nmt_leaf(pad_ns, b"")
    return nodes + [empty_leaf] * need


def build_nmt_root_bottom_up(leaves: Sequence[NmtNode]) -> NmtNode:
    """Standard bottom-up Merkle build with power-of-two padding."""
    if not leaves:
        # Root of an empty tree is a single padding leaf
        return nmt_leaf(PADDING_NAMESPACE, b"")

    level = _pad_to_pow2(list(leaves))
    while len(level) > 1:
        nxt: List[NmtNode] = []
        it = iter(level)
        for l in it:
            r = next(it)
            nxt.append(nmt_parent(l, r))
        level = nxt
    return level[0]


class NmtStreamingBuilder:
    """
    Incremental/streaming NMT builder.

    The reference implementation stores the pushed leaves and reuses the
    bottom-up constructor on finalize to guarantee identical padding and
    namespace aggregation behavior.
    """

    def __init__(self):
        self.leaves: List[NmtNode] = []

    def push(self, leaf: NmtNode) -> None:
        self.leaves.append(leaf)

    def finalize(self) -> NmtNode:
        return build_nmt_root_bottom_up(self.leaves)


# -------------------------- Erasure (replication) -----------------------------


def repeat_2x(leaves: Sequence[Tuple[int, bytes]]) -> List[Tuple[int, bytes]]:
    """
    Trivial (n,k)=(2,1) repetition code: duplicate each leaf in-place.
    This is *only for unit testing* of the commitment math (NMT behavior).
    """
    out: List[Tuple[int, bytes]] = []
    for ns, data in leaves:
        out.append((ns, data))
        out.append((ns, data))  # duplicate
    return out


# ---------------------------------- Tests -------------------------------------


def _mk_leaves() -> List[Tuple[int, bytes]]:
    # 6 leaves across 3 namespaces
    base = [
        (1, b"alpha"),
        (1, b"beta"),
        (2, b"gamma"),
        (2, b"delta"),
        (3, b"epsilon"),
        (3, b"zeta"),
    ]
    return base


def _to_nodes(leaves: Iterable[Tuple[int, bytes]]) -> List[NmtNode]:
    return [nmt_leaf(ns, data) for ns, data in leaves]


def _hex(b: bytes) -> str:
    return b.hex()


def test_nmt_root_matches_streaming_with_erasure_replication():
    """Bottom-up vs streaming roots must match on the extended (erasure-coded) set."""
    original = _mk_leaves()
    extended = repeat_2x(original)

    nodes = _to_nodes(extended)

    bottom_up = build_nmt_root_bottom_up(nodes)

    # streaming build
    sb = NmtStreamingBuilder()
    for n in nodes:
        sb.push(n)
    streaming_root = sb.finalize()

    assert _hex(bottom_up.digest) == _hex(streaming_root.digest)
    assert bottom_up.ns_min == streaming_root.ns_min
    assert bottom_up.ns_max == streaming_root.ns_max


def test_nmt_namespace_range_aggregates_correctly():
    """Root ns_min/ns_max should reflect the min/max of the input namespaces."""
    original = _mk_leaves()
    extended = repeat_2x(original)
    nodes = _to_nodes(extended)

    root = build_nmt_root_bottom_up(nodes)
    assert root.ns_min == min(ns for ns, _ in extended)
    assert root.ns_max == max(ns for ns, _ in extended)


def test_padding_to_power_of_two_is_stable():
    """
    Building with implicit padding vs explicit pre-padding to the same size
    yields the same root.
    """
    original = _mk_leaves()
    extended = repeat_2x(original)  # 12 leaves -> next pow2 is 16
    nodes = _to_nodes(extended)

    # Implicit padding
    root_implicit = build_nmt_root_bottom_up(nodes)

    # Explicit padding by caller (same padding rule)
    padded = _pad_to_pow2(nodes.copy())
    root_explicit = build_nmt_root_bottom_up(padded)

    assert _hex(root_implicit.digest) == _hex(root_explicit.digest)
    assert (root_implicit.ns_min, root_implicit.ns_max) == (
        root_explicit.ns_min,
        root_explicit.ns_max,
    )


@pytest.mark.parametrize(
    "ns,data",
    [
        (0, b""),
        (42, b"\x00" * 1),
        (42, b"\x00" * 32),
        (U64_MAX - 1, b"payload"),
    ],
)
def test_leaf_hash_domain_separation(ns: int, data: bytes):
    """
    Ensure leaf hashing uses a distinct domain tag and encodes both ns_min/max.
    Changing the namespace or domain must change the digest.
    """
    a = nmt_leaf(ns, data)
    b = nmt_leaf(ns, data)  # same
    assert a.digest == b.digest

    # Different namespace must change
    c = nmt_leaf((ns + 1) & U64_MAX, data)
    assert a.digest != c.digest

    # Different payload must change
    d = nmt_leaf(ns, data + b"\x01")
    assert a.digest != d.digest

    # Spot check: parent domain differs from leaf domain
    p = nmt_parent(a, b)
    assert p.digest != a.digest
