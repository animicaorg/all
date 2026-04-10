"""
Animica • DA • Blob Commitment

High-level helpers to compute the canonical **commitment** for a blob:
the Namespaced Merkle Tree (NMT) root over the erasure-coded, namespaced
leaves derived from the payload.

The commitment is what ultimately appears in headers (directly or as part of
an aggregate), and is represented by :class:`da.blob.types.Commitment`.

Design goals
------------
- Stream-friendly: sources can be bytes, file paths, file-like objects, or
  iterables of bytes. The erasure encoder handles chunking/streaming.
- Policy-aware namespaces: we validate basic namespace id constraints here;
  detailed policy enforcement belongs to higher layers.
- Minimal coupling: this module delegates to `da.erasure.encoder` for the
  (chunk → erasure → namespaced leaves) pipeline, and to `da.nmt.commit`
  to compute the NMT root from those leaves.

API
---
- commit(source, namespace, *, mime=None, erasure_params=None) -> Commitment, BlobMeta
- commit_bytes(data, namespace, **kw) -> Commitment, BlobMeta
- commit_file(path_or_file, namespace, **kw) -> Commitment, BlobMeta
- commit_iter(byte_iterable, namespace, **kw) -> Commitment, BlobMeta
"""

from __future__ import annotations

import os
from typing import IO, Any, Dict, Iterable, Optional, Tuple, Union

from ..constants import \
    MAX_BLOB_BYTES  # soft guard; precise enforcement upstream
from ..nmt.namespace import validate_namespace_id
from .types import BlobMeta
from .types import Commitment as CommitmentT

# --- Types for sources ---
Source = Union[
    bytes, bytearray, memoryview, str, os.PathLike, IO[bytes], Iterable[bytes]
]


# --- internal: dynamic imports with graceful fallbacks ---------------------- #


def _nmt_compute_root():
    """
    Return a callable that computes an NMT root from an iterable/sequence of
    already-encoded NMT leaves (bytes).
    """
    # Preferred name
    try:  # pragma: no cover - import lattice
        from ..nmt.commit import compute_da_root as fn  # type: ignore

        return fn
    except Exception:
        pass
    # Alternate name (if defined that way)
    try:  # pragma: no cover
        from ..nmt.commit import compute_nmt_root as fn  # type: ignore

        return fn
    except Exception:
        pass
    # Last-resort generic
    try:  # pragma: no cover
        from ..nmt.commit import compute_root as fn  # type: ignore

        return fn
    except Exception as e:  # pragma: no cover
        raise ImportError(
            "No suitable NMT commit function found in da.nmt.commit"
        ) from e


def _erasure_encode_to_leaves():
    """
    Return a callable that transforms a source+namespace into an **iterator of
    NMT leaves** and a **metadata dict** with optional fields:
      { size_bytes, data_shards, total_shards, share_bytes }
    """
    # Common name used by this repo
    try:  # pragma: no cover
        from ..erasure.encoder import \
            encode_blob_to_leaves as fn  # type: ignore

        return fn
    except Exception:
        pass
    # Alternate name
    try:  # pragma: no cover
        from ..erasure.encoder import \
            encode_to_namespaced_leaves as fn  # type: ignore

        return fn
    except Exception:
        pass
    # Fallback to a more generic entrypoint that returns (leaves, meta)
    try:  # pragma: no cover
        from ..erasure.encoder import encode as fn  # type: ignore

        return fn
    except Exception as e:  # pragma: no cover
        raise ImportError(
            "No suitable erasure encoder found in da.erasure.encoder"
        ) from e


# --- core ------------------------------------------------------------------ #


def _compute_size_hint(source: Source) -> Optional[int]:
    """Best-effort size hint without consuming the stream."""
    if isinstance(source, (bytes, bytearray, memoryview)):
        return len(source)
    if isinstance(source, (str, os.PathLike)):
        try:
            return os.path.getsize(source)  # type: ignore[arg-type]
        except Exception:
            return None
    # File-like or iterable: no non-destructive way here
    return None


def _meta_from_dict(
    d: Dict[str, Any], *, namespace: int, mime: Optional[str]
) -> BlobMeta:
    return BlobMeta(
        namespace=int(namespace),
        size_bytes=int(d.get("size_bytes", d.get("sizeBytes", 0))),
        mime=mime,
        data_shards=(
            (None if d.get("data_shards") is None else int(d["data_shards"]))
            if "data_shards" in d
            else (None if d.get("dataShards") is None else int(d["dataShards"]))
        ),
        total_shards=(
            (None if d.get("total_shards") is None else int(d["total_shards"]))
            if "total_shards" in d
            else (None if d.get("totalShards") is None else int(d["totalShards"]))
        ),
        share_bytes=(
            (None if d.get("share_bytes") is None else int(d["share_bytes"]))
            if "share_bytes" in d
            else (
                (None if d.get("shareBytes") is None else int(d["shareBytes"]))
                if "shareBytes" in d
                else None
            )
        ),
    )


def commit(
    source: Source,
    namespace: int,
    *,
    mime: Optional[str] = None,
    erasure_params: Optional[object] = None,
) -> Tuple[CommitmentT, BlobMeta]:
    """
    Compute the canonical blob commitment for `source` under `namespace`.

    Parameters
    ----------
    source:
        bytes / bytearray / memoryview, file path, file-like (rb), or iterable[bytes].
    namespace:
        Non-negative integer namespace id (policy/range enforcement happens upstream).
    mime:
        Optional MIME type hint for metadata (informational only).
    erasure_params:
        Optional params object for the erasure pipeline (e.g., k/n profile). If None,
        defaults are used by the encoder.

    Returns
    -------
    (Commitment, BlobMeta)
        - Commitment(namespace, root, size_bytes)
        - BlobMeta(namespace, size_bytes, mime, data_shards, total_shards, share_bytes)

    Raises
    ------
    ValueError
        If namespace is invalid or size exceeds soft MAX_BLOB_BYTES.
    ImportError
        If encoder or NMT commit function is not available.
    """
    # Validate namespace early
    validate_namespace_id(namespace)

    # Soft preflight size guard (won't consume streams)
    size_hint = _compute_size_hint(source)
    if size_hint is not None and size_hint > MAX_BLOB_BYTES:
        raise ValueError(f"blob size {size_hint} > MAX_BLOB_BYTES={MAX_BLOB_BYTES}")

    # Get encoder & NMT root calculators
    encode_to_leaves = _erasure_encode_to_leaves()
    compute_root = _nmt_compute_root()

    # Run the pipeline: source → namespaced leaves (bytes) [+ meta]
    # Try multiple calling conventions for flexibility across encoder variants.
    leaves_iter: Iterable[bytes]
    meta_like: Dict[str, Any]

    called = False
    last_err: Optional[Exception] = None
    for kw in (
        {"source": source, "namespace": namespace, "params": erasure_params},
        {"source": source, "ns": namespace, "params": erasure_params},
        {"data": source, "namespace": namespace, "params": erasure_params},
        {"data": source, "ns": namespace, "params": erasure_params},
    ):
        try:
            leaves_iter, meta_like = encode_to_leaves(**kw)  # type: ignore[misc]
            called = True
            break
        except TypeError as e:
            last_err = e
            continue
    if not called:  # pragma: no cover
        raise TypeError(
            "erasure encoder signature mismatch; tried variants with "
            "(source|data) and (namespace|ns)."
        ) from last_err

    # Compute the NMT root from emitted leaves (can be a generator).
    root_bytes = compute_root(leaves_iter)

    # Build metadata; if encoder didn't provide size, compute from hint or fallback.
    meta = _meta_from_dict(meta_like or {}, namespace=namespace, mime=mime)
    if meta.size_bytes == 0:
        # If the encoder didn't populate a precise size and we don't have a hint,
        # we prefer to avoid consuming `source` here. Leave as 0 (caller may fill).
        if size_hint is not None:
            meta = BlobMeta(
                namespace=meta.namespace,
                size_bytes=size_hint,
                mime=meta.mime,
                data_shards=meta.data_shards,
                total_shards=meta.total_shards,
                share_bytes=meta.share_bytes,
            )

    # Final soft size check if we now have an exact size
    if meta.size_bytes and meta.size_bytes > MAX_BLOB_BYTES:
        raise ValueError(
            f"blob size {meta.size_bytes} > MAX_BLOB_BYTES={MAX_BLOB_BYTES}"
        )

    commitment = CommitmentT(
        namespace=namespace, root=root_bytes, size_bytes=meta.size_bytes
    )
    return commitment, meta


# --- convenience wrappers --------------------------------------------------- #


def commit_bytes(
    data: Union[bytes, bytearray, memoryview],
    namespace: int,
    *,
    mime: Optional[str] = None,
    erasure_params: Optional[object] = None,
) -> Tuple[CommitmentT, BlobMeta]:
    """Commit an in-memory payload."""
    return commit(data, namespace, mime=mime, erasure_params=erasure_params)


def commit_file(
    path_or_file: Union[str, os.PathLike, IO[bytes]],
    namespace: int,
    *,
    mime: Optional[str] = None,
    erasure_params: Optional[object] = None,
) -> Tuple[CommitmentT, BlobMeta]:
    """Commit a file by path or already-open file object."""
    return commit(path_or_file, namespace, mime=mime, erasure_params=erasure_params)


def commit_iter(
    byte_iterable: Iterable[bytes],
    namespace: int,
    *,
    mime: Optional[str] = None,
    erasure_params: Optional[object] = None,
) -> Tuple[CommitmentT, BlobMeta]:
    """Commit a stream represented as an iterable of bytes chunks."""
    return commit(byte_iterable, namespace, mime=mime, erasure_params=erasure_params)


__all__ = [
    "commit",
    "commit_bytes",
    "commit_file",
    "commit_iter",
]
