"""
Animica P2P wire layer.

This package contains:
  - encoding.py    : canonical msgspec/CBOR codecs and helpers
  - message_ids.py : numeric IDs for all wire messages (HELLO, PING, INV, …)
  - messages.py    : dataclasses / typed models for all frames
  - frames.py      : envelope (msg_id, seq, flags, payload), checksums, size guards

Design goals:
  • Deterministic, versioned schemas (backed by spec/ and unit tests)
  • Zero-copy where possible; constant-time comparisons for critical fields
  • Forward-compat via feature flags and reserved bits
"""

from __future__ import annotations

from importlib import import_module as _import_module
from typing import Any, List

try:
    # Re-export module version if parent provides it.
    from ..version import __version__  # type: ignore
except Exception:  # pragma: no cover - optional
    __version__ = "0.0.0+local"

__all__: List[str] = [
    "encoding",
    "message_ids",
    "messages",
    "frames",
    "__version__",
]


def __getattr__(name: str) -> Any:
    """
    Lazy-import submodules so downstreams can do:

        from p2p.wire import encoding, messages

    without importing everything eagerly.
    """
    if name in ("encoding", "message_ids", "messages", "frames"):
        return _import_module(f"{__name__}.{name}")
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> List[str]:  # pragma: no cover - trivial
    return sorted(list(globals().keys()) + __all__)
