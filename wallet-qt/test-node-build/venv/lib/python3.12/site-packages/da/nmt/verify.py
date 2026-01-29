"""
Animica • DA • NMT — Proof verification (inclusion & range)

This module verifies Namespaced Merkle Tree (NMT) proofs against a claimed
root. It supports:

  • verify_inclusion(...)          — single-leaf membership
  • verify_range(...)              — contiguous span (requires per-leaf namespaces)
  • verify_namespace_range(...)    — span where all leaves share one namespace
  • verify_inclusion_from_encoded(...)
  • verify_range_from_encoded(...)
  • verify_namespace_range_from_encoded(...)

Design notes
------------
• We reconstruct the Merkle path bottom-up using the same leaf/parent hashing
  used by the NMT builder. We import leaf/parent helpers from da.nmt.node and
  fall back to local equivalents if necessary.

• Sibling ordering is enforced: at every level, the left subtree must have a
  namespace max <= the right subtree namespace min. Violations reject the proof.

• Range proofs are compact multi-proofs emitted by da.nmt.proofs.build_range /
  build_namespace_range. Verification mirrors that construction.

Return value & errors
---------------------
All `verify_*` functions return a boolean True/False. Use the `*_checked`
variants (internal) to raise NMTVerifyError on failure if you need diagnostics.
"""

from __future__ import annotations

import hmac
from dataclasses import dataclass
from typing import (Dict, Iterable, Iterator, List, Mapping, MutableMapping,
                    Optional, Sequence, Tuple, Union)

from ..utils.hash import sha3_256
from . import codec
from .namespace import NamespaceId, NamespaceRange
from .node import Node  # structural container (hash, ns_min, ns_max)
from .proofs import InclusionProof, RangeProof, SiblingStep

# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #


class NMTVerifyError(ValueError):
    """Raised when a proof fails verification with details."""


ROOT_SIZE = 32


# --------------------------------------------------------------------------- #
# Resolve hashing helpers from da.nmt.node (with safe fallbacks)
# --------------------------------------------------------------------------- #

# We try a few likely export names to remain robust to refactors.
_leaf_fn = None
_parent_fn = None

try:
    from .node import make_leaf as _leaf_fn  # type: ignore[attr-defined]
except Exception:
    try:
        from .node import leaf as _leaf_fn  # type: ignore[attr-defined]
    except Exception:
        try:
            from .node import \
                leaf_node as _leaf_fn  # type: ignore[attr-defined]
        except Exception:
            _leaf_fn = None

try:
    from .node import parent as _parent_fn  # type: ignore[attr-defined]
except Exception:
    try:
        from .node import \
            parent_node as _parent_fn  # type: ignore[attr-defined]
    except Exception:
        try:
            from .node import \
                combine as _parent_fn  # type: ignore[attr-defined]
        except Exception:
            try:
                from .node import \
                    merge as _parent_fn  # type: ignore[attr-defined]
            except Exception:
                try:
                    from .node import \
                        hash_parent as _parent_fn  # type: ignore[attr-defined]
                except Exception:
                    _parent_fn = None


def _fallback_leaf(ns: NamespaceId, payload_hash32: bytes) -> Node:
    """
    Local leaf hasher if da.nmt.node helpers are unavailable.

    Hash = sha3_256(b"NMT:leaf" || ns_be || payload_hash)
    Node.ns_min == Node.ns_max == ns
    """
    if len(payload_hash32) != 32:
        raise NMTVerifyError("leaf payload hash must be 32 bytes")
    ns_be = int(ns).to_bytes((ns.bit_length() + 7) // 8 or 1, "big")
    h = sha3_256(b"NMT:leaf" + ns_be + payload_hash32)
    return Node(hash=h, ns_min=ns, ns_max=ns)


def _fallback_parent(left: Node, right: Node) -> Node:
    """
    Local parent hasher if da.nmt.node helpers are unavailable.

    Enforces namespace ordering: left.ns_max <= right.ns_min.

    Hash = sha3_256(b"NMT:node" || left.hash || right.hash
                    || ns_min_be || ns_max_be)
    where ns_min/max are the merged range across children.
    """
    if int(left.ns_max) > int(right.ns_min):
        raise NMTVerifyError("namespace ordering violated (left.max > right.min)")
    ns_min = left.ns_min
    ns_max = right.ns_max
    # Big-endian encodes sized to the larger of the two child widths (at least 1)
    width = max(
        (ns_min.bit_length() + 7) // 8 or 1,
        (ns_max.bit_length() + 7) // 8 or 1,
    )
    h = sha3_256(
        b"NMT:node"
        + left.hash
        + right.hash
        + int(ns_min).to_bytes(width, "big")
        + int(ns_max).to_bytes(width, "big")
    )
    return Node(hash=h, ns_min=ns_min, ns_max=ns_max)


def _leaf(ns: NamespaceId, payload_hash32: bytes) -> Node:
    if _leaf_fn is not None:
        return _leaf_fn(ns, payload_hash32)  # type: ignore[misc]
    return _fallback_leaf(ns, payload_hash32)


def _parent(left: Node, right: Node) -> Node:
    if _parent_fn is not None:
        return _parent_fn(left, right)  # type: ignore[misc]
    return _fallback_parent(left, right)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _ct_eq(a: bytes, b: bytes) -> bool:
    return hmac.compare_digest(a, b)


def _ns_obj(x: Union[int, NamespaceId]) -> NamespaceId:
    return x if isinstance(x, NamespaceId) else NamespaceId(int(x))


def _node_from_sibling_step(step: SiblingStep) -> Node:
    return Node(hash=step.hash, ns_min=step.ns_min, ns_max=step.ns_max)


# --------------------------------------------------------------------------- #
# Inclusion proof (single leaf)
# --------------------------------------------------------------------------- #


def _reconstruct_inclusion_root(
    proof: InclusionProof,
    *,
    leaf_ns: Optional[Union[int, NamespaceId]] = None,
    leaf_payload_hash: Optional[bytes] = None,
) -> bytes:
    ns = _ns_obj(leaf_ns if leaf_ns is not None else proof.leaf_ns)
    ph = leaf_payload_hash if leaf_payload_hash is not None else proof.leaf_payload_hash
    if not ph or len(ph) != 32:
        raise NMTVerifyError("missing or invalid 32-byte leaf payload hash")

    cur = _leaf(ns, ph)
    # Walk up the path
    for idx, step in enumerate(proof.siblings):
        sib = _node_from_sibling_step(step)
        if step.side == "R":
            cur = _parent(cur, sib)
        elif step.side == "L":
            cur = _parent(sib, cur)
        else:
            raise NMTVerifyError(
                f"bad sibling side at level {step.level}: {step.side!r}"
            )
        # Optional: enforce level monotonicity if present
        if step.level != idx:
            # We tolerate mismatches but still enforce non-decreasing levels.
            if step.level < idx:
                raise NMTVerifyError("sibling steps out of order (level decreased)")

    return cur.hash


def verify_inclusion(
    root: bytes,
    proof: InclusionProof,
    *,
    leaf_payload_hash: Optional[bytes] = None,
    leaf_ns: Optional[Union[int, NamespaceId]] = None,
) -> bool:
    """
    Verify a single-leaf inclusion proof.

    Supply either `leaf_payload_hash` (preferred), or rely on the value embedded
    in the proof (proof.leaf_payload_hash).
    """
    try:
        if len(root) != ROOT_SIZE:
            raise NMTVerifyError("root must be 32 bytes")
        cand = _reconstruct_inclusion_root(
            proof, leaf_ns=leaf_ns, leaf_payload_hash=leaf_payload_hash
        )
        return _ct_eq(cand, root)
    except Exception:
        return False


def verify_inclusion_from_encoded(
    root: bytes, proof: InclusionProof, encoded_leaf: bytes
) -> bool:
    """
    Verify inclusion using a fully encoded leaf (ns||len||data).
    """
    try:
        ns, payload = codec.decode_leaf(encoded_leaf)
        ph = sha3_256(
            codec.write_uvarint(len(payload)) + payload
        )  # not exported; compute directly
    except AttributeError:
        # write_uvarint isn't exported; use helper that hashes payload portion directly
        ph = codec.payload_hash_from_encoded(encoded_leaf)
        ns, _ = codec.decode_leaf(encoded_leaf)
    try:
        cand = _reconstruct_inclusion_root(proof, leaf_ns=ns, leaf_payload_hash=ph)
        if len(root) != ROOT_SIZE:
            raise NMTVerifyError("root must be 32 bytes")
        return _ct_eq(cand, root)
    except Exception:
        return False


# --------------------------------------------------------------------------- #
# Range proof (contiguous span)
# --------------------------------------------------------------------------- #


def _prepare_ns_per_leaf(
    count: int,
    leaf_namespaces: Optional[
        Union[int, NamespaceId, Sequence[Union[int, NamespaceId]]]
    ],
    ns_range: NamespaceRange,
) -> List[NamespaceId]:
    if leaf_namespaces is None:
        # Require a degenerate namespace range (typical for namespace-range proofs).
        if int(ns_range.ns_min) != int(ns_range.ns_max):
            raise NMTVerifyError(
                "per-leaf namespaces required for mixed-namespace ranges"
            )
        return [ns_range.ns_min] * count
    if isinstance(leaf_namespaces, (int, NamespaceId)):
        return [_ns_obj(leaf_namespaces)] * count
    if len(leaf_namespaces) != count:
        raise NMTVerifyError("leaf_namespaces length must equal proof.count")
    return [_ns_obj(x) for x in leaf_namespaces]


def _reconstruct_range_root(
    root: bytes,
    proof: RangeProof,
    leaf_payload_hashes: Sequence[bytes],
    *,
    leaf_namespaces: Optional[
        Union[int, NamespaceId, Sequence[Union[int, NamespaceId]]]
    ] = None,
) -> bytes:
    if any(len(h) != 32 for h in leaf_payload_hashes):
        raise NMTVerifyError("all leaf payload hashes must be 32 bytes")
    if proof.count != len(leaf_payload_hashes):
        raise NMTVerifyError(
            "proof.count does not match number of provided leaf hashes"
        )

    # Build the active map of indices -> Node for the covered span at level 0.
    ns_per_leaf = _prepare_ns_per_leaf(proof.count, leaf_namespaces, proof.ns_range)
    active: Dict[int, Node] = {}
    for k, ph in enumerate(leaf_payload_hashes):
        idx = proof.start + k
        active[idx] = _leaf(ns_per_leaf[k], ph)

    # Iterator over provided sibling steps.
    steps = list(proof.siblings)
    step_i = 0

    # Ascend levels until we consumed all siblings and condensed to a single node.
    while True:
        # If we've consumed all siblings and reduced to a single node, we’re done.
        if step_i >= len(steps) and len(active) == 1:
            # return the only node hash
            return next(iter(active.values())).hash

        # Merge this level into parents.
        next_active: Dict[int, Node] = {}
        visited: set[int] = set()

        for i in sorted(active.keys()):
            if i in visited:
                continue

            # Determine expected sibling index and side for i.
            if i % 2 == 0:
                j = i + 1
                side = "R"
            else:
                j = i - 1
                side = "L"

            left_first = None  # type: Optional[Tuple[Node, Node]]

            if j in active:
                # Both children present in the active set.
                if i < j:
                    pair = (active[i], active[j])
                else:
                    pair = (active[j], active[i])
                visited.add(i)
                visited.add(j)
                parent_index = min(i, j) // 2
                left_first = pair
            else:
                # Need a sibling from the proof stream.
                if step_i >= len(steps):
                    raise NMTVerifyError("ran out of sibling steps while reducing")
                step = steps[step_i]
                step_i += 1
                if step.side != side:
                    raise NMTVerifyError(
                        f"sibling side mismatch at level {step.level} (expected {side}, got {step.side})"
                    )
                sib = _node_from_sibling_step(step)
                if side == "R":
                    pair = (active[i], sib)  # current is left, sibling right
                else:
                    pair = (sib, active[i])  # sibling left, current right
                visited.add(i)
                parent_index = i // 2
                left_first = pair

            # Combine to parent and carry upward.
            parent = _parent(left_first[0], left_first[1])
            next_active[parent_index] = parent

        active = next_active
        # next level

    # Unreachable
    # return b""


def verify_range(
    root: bytes,
    proof: RangeProof,
    leaf_payload_hashes: Sequence[bytes],
    *,
    leaf_namespaces: Optional[
        Union[int, NamespaceId, Sequence[Union[int, NamespaceId]]]
    ] = None,
) -> bool:
    """
    Verify a contiguous-span range proof.

    For general spans that may include multiple namespaces, you MUST supply
    `leaf_namespaces` as a sequence (one per leaf). If the span is known to be
    a single-namespace range, you may omit it or pass a scalar.
    """
    try:
        if len(root) != ROOT_SIZE:
            raise NMTVerifyError("root must be 32 bytes")
        cand = _reconstruct_range_root(
            root, proof, leaf_payload_hashes, leaf_namespaces=leaf_namespaces
        )
        return _ct_eq(cand, root)
    except Exception:
        return False


def verify_namespace_range(
    root: bytes,
    proof: RangeProof,
    leaf_payload_hashes: Sequence[bytes],
    *,
    namespace: Optional[Union[int, NamespaceId]] = None,
) -> bool:
    """
    Convenience wrapper when all leaves share the same namespace.
    If `namespace` is omitted, we require proof.ns_range to be degenerate.
    """
    ns_arg: Optional[Union[int, NamespaceId, Sequence[Union[int, NamespaceId]]]]
    if namespace is None:
        # Let _prepare_ns_per_leaf infer from proof.ns_range (must be degenerate).
        ns_arg = None
    else:
        ns_arg = _ns_obj(namespace)
    return verify_range(root, proof, leaf_payload_hashes, leaf_namespaces=ns_arg)


# --------------------------------------------------------------------------- #
# Encoded-leaf convenience wrappers
# --------------------------------------------------------------------------- #


def _payload_hashes_from_encoded(
    encoded_leaves: Sequence[bytes],
) -> Tuple[List[NamespaceId], List[bytes]]:
    ns_list: List[NamespaceId] = []
    ph_list: List[bytes] = []
    for enc in encoded_leaves:
        ns, _payload = codec.decode_leaf(enc)
        ph = codec.payload_hash_from_encoded(enc)
        ns_list.append(ns)
        ph_list.append(ph)
    return ns_list, ph_list


def verify_range_from_encoded(
    root: bytes, proof: RangeProof, encoded_leaves: Sequence[bytes]
) -> bool:
    """
    Verify a range proof where the caller supplies the exact concatenated leaf
    encodings for the covered span.
    """
    try:
        ns_list, ph_list = _payload_hashes_from_encoded(encoded_leaves)
        return verify_range(root, proof, ph_list, leaf_namespaces=ns_list)
    except Exception:
        return False


def verify_namespace_range_from_encoded(
    root: bytes, proof: RangeProof, encoded_leaves: Sequence[bytes]
) -> bool:
    """
    Verify a namespace-range proof using encoded leaves. Requires all leaves to
    share one namespace (will be checked).
    """
    try:
        ns_list, ph_list = _payload_hashes_from_encoded(encoded_leaves)
        # Ensure all namespaces equal
        if any(int(ns) != int(ns_list[0]) for ns in ns_list):
            return False
        return verify_range(root, proof, ph_list, leaf_namespaces=ns_list[0])
    except Exception:
        return False


__all__ = [
    "NMTVerifyError",
    "verify_inclusion",
    "verify_inclusion_from_encoded",
    "verify_range",
    "verify_range_from_encoded",
    "verify_namespace_range",
    "verify_namespace_range_from_encoded",
    "ROOT_SIZE",
]
