from __future__ import annotations

"""
pq.py.utils
===========

Utility toolbox for the Animica PQ layer.

This package centralizes **portable primitives** used across wallet / SDK /
P2P handshake code:

- Hashing helpers (SHA3-256/512, optional BLAKE3, optional Keccak-256)
- HKDF-SHA3-256 key derivation (for P2P key schedule)
- Bech32m address codec (anim1...)
- RNG helpers (OS-backed and deterministic test seeds)
- Hex/bytes helpers

Design notes
------------
* These utilities are **non-consensus** conveniences. Consensus-critical
  domain separation strings and encodings are specified under `spec/`.
* Imports are **lazily guarded** so that partial checkouts don't explode;
  a missing submodule will raise a clear ImportError on first use rather
  than at package import time.
"""

from typing import Callable, Optional

__all__ = [
    # hashing
    "sha3_256", "sha3_512", "blake3_256", "keccak_256",
    "to_hex", "from_hex",
    # hkdf
    "hkdf_sha3_256",
    # bech32
    "bech32m_encode", "bech32m_decode",
    # rng
    "rng_bytes", "rng_u32",
]

# ---------------------------------------------------------------------------
# Guarded import helpers
# ---------------------------------------------------------------------------

def _missing_factory(module: str, symbol: str) -> Callable[..., "NoReturn"]:
    def _missing(*_args, **_kwargs):
        raise ImportError(
            f"Required utility '{symbol}' from '{module}' is unavailable. "
            f"Ensure file '{module.replace('.', '/')}.py' exists and imports correctly."
        )
    return _missing  # type: ignore[return-value]


# ------------------ Hashing ------------------

try:
    from .hash import (
        sha3_256,
        sha3_512,
        blake3_256,   # optional; falls back internally if blake3 is missing
        keccak_256,   # optional; present for tooling/tests
        to_hex,
        from_hex,
    )
except Exception:
    sha3_256 = _missing_factory("pq.py.utils.hash", "sha3_256")  # type: ignore[assignment]
    sha3_512 = _missing_factory("pq.py.utils.hash", "sha3_512")  # type: ignore[assignment]
    blake3_256 = _missing_factory("pq.py.utils.hash", "blake3_256")  # type: ignore[assignment]
    keccak_256 = _missing_factory("pq.py.utils.hash", "keccak_256")  # type: ignore[assignment]
    to_hex = _missing_factory("pq.py.utils.hash", "to_hex")  # type: ignore[assignment]
    from_hex = _missing_factory("pq.py.utils.hash", "from_hex")  # type: ignore[assignment]


# ------------------ HKDF ------------------

try:
    from .hkdf import hkdf_sha3_256
except Exception:
    hkdf_sha3_256 = _missing_factory("pq.py.utils.hkdf", "hkdf_sha3_256")  # type: ignore[assignment]


# ------------------ Bech32m ------------------

try:
    from .bech32 import bech32m_encode, bech32m_decode
except Exception:
    bech32m_encode = _missing_factory("pq.py.utils.bech32", "bech32m_encode")  # type: ignore[assignment]
    bech32m_decode = _missing_factory("pq.py.utils.bech32", "bech32m_decode")  # type: ignore[assignment]


# ------------------ RNG ------------------

try:
    from .rng import rng_bytes, rng_u32
except Exception:
    rng_bytes = _missing_factory("pq.py.utils.rng", "rng_bytes")  # type: ignore[assignment]
    rng_u32 = _missing_factory("pq.py.utils.rng", "rng_u32")      # type: ignore[assignment]
