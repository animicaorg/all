"""
Animica PQ Primitives (pq.py)
=============================

High-level, uniform Python APIs for **post-quantum crypto** used across Animica:

- **Signatures:** CRYSTALS-Dilithium3, SPHINCS+ (SHAKE-128s)
- **KEM:** ML-KEM / Kyber-768 (for P2P handshakes)
- **Policy & IDs:** canonical algorithm IDs and feature flags
- **Addressing:** bech32m `anim1…` addresses derived from PQ public keys
- **Handshake:** Kyber768 + HKDF-SHA3-256 → AEAD channel bootstrap

Backends
--------
- Prefers **liboqs** when available (fast paths).
- Falls back to **pure-Python/portable** refs (slow; suitable for tests/dev).
- All consensus-critical encodings/hashes use SHA3/SHAKE and deterministic CBOR.

This package exposes cohesive, versioned entrypoints while keeping implementation
details in submodules. Import the functions you need directly from here:

    from pq.py import registry, address, keygen, sign, verify, kem, handshake

Versioning
----------
The Python distribution name is **`animica-pq`**. The `__version__` below is
sourced from installed metadata when available, otherwise a sensible dev default.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version as _pkg_version

try:
    __version__ = _pkg_version("animica-pq")
except PackageNotFoundError:  # local checkouts / editable installs
    __version__ = "0.1.0.dev0"

# Submodule re-exports (kept flat for DX)
# These imports are intentionally at module scope so IDEs and type checkers see them.
# Implementations live in sibling files per the project layout.
from . import registry  # alg id/name/size tables and feature flags
from . import utils     # sha3/blake3 (opt), hkdf, rng, bech32 helpers
from . import address   # bech32m anim1… codec (alg_id || sha3_256(pubkey))
from . import keygen    # uniform keygen API for signatures & KEM
from . import sign      # domain-separated sign(msg, alg_id, sk)
from . import verify    # verify(sig, msg, alg_id, pk)
from . import kem       # Kyber768 encaps/decaps wrappers
from . import handshake # Kyber768 + HKDF P2P handshake

__all__ = [
    "__version__",
    "registry",
    "utils",
    "address",
    "keygen",
    "sign",
    "verify",
    "kem",
    "handshake",
    "banner",
    "features",
]

def _detect_features() -> dict[str, bool]:
    """
    Detect optional runtime features/backends. Safe and side-effect free.
    """
    oqs_ok = False
    try:
        # Import is lightweight; module handles its own guarded dlopen.
        from .algs import oqs_backend  # noqa: F401
        oqs_ok = oqs_backend.is_available()
    except Exception:
        oqs_ok = False
    return {
        "liboqs": oqs_ok,
        "blake3": _has_blake3(),
    }

def _has_blake3() -> bool:
    try:
        import blake3  # type: ignore  # noqa: F401
        return True
    except Exception:
        return False

features = _detect_features()

def banner() -> str:
    """
    Return a small one-line banner suitable for logs.
    Example: 'animica-pq 0.1.0 (liboqs=yes, blake3=no)'
    """
    liboqs = "yes" if features.get("liboqs") else "no"
    blake3 = "yes" if features.get("blake3") else "no"
    return f"animica-pq {__version__} (liboqs={liboqs}, blake3={blake3})"
