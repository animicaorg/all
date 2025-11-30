"""Prototype precompile wrapper for post-quantum signature verification.

This module attempts to use the `oqs` Python bindings (liboqs) when available and
exposes a `verify(pubkey, message, signature, scheme='Dilithium3') -> bool` function.

In a production runtime the equivalent logic should live in a native precompile (C/Rust)
linked against a vetted PQ library (liboqs, PQClean implementations, or a hardened wrapper).
This file is a prototype that allows the VM to call into `oqs` when present, but it
falls back to raising NotImplementedError if the native library isn't installed.

Usage:
    from vm_py.precompiles.pq_precompile import verify
    ok = verify(pubkey_bytes_or_hex, message_bytes, sig_bytes_or_hex, scheme='Dilithium3')
"""

from __future__ import annotations

import typing as t

try:
    import oqs  # type: ignore

    _HAS_OQS = True
except Exception:
    oqs = None  # type: ignore
    _HAS_OQS = False

try:
    # Try to load native precompile via ctypes helper (if built)
    from vm_py.precompiles import native_loader  # type: ignore

    _HAS_NATIVE = True
except Exception:
    native_loader = None  # type: ignore
    _HAS_NATIVE = False


def _to_bytes(x: t.Union[bytes, str]) -> bytes:
    if isinstance(x, bytes):
        return x
    return bytes.fromhex(x)


def verify(
    pubkey: t.Union[bytes, str],
    message: bytes,
    signature: t.Union[bytes, str],
    scheme: str = "Dilithium3",
) -> bool:
    """Verify a PQ signature using liboqs via python-oqs.

    Returns True if the signature verifies, False otherwise. Raises RuntimeError if oqs
    is not available in the environment.
    """
    if _HAS_NATIVE:
        try:
            return native_loader.verify(
                _to_bytes(pubkey), message, _to_bytes(signature), scheme
            )
        except Exception:
            # fall back to python oqs or shim
            pass

    if not _HAS_OQS:
        raise RuntimeError(
            "liboqs (python-oqs) is not available in this runtime and native precompile not found"
        )

    pub = _to_bytes(pubkey)
    sig = _to_bytes(signature)

    # python-oqs provides a Signature object for each scheme. Use it to verify.
    try:
        with oqs.Signature(scheme) as verifier:
            # verify returns True/False or raises on error depending on bindings
            return verifier.verify(message, sig, pub)
    except Exception:
        # Normalize any exceptions into False
        return False
