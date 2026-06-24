"""animica_fastpow — native SHA3-256 PoW nonce scanner for the Animica CPU miner.

The compiled ``_fastpow`` extension runs the hash loop in C with the GIL
released, giving real multi-core hashrate. Import is tolerant: if the extension
isn't built/shipped, ``available()`` returns ``False`` and the caller falls back
to the pure-Python loop (so the miner still works, just slower).

API
---
    scan(prefix: bytes, mix_seed: bytes, target: bytes(32, big-endian),
         start_nonce: int, iterations: int) -> tuple[int, bytes] | None
        First nonce in [start_nonce, start_nonce+iterations) whose
        SHA3-256(prefix || mix_seed || nonce.to_bytes(8,"little")) is <= target,
        with its 32-byte digest; or None.

    sha3_256(data: bytes) -> bytes      # NIST SHA3-256, for parity checks
"""
from __future__ import annotations

try:
    from . import _fastpow  # compiled C extension (built via setup.py / wheel)
    scan = _fastpow.scan
    sha3_256 = _fastpow.sha3_256
    _AVAILABLE = True
    _IMPORT_ERROR = None
except Exception as exc:  # pragma: no cover - missing/unbuilt extension
    _fastpow = None
    scan = None
    sha3_256 = None
    _AVAILABLE = False
    _IMPORT_ERROR = exc


def available() -> bool:
    """True iff the native extension loaded and ``scan``/``sha3_256`` are usable."""
    return _AVAILABLE


def import_error() -> Exception | None:
    """The exception that prevented loading the native extension, if any."""
    return _IMPORT_ERROR


__all__ = ["scan", "sha3_256", "available", "import_error"]
