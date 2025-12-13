from __future__ import annotations

"""
Animica PQ: Dilithium3 signature backend (pure-Python implementation)

Uses the vendored pure-Python ML-DSA-65 (Dilithium3) implementation from animica._vendor.
No liboqs or compiled dependencies required.

Uniform surface exposed to higher layers (see pq/py/algs/__init__.py):
  - sizes: dict {"pk","sk","sig"} (ints)
  - is_available() -> bool
  - keypair(seed: bytes|None) -> (sk: bytes, pk: bytes)
  - sign(sk: bytes, msg: bytes) -> sig: bytes
  - verify(pk: bytes, msg: bytes, sig: bytes) -> bool
"""

import os
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

# Use pure-Python implementation from vendored animica._vendor
_PURE_PYTHON_OK = False
_sizes: Dict[str, int] = {"pk": 1952, "sk": 4000, "sig": 3293}

try:
    # Import pure-Python implementation from vendored animica._vendor
    # Use absolute imports after adjusting path to find animica package
    import sys
    import os
    # Add parent directory (repo root) to path to find animica package
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../"))
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)
    
    from animica._vendor.dilithium_py import Dilithium3 as _Dilithium3Impl
    
    _PURE_PYTHON_OK = True
    _sizes = {"pk": 1952, "sk": 4000, "sig": 3293}
except Exception:
    _PURE_PYTHON_OK = False


# --------------------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------------------
sizes = _sizes.copy()


def is_available() -> bool:
    """
    Return True if a working Dilithium3 implementation is available.
    Uses pure-Python ML-DSA-65 (Dilithium3) from animica._vendor.
    """
    return _PURE_PYTHON_OK


def keypair(seed: Optional[bytes] = None) -> Tuple[bytes, bytes]:
    """
    Generate a Dilithium3/ML-DSA-65 keypair using pure-Python implementation.

    Args:
        seed: Optional 32-byte seed for deterministic generation (testing only)

    Returns:
        (secret_key, public_key) tuple
    """
    if not _PURE_PYTHON_OK:
        raise NotImplementedError(
            "Dilithium3 pure-Python implementation unavailable. Check animica._vendor installation."
        )
    
    return _Dilithium3Impl.keygen(seed)


def generate_keypair(seed: Optional[bytes] = None) -> Tuple[bytes, bytes]:
    """Compatibility wrapper returning (pk, sk)."""
    sk, pk = keypair(seed)
    return (pk, sk)


def sign(sk: bytes, msg: bytes) -> bytes:
    """
    Sign a message using pure-Python ML-DSA-65 (Dilithium3).

    Args:
        sk: Secret key (4000 bytes)
        msg: Message to sign

    Returns:
        Signature bytes (3293 bytes)
    """
    if not _PURE_PYTHON_OK:
        raise NotImplementedError(
            "Dilithium3 pure-Python implementation unavailable."
        )
    
    return _Dilithium3Impl.sign(sk, msg)


def verify(pk: bytes, msg: bytes, sig: bytes) -> bool:
    """
    Verify a signature using pure-Python ML-DSA-65 (Dilithium3).

    Args:
        pk: Public key (1952 bytes)
        msg: Message that was signed
        sig: Signature to verify (3293 bytes)

    Returns:
        True if valid, False otherwise
    """
    if not _PURE_PYTHON_OK:
        return False
    
    try:
        return _Dilithium3Impl.verify(pk, msg, sig)
    except Exception:
        return False


# Self-check (optional quick sanity if module is run directly)
if __name__ == "__main__":
    print("[dilithium3] available:", is_available(), "sizes:", sizes)
    try:
        sk, pk = keypair()
        msg = b"hello animica"
        sig = sign(sk, msg)
        print("verify(ok):", verify(pk, msg, sig))
        print("verify(bad):", verify(pk, msg + b"x", sig))
    except NotImplementedError as e:
        print(str(e))
