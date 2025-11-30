"""PQ verify helper for the VM stdlib.

This module first attempts to call a native precompile/prototype wrapper (`vm_py.precompiles.pq_precompile`)
that uses a vetted PQ library like liboqs. If that is not available, it falls back to the
development HMAC-based shim used for local testing.

Production: replace the fallback with a native, audited precompile that performs real PQ verification.
"""
from __future__ import annotations

import hmac
import hashlib
import typing as t

try:
    # Optional prototype precompile wrapper that calls python-oqs if installed
    from vm_py.precompiles import pq_precompile  # type: ignore
    _HAS_PRECOMPILE = True
except Exception:
    pq_precompile = None  # type: ignore
    _HAS_PRECOMPILE = False


def _to_bytes(x: t.Union[bytes, str]) -> bytes:
    if isinstance(x, bytes):
        return x
    return bytes.fromhex(x)


def verify(pubkey_hex: bytes | str, message: bytes, sig_hex: bytes | str, scheme: str = "Dilithium3") -> bool:
    """Verify a message signed by a PQ scheme.

    Tries the native precompile first; falls back to HMAC shim for development.
    """
    # Normalize inputs
    try:
        pubkey = _to_bytes(pubkey_hex)
    except Exception:
        pubkey = pubkey_hex if isinstance(pubkey_hex, bytes) else bytes(str(pubkey_hex), 'utf8')

    try:
        sig = _to_bytes(sig_hex)
    except Exception:
        sig = sig_hex if isinstance(sig_hex, bytes) else bytes(str(sig_hex), 'utf8')

    # Try native/prototype precompile
    if _HAS_PRECOMPILE:
        try:
            return pq_precompile.verify(pubkey, message, sig, scheme=scheme)
        except Exception:
            # If precompile fails unexpectedly, fall back to dev shim
            pass

    # Development fallback: HMAC-SHA256 using pubkey as key (NOT SECURE)
    expected = hmac.new(pubkey, message, hashlib.sha256).digest()
    return hmac.compare_digest(expected, sig)
