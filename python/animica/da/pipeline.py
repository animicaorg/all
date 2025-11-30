from __future__ import annotations

import hashlib
from typing import Any, Dict, Iterable, List, Mapping, Union

from .blob_chunking import chunk_blob

BytesLike = Union[bytes, bytearray, memoryview]


def _to_bytes(data: BytesLike) -> bytes:
    if isinstance(data, bytes):
        return data
    if isinstance(data, bytearray):
        return bytes(data)
    if isinstance(data, memoryview):
        return data.tobytes()
    raise TypeError(f"expected bytes-like object, got {type(data)!r}")


def _compute_root(namespace: bytes, shards: Iterable[BytesLike]) -> bytes:
    """
    Compute a deterministic DA commitment root:

        root = sha256( namespace || for each shard: len(shard) || shard )

    This is a simple, well-defined commitment that is stable as long as:
      * namespace bytes do not change,
      * shard order / contents do not change.
    """
    if not isinstance(namespace, (bytes, bytearray)):
        raise TypeError("namespace must be bytes-like")
    ns = bytes(namespace)

    h = hashlib.sha256()
    h.update(ns)

    for shard in shards:
        s = _to_bytes(shard)
        # Include length to avoid ambiguity over concatenation boundaries.
        h.update(len(s).to_bytes(8, "big"))
        h.update(s)

    return h.digest()


def encode_blob_for_da(
    blob: BytesLike,
    *,
    namespace: BytesLike,
    chunk_size: int,
    data_shards: int,
    parity_shards: int,
) -> Dict[str, Any]:
    """
    High-level DA encoding:

        blob
          → chunk_blob(...)
          → list of shards
          → commitment root over (namespace, shards)
          → commitment dict with params and shards

    Notes:
      * For now, RS/NMT layout is simplified to just chunking; the low-level
        RS/NMT correctness is covered by the Rust tests in animica_native.
      * We still plumb through `data_shards` and `parity_shards` as params
        so future wiring to the native crate is straightforward.
    """
    if chunk_size <= 0:
        raise ValueError(f"chunk_size must be positive, got {chunk_size}")
    if data_shards <= 0:
        raise ValueError(f"data_shards must be positive, got {data_shards}")
    if parity_shards < 0:
        raise ValueError(f"parity_shards must be >= 0, got {parity_shards}")

    ns = _to_bytes(namespace)
    if len(ns) != 8:
        raise ValueError(
            f"namespace must be exactly 8 bytes for DA (got length {len(ns)})"
        )

    b = _to_bytes(blob)
    original_len = len(b)

    # Simple, deterministic chunking; future work can swap this out for a more
    # elaborate shard layout as long as the contract stays the same.
    shards: List[bytes] = chunk_blob(b, chunk_size=chunk_size)

    if original_len > 0 and not shards:
        raise RuntimeError("non-empty blob produced no shards (chunking bug)")

    root = _compute_root(ns, shards)

    params: Dict[str, Any] = {
        "chunk_size": chunk_size,
        "data_shards": data_shards,
        "parity_shards": parity_shards,
        # Needed for exact reconstruction of the original blob.
        "original_len": original_len,
    }

    commitment: Dict[str, Any] = {
        "namespace": ns,
        "root": root,
        "params": params,
        # We include shards here so a caller *can* store them inline,
        # but reconstruct_blob_from_da will accept external shards too.
        "shards": shards,
    }

    return commitment


def reconstruct_blob_from_da(
    commitment: Mapping[str, Any],
    shards: Iterable[BytesLike],
) -> bytes:
    """
    Reconstruct the original blob from a DA commitment and a set of shards.

    Steps:
      1. Extract namespace, root, and params (including original_len).
      2. Normalize shards to bytes and recompute the commitment root.
      3. If the root differs, raise an error (data corruption or mismatch).
      4. Concatenate shards and trim to original_len.
    """
    if (
        "namespace" not in commitment
        or "root" not in commitment
        or "params" not in commitment
    ):
        raise ValueError(
            "commitment is missing required keys ('namespace', 'root', 'params')"
        )

    ns = _to_bytes(commitment["namespace"])
    root_expected = _to_bytes(commitment["root"])
    params = commitment["params"]

    if not isinstance(params, Mapping):
        raise TypeError("commitment['params'] must be a mapping")

    original_len = params.get("original_len")
    if not isinstance(original_len, int) or original_len < 0:
        raise ValueError(
            "commitment['params']['original_len'] must be a non-negative integer"
        )

    shard_list: List[bytes] = [_to_bytes(s) for s in shards]

    # Basic sanity: if the original length was > 0, we expect at least one shard.
    if original_len > 0 and not shard_list:
        raise ValueError("no shards provided for non-empty blob")

    root_actual = _compute_root(ns, shard_list)
    if root_actual != root_expected:
        raise ValueError(
            "DA commitment root mismatch; shards may be corrupted or mismatched"
        )

    blob_concat = b"".join(shard_list)
    if len(blob_concat) < original_len:
        raise ValueError(
            f"insufficient data in shards to reconstruct original blob "
            f"(have {len(blob_concat)} bytes, need {original_len})"
        )

    return blob_concat[:original_len]
