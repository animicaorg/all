"""
Animica • DA • Erasure Coding

This package provides the erasure-coding layer used by the Data Availability
(NMT) subsystem. It is responsible for splitting blobs into fixed-size shares,
applying Reed–Solomon (RS) encoding to create redundancy, and defining the
canonical row/column layout used by DAS (Data Availability Sampling).

Submodules (lazy-imported)
--------------------------
params              — (k, n) profiles, shard size, padding rules
partitioner         — blob → fixed-size chunk/“share” partitioning
reedsolomon         — RS encode/decode primitives
encoder             — end-to-end blob → erasure → namespaced leaves
decoder             — recover original blob from any k shares (+ proof checks)
layout              — row/column math for extended matrices
availability_math   — DAS probability & sample sizing helpers

This __init__ exposes a stable, high-level surface via lazy attribute loading,
so importing `da.erasure` is fast and does not pull heavy deps until needed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, Tuple

from ..version import __version__  # re-export

# Public API surface (attribute name -> (module_path, attr_name))
_EXPORTS: Dict[str, Tuple[str, str]] = {
    # params
    "ErasureParams": ("da.erasure.params", "ErasureParams"),
    "DEFAULT_PARAMS": ("da.erasure.params", "DEFAULT_PARAMS"),
    # partitioner
    "partition_blob": ("da.erasure.partitioner", "partition_blob"),
    # reedsolomon
    "rs_encode": ("da.erasure.reedsolomon", "rs_encode"),
    "rs_decode": ("da.erasure.reedsolomon", "rs_decode"),
    "RSCodec": ("da.erasure.reedsolomon", "RSCodec"),
    # encoder / decoder
    "encode_blob_to_leaves": ("da.erasure.encoder", "encode_blob_to_leaves"),
    "recover_blob_from_leaves": ("da.erasure.decoder", "recover_blob_from_leaves"),
    # layout helpers
    "extended_dims": ("da.erasure.layout", "extended_dims"),
    "row_col_index": ("da.erasure.layout", "row_col_index"),
    "share_index_iter_rows": ("da.erasure.layout", "share_index_iter_rows"),
    # availability math
    "required_samples_for_p_fail": (
        "da.erasure.availability_math",
        "required_samples_for_p_fail",
    ),
    "p_fail_given_samples": ("da.erasure.availability_math", "p_fail_given_samples"),
}

__all__ = (
    tuple(sorted(_EXPORTS.keys() + ("__version__",)))
    if False
    else (
        "ErasureParams",
        "DEFAULT_PARAMS",
        "partition_blob",
        "rs_encode",
        "rs_decode",
        "RSCodec",
        "encode_blob_to_leaves",
        "recover_blob_from_leaves",
        "extended_dims",
        "row_col_index",
        "share_index_iter_rows",
        "required_samples_for_p_fail",
        "p_fail_given_samples",
        "__version__",
    )
)


def __getattr__(name: str) -> Any:
    """
    Lazy attribute loader for the public API. Imports the target symbol from the
    corresponding submodule on first access.
    """
    target = _EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module 'da.erasure' has no attribute {name!r}")
    mod_path, attr_name = target
    module = __import__(mod_path, fromlist=[attr_name])
    value = getattr(module, attr_name)
    globals()[name] = value  # cache
    return value


if TYPE_CHECKING:
    # Static type checkers resolve symbols without executing __getattr__
    from .availability_math import (p_fail_given_samples,
                                    required_samples_for_p_fail)
    from .decoder import recover_blob_from_leaves
    from .encoder import encode_blob_to_leaves
    from .layout import extended_dims, row_col_index, share_index_iter_rows
    from .params import DEFAULT_PARAMS, ErasureParams
    from .partitioner import partition_blob
    from .reedsolomon import RSCodec, rs_decode, rs_encode
