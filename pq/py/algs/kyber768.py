from __future__ import annotations

"""
Animica PQ: Kyber768 (ML-KEM-768) KEM backend (pure-Python implementation)

Uses the vendored pure-Python ML-KEM-768 (Kyber768) implementation from animica._vendor.
No liboqs or compiled dependencies required.

Uniform surface exposed to higher layers (see pq/py/algs/__init__.py):
  - sizes: dict {"pk","sk","ct","ss"} (ints)
  - is_available() -> bool
  - keypair(seed: bytes|None) -> (sk: bytes, pk: bytes)
  - encapsulate(pk: bytes) -> (ct: bytes, ss: bytes)
  - decapsulate(sk: bytes, ct: bytes) -> ss: bytes
"""

import os
from typing import Dict, Optional, Tuple

# Use pure-Python implementation from vendored animica._vendor
_PURE_PYTHON_OK = False
_sizes: Dict[str, int] = {"pk": 1184, "sk": 2400, "ct": 1088, "ss": 32}

try:
    # Import pure-Python implementation from vendored animica._vendor
    # Use absolute imports after adjusting path to find animica package
    import sys
    import os
    # Add parent directory (repo root) to path to find animica package
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../"))
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)
    
    from animica._vendor.kyber_py import Kyber768 as _Kyber768Impl
    
    _PURE_PYTHON_OK = True
except Exception:
    _PURE_PYTHON_OK = False


# --------------------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------------------
sizes = _sizes.copy()


def is_available() -> bool:
    """
    Return True if a working Kyber768 implementation is available.
    Uses pure-Python ML-KEM-768 (Kyber768) from animica._vendor.
    """
    return _PURE_PYTHON_OK


def keypair(seed: Optional[bytes] = None) -> Tuple[bytes, bytes]:
    """
    Generate a Kyber768/ML-KEM-768 keypair using pure-Python implementation.

    Args:
        seed: Optional 64-byte seed for deterministic generation (testing only)

    Returns:
        (secret_key, public_key) tuple
    """
    if not _PURE_PYTHON_OK:
        raise NotImplementedError(
            "Kyber768 pure-Python implementation unavailable. Check animica._vendor installation."
        )
    
    sk, pk = _Kyber768Impl.keygen(seed)
    return (sk, pk)


def generate_keypair(seed: Optional[bytes] = None) -> Tuple[bytes, bytes]:
    """Compatibility wrapper returning (pk, sk)."""
    sk, pk = keypair(seed)
    return (pk, sk)


def encapsulate(pk: bytes) -> Tuple[bytes, bytes]:
    """
    Encapsulate to generate shared secret and ciphertext.

    Args:
        pk: Public key (1184 bytes)

    Returns:
        (ciphertext, shared_secret) tuple
    """
    if not _PURE_PYTHON_OK:
        raise NotImplementedError(
            "Kyber768 pure-Python implementation unavailable."
        )
    
    ct, ss = _Kyber768Impl.encaps(pk)
    return (ct, ss)


def decapsulate(sk: bytes, ct: bytes) -> bytes:
    """
    Decapsulate ciphertext to recover shared secret.

    Args:
        sk: Secret key (2400 bytes)
        ct: Ciphertext (1088 bytes)

    Returns:
        shared_secret (32 bytes)
    """
    if not _PURE_PYTHON_OK:
        raise NotImplementedError(
            "Kyber768 pure-Python implementation unavailable."
        )
    
    return _Kyber768Impl.decaps(sk, ct)


# Self-check (optional quick sanity if module is run directly)
if __name__ == "__main__":
    print("[kyber768] available:", is_available(), "sizes:", sizes)
    try:
        sk, pk = keypair()
        ct, ss_b = encapsulate(pk)
        ss_a = decapsulate(sk, ct)
        print("match:", ss_a == ss_b, "ss len:", len(ss_a))
    except NotImplementedError as e:
        print(str(e))
