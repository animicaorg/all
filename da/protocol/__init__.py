from __future__ import annotations

"""
Animica • DA • Wire Protocol
===========================

Package marker and light re-exports for the Data Availability (DA) wire
protocol. This package covers the *binary/on-the-wire* message shapes used
between nodes (distinct from higher-level P2P gossip helpers).

Modules
-------
- messages.py  : INV / GET / PROOF message dataclasses and enums.
- encoding.py  : Compact codec (CBOR/msgspec) and frame checksums.

Versioning
----------
The protocol is versioned independently from the Python package version.
Bumping `PROTOCOL_VERSION` constitutes a wire-level change and should be
accompanied by compatibility notes and test vectors.

Convenience
-----------
`encode(obj)` / `decode(buf)` lazily import the codec to avoid import cycles
and heavy dependencies at import time.
"""

from typing import Any

# Public semantic version of the *Python package*.
try:
    from da.version import __version__  # re-export package version
except Exception:  # pragma: no cover
    __version__ = "0.0.0+unknown"

# Wire protocol family name and version (for frames/handshakes if needed).
PROTOCOL_FAMILY = "animica/da"
PROTOCOL_VERSION = 1


def encode(obj: Any) -> bytes:
    """
    Encode a DA protocol message into bytes (frame).
    Lazily imports the codec to keep package import light.
    """
    from .encoding import encode_frame  # local import by design

    return encode_frame(obj)


def decode(buf: bytes) -> Any:
    """
    Decode bytes (frame) into a DA protocol message object.
    Lazily imports the codec to keep package import light.
    """
    from .encoding import decode_frame  # local import by design

    return decode_frame(buf)


__all__ = [
    "__version__",
    "PROTOCOL_FAMILY",
    "PROTOCOL_VERSION",
    "encode",
    "decode",
]
