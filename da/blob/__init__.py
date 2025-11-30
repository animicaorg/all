"""
Animica • DA • Blob package

High-level blob primitives and helpers used by the Data Availability layer.

Subpackages & siblings of interest:
  • da.schemas         — CDDL/JSON-Schema objects for envelopes/proofs.
  • da.nmt             — Namespaced Merkle Tree (NMT) structures & proofs.
  • da.erasure         — Reed–Solomon params/encoder/decoder and layout math.
  • da.blob            — This package: blob types, commitment, store/index, IO.
  • da.sampling        — DAS client/verifier and probability helpers.
  • da.retrieval       — FastAPI service: POST/GET/proof endpoints.
  • da.adapters        — Wiring hooks into core/rpc/p2p.

Only light re-exports live here to avoid import-time bloat; heavy modules
(chunking, store, etc.) should be imported from their concrete modules.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..version import __version__  # re-export package version

if TYPE_CHECKING:
    # These are imported only for typing to keep import-time lean.
    from .types import BlobMeta, BlobRef, Commitment, Receipt  # noqa: F401

__all__ = [
    "__version__",
    # Types (available at runtime once imported from da.blob.types)
    "BlobRef",
    "BlobMeta",
    "Commitment",
    "Receipt",
]
