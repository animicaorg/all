from __future__ import annotations

"""
Animica • DA • Core-Chain Adapter
=================================

Compute and validate the **DA root** used in block headers.

Two canonical modes are supported:

1) **leaves** mode (preferred): concatenate *all* namespaced leaves from each
   blob (in the exact order they are included in the block) and compute a single
   Namespaced Merkle Tree (NMT) root over that full leaf stream.

2) **commitments** mode (fallback): if only per-blob commitments are available
   at block-build time, compute an NMT root where each *leaf* is the 32-byte
   commitment of the blob (leaf namespace = the blob's namespace). This mode
   provides inclusion binding for which blobs were referenced, but does not
   expose share-level sampling indices. It is deterministic and suitable for
   dev/test networks or light blocks.

The mode should be chosen network-wide via policy/params; this adapter supports
both and leaves selection to the caller.

Usage (block builder)
---------------------
    from da.adapters.core_chain import (
        BlobInclusion, compute_da_root, validate_da_root
    )

    inclusions = [
        BlobInclusion(namespace=24, commitment=bytes.fromhex("..."),
                      size=12345, leaves=blob1_leaves),   # encoded with da.nmt.codec
        BlobInclusion(namespace=42, commitment=bytes.fromhex("..."),
                      size=512000, leaves=None),          # no leaves available
    ]

    # Auto picks 'leaves' only if *every* item has leaves; otherwise 'commitments'
    da_root = compute_da_root(inclusions, mode="auto")

    header.da_root = da_root  # set before hashing/signing the header

    # After assembling the block:
    validate_da_root(header_da_root=header.da_root, inclusions=inclusions, mode="auto")

Definitions
----------
- Commitment: Per-blob NMT root computed over that blob's own namespaced leaves.
  (See da/blob/commitment.py and da/nmt/commit.py.)
- Leaf encoding: `da.nmt.codec.leaf_encode(namespace_id: int, data: bytes)` produces
  canonical `namespace || len || data` bytes for NMT leaves.

Determinism & Ordering
----------------------
Block authors MUST pass inclusions in the exact order they are serialized in the
block body. This function will not sort. Deterministic roots require that all
nodes reconstruct leaves in the same order when validating the block.

Light Clients
-------------
Light clients that perform DAS over *shares* require the header DA root to be
computed in **leaves** mode. Using **commitments** mode is acceptable for early
milestones and devnets that do not ship sampling; production networks should
prefer **leaves**.

"""

from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence, Tuple, Union

from da.errors import DAError
from da.utils.bytes import hex_to_bytes, bytes_to_hex
from da.utils.merkle import merkle_root
from da.utils.hash import sha3_256
from da.nmt.codec import leaf_encode
from da.nmt.commit import compute_nmt_root  # root over encoded leaves


# --------------------------------------------------------------------------------------
# Data model for block DA inclusions
# --------------------------------------------------------------------------------------

@dataclass(frozen=True)
class BlobInclusion:
    """
    Description of a blob included in a block.

    Attributes:
        namespace: 32-bit namespace id of the blob.
        commitment: 32-byte per-blob commitment (NMT root over that blob's own leaves).
        size: Size of the original blob in bytes (for metadata only).
        leaves: Optional list/iterator of leaf-encoded bytes (namespace||len||data)
                for this blob. If provided for *all* blobs, the adapter can build
                a single block-wide NMT over shares ("leaves" mode).
    """
    namespace: int
    commitment: bytes
    size: int
    leaves: Optional[Sequence[bytes]] = None


# --------------------------------------------------------------------------------------
# Root computation
# --------------------------------------------------------------------------------------

def compute_da_root(
    inclusions: Iterable[BlobInclusion],
    *,
    mode: str = "auto",  # "auto" | "leaves" | "commitments"
) -> bytes:
    """
    Compute the DA root for a set of blob inclusions in block order.

    - mode="leaves":   requires every inclusion.leaves to be non-None.
    - mode="commitments": builds an NMT over per-blob commitments as leaves.
    - mode="auto":     uses "leaves" iff all items have leaves, else "commitments".
    """
    items = list(inclusions)

    if mode not in ("auto", "leaves", "commitments"):
        raise DAError(f"Unknown DA root mode: {mode}")

    if mode == "auto":
        mode = "leaves" if all(i.leaves is not None for i in items) else "commitments"

    if mode == "leaves":
        if not items:
            # Empty block; root of empty tree = hash of empty string under sha3_256 by convention
            return sha3_256(b"")
        # Concatenate all already-encoded leaves in *block order*
        flat_leaves: List[bytes] = []
        for i, inc in enumerate(items):
            if inc.leaves is None:
                raise DAError("leaves mode requires leaves for all inclusions")
            # We assume each `inc.leaves` entry is already `leaf_encode(ns, data)`; if not,
            # callers should encode using da.nmt.codec before passing.
            flat_leaves.extend(inc.leaves)
        return compute_nmt_root(flat_leaves)

    # commitments mode
    if not items:
        return sha3_256(b"")
    # Build NMT leaves where the "data" is the 32-byte commitment itself.
    enc: List[bytes] = []
    for inc in items:
        if not isinstance(inc.commitment, (bytes, bytearray)) or len(inc.commitment) == 0:
            raise DAError("Invalid commitment in inclusion")
        enc.append(leaf_encode(inc.namespace, bytes(inc.commitment)))
    return compute_nmt_root(enc)


# --------------------------------------------------------------------------------------
# Validation helpers
# --------------------------------------------------------------------------------------

def _as_bytes(x: Union[bytes, bytearray, str]) -> bytes:
    if isinstance(x, (bytes, bytearray)):
        return bytes(x)
    if isinstance(x, str):
        return hex_to_bytes(x)
    raise TypeError("header_da_root must be bytes or 0x-hex str")

def validate_da_root(
    *,
    header_da_root: Union[bytes, bytearray, str],
    inclusions: Iterable[BlobInclusion],
    mode: str = "auto",
) -> None:
    """
    Recompute DA root from inclusions and compare against the header value.

    Raises DAError on mismatch. Returns None on success.
    """
    expected = compute_da_root(inclusions, mode=mode)
    got = _as_bytes(header_da_root)
    if got != expected:
        raise DAError(
            f"DA root mismatch: header={bytes_to_hex(got)} expected={bytes_to_hex(expected)} (mode={mode})"
        )


# --------------------------------------------------------------------------------------
# Convenience builders
# --------------------------------------------------------------------------------------

def build_inclusions_from_commitments(
    entries: Iterable[Tuple[int, Union[bytes, str], int]],
) -> List[BlobInclusion]:
    """
    Convenience: create BlobInclusion list from (namespace, commitment, size) tuples.
    `commitment` may be bytes or 0x-hex str. `leaves` will be None.
    """
    out: List[BlobInclusion] = []
    for ns, commit, size in entries:
        if isinstance(commit, str):
            c = hex_to_bytes(commit)
        else:
            c = bytes(commit)
        out.append(BlobInclusion(namespace=int(ns), commitment=c, size=int(size), leaves=None))
    return out


def build_inclusions_from_leaves(
    entries: Iterable[Tuple[int, Union[bytes, str], int, Sequence[bytes]]],
) -> List[BlobInclusion]:
    """
    Convenience: create BlobInclusion list from (namespace, commitment, size, leaves) tuples.
    The `leaves` values must already be encoded using `da.nmt.codec.leaf_encode`.
    """
    out: List[BlobInclusion] = []
    for ns, commit, size, leaves in entries:
        if isinstance(commit, str):
            c = hex_to_bytes(commit)
        else:
            c = bytes(commit)
        out.append(BlobInclusion(namespace=int(ns), commitment=c, size=int(size), leaves=list(leaves)))
    return out


__all__ = [
    "BlobInclusion",
    "compute_da_root",
    "validate_da_root",
    "build_inclusions_from_commitments",
    "build_inclusions_from_leaves",
]
