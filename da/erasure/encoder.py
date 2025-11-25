"""
Animica • DA • Erasure — Encoder
Pipeline: raw blob → (data shards → RS parity) → namespaced NMT leaves.

This module ties together:
  • partitioning a blob into fixed-size *data shards* (padding last stripe),
  • computing systematic Reed–Solomon parity shards per stripe,
  • wrapping both data & parity shards into canonical *namespaced leaves*
    for the Namespaced Merkle Tree (NMT) layer.

Ordering (canonical)
--------------------
For each stripe, leaves are emitted in this exact order:

    [ data_0, data_1, ..., data_{k-1}, parity_0, parity_1, ..., parity_{p-1} ]

where:
  k = params.data_shards
  p = params.parity_shards
  n = params.total_shards = k + p

Data leaves embed only the meaningful portion of the shard (no right-padding).
Parity leaves embed the full `share_bytes` payload (always fixed-size).

Public API
----------
- encode_blob_to_leaves(blob, namespace, params=DEFAULT_PARAMS)
      -> (leaves: List[bytes], info: ErasureEncodeInfo)

The returned `leaves` can be fed directly into the NMT builder. The companion
decoder lives in `da.erasure.decoder` (blob recovery from any k leaves + proofs).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Sequence, Tuple

from .params import ErasureParams, DEFAULT_PARAMS
from .partitioner import partition_blob, DataShard
from .reedsolomon import rs_encode
from ..nmt.namespace import normalize_namespace
from ..nmt.codec import encode_leaf


# --------------------------------------------------------------------------- #
# Metadata returned by the encoder
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class ErasureEncodeInfo:
    """
    Describes how a blob was encoded into leaves.
    """
    params: ErasureParams
    namespace: bytes               # normalized namespace (fixed length)
    original_size: int             # input blob length in bytes
    stripes: int                   # number of stripes
    share_bytes: int               # bytes per shard payload
    data_per_stripe: int           # k
    parity_per_stripe: int         # p
    leaves_per_stripe: int         # n = k + p
    total_data_shards: int         # stripes * k
    total_parity_shards: int       # stripes * p
    total_leaves: int              # stripes * n
    data_meaningful_lengths: List[int]  # len per data shard (for final stripe mostly)

    def leaf_index(self, stripe: int, position: int) -> int:
        """
        Convert (stripe, position) -> global leaf index, where position ∈ [0, n).
        """
        n = self.leaves_per_stripe
        if not (0 <= stripe < self.stripes):
            raise IndexError("stripe out of range")
        if not (0 <= position < n):
            raise IndexError("position out of range")
        return stripe * n + position


# --------------------------------------------------------------------------- #
# Encoder
# --------------------------------------------------------------------------- #

def _group_by_stripe(items: Sequence[DataShard], k: int) -> List[List[DataShard]]:
    """
    Turn a flat list of DataShard (already in stripe order) into
    [[stripe0 shards...], [stripe1 shards...], ...] each of length k.
    """
    if not items:
        return []
    if len(items) % k != 0:
        raise ValueError("data shard count not divisible by k")
    return [list(items[i:i + k]) for i in range(0, len(items), k)]


def encode_blob_to_leaves(
    blob: bytes,
    namespace: bytes,
    params: ErasureParams = DEFAULT_PARAMS,
) -> Tuple[List[bytes], ErasureEncodeInfo]:
    """
    Encode `blob` into namespaced NMT leaves using erasure coding parameters.

    Args:
        blob:      raw payload to be committed via DA.
        namespace: namespace identifier for the blob (bytes); see
                   `da.nmt.namespace.normalize_namespace` for accepted forms.
        params:    erasure coding profile; defaults to `DEFAULT_PARAMS`.

    Returns:
        (leaves, info)
          leaves: list of byte-encoded NMT leaves in canonical per-stripe order.
          info:   ErasureEncodeInfo with sizes/counts and helpers.

    Raises:
        TypeError, ValueError on invalid inputs.
    """
    if not isinstance(blob, (bytes, bytearray, memoryview)):
        raise TypeError("blob must be a bytes-like object")

    ns = normalize_namespace(namespace)
    k = params.data_shards
    p = params.parity_shards
    n = params.total_shards
    B = params.share_bytes

    # Partition into fixed-size data shards (k per stripe)
    data_shards = partition_blob(bytes(blob), params)  # List[DataShard]
    stripes = 0 if not data_shards else (len(data_shards) // k)
    grouped = _group_by_stripe(data_shards, k)

    # Metadata: track meaningful lengths for data shards (mostly affects last stripe)
    data_lengths: List[int] = [s.data_len for s in data_shards]

    # Prepare output leaf list with known capacity
    leaves: List[bytes] = []
    leaves_per_stripe = n

    # Process each stripe: emit data leaves, compute parity leaves, emit parity
    for stripe_shards in grouped:
        # 1) Data leaves (meaningful portion only)
        for s in stripe_shards:
            body = s.payload[: s.data_len] if s.data_len <= B else s.payload
            leaves.append(encode_leaf(ns, body))

        # 2) Parity leaves (full-precision; always B bytes)
        #    Build the parity from the stripe's data payloads (exactly B per shard).
        data_payloads = [s.payload for s in stripe_shards]  # each length B
        parity_payloads = rs_encode(data_payloads, params) if p > 0 else []

        for parity in parity_payloads:
            # Parity leaves carry the full B payload to remain deterministic/stable.
            if len(parity) != B:
                raise AssertionError("internal: parity shard size mismatch")
            leaves.append(encode_leaf(ns, parity))

    # Empty-blob case: return empty leaves with minimal metadata.
    if not data_shards:
        info = ErasureEncodeInfo(
            params=params,
            namespace=ns,
            original_size=0,
            stripes=0,
            share_bytes=B,
            data_per_stripe=k,
            parity_per_stripe=p,
            leaves_per_stripe=n,
            total_data_shards=0,
            total_parity_shards=0,
            total_leaves=0,
            data_meaningful_lengths=[],
        )
        return [], info

    # Construct info block
    info = ErasureEncodeInfo(
        params=params,
        namespace=ns,
        original_size=len(blob),
        stripes=stripes,
        share_bytes=B,
        data_per_stripe=k,
        parity_per_stripe=p,
        leaves_per_stripe=n,
        total_data_shards=stripes * k,
        total_parity_shards=stripes * p,
        total_leaves=stripes * n,
        data_meaningful_lengths=data_lengths,
    )
    # Sanity: count matches
    if len(leaves) != info.total_leaves:
        raise AssertionError("internal: leaf count mismatch")

    return leaves, info


__all__ = [
    "ErasureEncodeInfo",
    "encode_blob_to_leaves",
]
