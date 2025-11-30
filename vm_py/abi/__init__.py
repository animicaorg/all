"""
vm_py.abi
=========

Public ABI surface for the Animica Python VM.

This package provides:
  • Type definitions for ABI items (functions, events, errors, scalars).
  • Canonical encoder/decoder utilities for arguments and return values.
  • Helpers used by the RPC/SDK to pack/unpack call payloads.

Everything here is pure-Python and deterministic. Encoding is a simple,
length-prefixed, canonical scheme that aligns with the node RPC and SDKs.
"""

from __future__ import annotations

# Re-export version for convenience.
try:
    from ..version import __version__  # type: ignore
except Exception:  # pragma: no cover
    __version__ = "0.0.0+unknown"

from .decoding import *  # noqa: F401,F403
from .encoding import *  # noqa: F401,F403
# Re-export the primary public API of submodules.
# (Submodules should define their own __all__.)
from .types import *  # noqa: F401,F403

# Compose a stable __all__ from submodule exports if present.
_all_types: tuple[str, ...]
_all_encoding: tuple[str, ...]
_all_decoding: tuple[str, ...]

try:
    from .types import __all__ as _all_types  # type: ignore
except Exception:  # pragma: no cover
    _all_types = tuple()

try:
    from .encoding import __all__ as _all_encoding  # type: ignore
except Exception:  # pragma: no cover
    _all_encoding = tuple()

try:
    from .decoding import __all__ as _all_decoding  # type: ignore
except Exception:  # pragma: no cover
    _all_decoding = tuple()

__all__ = tuple(
    dict.fromkeys(  # preserve order, dedupe
        (*_all_types, *_all_encoding, *_all_decoding, "__version__")
    )
)
