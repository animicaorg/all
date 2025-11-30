"""
Animica • DA • Erasure — Partitioner
Split a raw blob into fixed-size *data shards* (payload slices) suitable for
Reed–Solomon encoding, while preserving each slice's *meaningful* length for
namespaced-leaf encoding in the NMT layer.

This module is dependency-light and purely about chunking:
  • You give it a `bytes` blob and `ErasureParams`.
  • It returns fixed-size payload slices of length `share_bytes` (k per stripe),
    each annotated with the number of meaningful bytes (without right-padding).
  • Optional helpers can turn those slices into namespaced NMT leaves by
    calling into `da.nmt.codec` lazily (so importing this module is cheap).

Parity shards are NOT produced here; see `da.erasure.encoder` for the full
blob → (data+parity) → namespaced-leaves pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Generator, Iterable, Iterator, List, Sequence, Tuple

from .params import DEFAULT_PARAMS, ErasureParams

# --------------------------------------------------------------------------- #
# Model
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class DataShard:
    """
    One fixed-size payload slice to feed the RS encoder.

    Attributes:
      index:       0-based index among *data* shards (k per stripe, then next stripe)
      payload:     bytes of length exactly `share_bytes` (right-padded with zeros if needed)
      data_len:    number of meaningful bytes in `payload` (<= share_bytes)
      stripe:      which stripe this shard belongs to (0-based)
      offset_in_stripe: 0..k-1
      is_padding:  True iff data_len == 0 (pure right padding beyond blob length)
    """

    index: int
    payload: bytes
    data_len: int
    stripe: int
    offset_in_stripe: int
    is_padding: bool


# --------------------------------------------------------------------------- #
# Core API
# --------------------------------------------------------------------------- #


def partition_blob(blob: bytes, params: ErasureParams) -> List[DataShard]:
    """
    Split `blob` into fixed-size *data shards* of length `params.share_bytes`,
    arranged in stripes of `params.data_shards` shards each. The final stripe
    is right-padded with zeros as needed.

    Returns a list of DataShard (length = stripes * k). If `blob` is empty,
    returns an empty list.
    """
    if not isinstance(blob, (bytes, bytearray, memoryview)):
        raise TypeError("blob must be a bytes-like object")
    blob_mv = memoryview(blob)
    k = params.data_shards
    B = params.share_bytes

    if len(blob_mv) == 0:
        return []

    stripes = params.stripes_for_blob(len(blob_mv))
    shards: List[DataShard] = []
    shard_index = 0
    pos = 0

    for stripe in range(stripes):
        for off in range(k):
            # Remaining payload in this shard
            remaining = len(blob_mv) - pos
            if remaining <= 0:
                data_len = 0
                payload = bytes(B)  # all zeros (pure padding)
                is_padding = True
            else:
                data_len = B if remaining >= B else remaining
                # Slice the meaningful part and right-pad to B
                meaningful = blob_mv[pos : pos + data_len].tobytes()
                if data_len < B:
                    payload = meaningful + bytes(B - data_len)
                else:
                    payload = meaningful
                is_padding = data_len == 0

            shards.append(
                DataShard(
                    index=shard_index,
                    payload=payload,
                    data_len=data_len,
                    stripe=stripe,
                    offset_in_stripe=off,
                    is_padding=is_padding,
                )
            )
            shard_index += 1
            pos += data_len

    return shards


# --------------------------------------------------------------------------- #
# Namespaced leaf helpers (optional; lazy import to avoid heavy deps)
# --------------------------------------------------------------------------- #


def make_namespaced_leaves(
    shards: Sequence[DataShard],
    namespace: bytes,
) -> List[bytes]:
    """
    Turn *data shards* into namespaced NMT leaves using the canonical
    leaf encoding (namespace || u16(len) || data). Only the *meaningful*
    portion `payload[:data_len]` is embedded; the right-padding zeros are
    not serialized into the leaf body.

    NOTE: Parity shards are constructed later in `da.erasure.encoder` and
    should also be wrapped as leaves (typically with the same namespace or
    a reserved parity namespace depending on policy).
    """
    # Lazy import to keep this module standalone for most uses
    try:
        from da.nmt.codec import encode_leaf  # type: ignore
        from da.nmt.namespace import normalize_namespace  # type: ignore
    except Exception as e:  # pragma: no cover - import-time guard
        raise RuntimeError(
            "da.nmt.codec/namespace not available; cannot build namespaced leaves"
        ) from e

    ns = normalize_namespace(namespace)
    leaves: List[bytes] = []
    for s in shards:
        body = s.payload[: s.data_len] if s.data_len <= len(s.payload) else s.payload
        leaves.append(encode_leaf(ns, body))
    return leaves


# --------------------------------------------------------------------------- #
# Utilities
# --------------------------------------------------------------------------- #


def data_shard_count_for_blob(blob_len: int, params: ErasureParams) -> int:
    """
    Total number of *data* shards (k per stripe) required to carry a blob
    of length `blob_len`.
    """
    if blob_len < 0:
        raise ValueError("blob_len must be non-negative")
    return params.stripes_for_blob(blob_len) * params.data_shards


__all__ = [
    "DataShard",
    "partition_blob",
    "make_namespaced_leaves",
    "data_shard_count_for_blob",
    "partition",
]


# Compatibility aliases ---------------------------------------------------------------


def partition(
    blob: bytes, namespace: bytes
) -> List[bytes]:  # pragma: no cover - thin wrapper
    """Convenience alias used by some legacy callers/tests."""
    shards = partition_blob(blob, DEFAULT_PARAMS)
    return make_namespaced_leaves(shards, namespace)
