"""
Animica Data Availability (DA) package.

Public responsibilities:
- Commit blobs (NMT root), erasure encode/decode, and verify namespaced proofs.
- Compute/validate the block-level DA root (Merkle over BlobDescriptors).
- Serve/consume DA retrieval & proof APIs; support sampling for light clients.

This package intentionally keeps a small import surface at module import time so
downstream tools can `import da` without pulling heavy dependencies. Submodules
are imported lazily where possible.
"""

from __future__ import annotations

# Version
try:  # populated by da/version.py
    from .version import __version__  # type: ignore
except Exception:  # pragma: no cover
    __version__ = "0.0.0+local"

# Optional, lightweight re-exports for common types (guarded to avoid hard deps
# when only the package marker is needed).
__all__ = ["__version__"]

try:
    from .blob.types import BlobRef, BlobMeta, Commitment, Receipt  # type: ignore
    __all__ += ["BlobRef", "BlobMeta", "Commitment", "Receipt"]
except Exception:  # pragma: no cover
    pass

def get_version() -> str:
    """Return the DA package version string."""
    return __version__
