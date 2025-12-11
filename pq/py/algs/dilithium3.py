from __future__ import annotations

"""
Animica PQ: Dilithium3 signature backend (native wrapper)

Priority of implementations:
  1) python-oqs (Open Quantum Safe) — production-capable if installed
  2) Explicitly opted-in DEV-ONLY fallback (ANIMICA_UNSAFE_PQ_FAKE=1), which is NOT secure
     and exists purely to let the rest of the stack run on machines without liboqs.
     Do not use on real networks.

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

# --------------------------------------------------------------------------------------
# Try python-oqs first
# --------------------------------------------------------------------------------------
_OQS_OK = False
_sizes: Dict[str, int] = {"pk": 0, "sk": 0, "sig": 0}

try:
    import oqs  # type: ignore

    # Try ML-DSA-65 first (NIST standard, liboqs 0.15.0+)
    # Fall back to Dilithium3 (legacy, liboqs < 0.15.0)
    enabled_mechs = getattr(oqs, "get_enabled_sig_mechanisms", lambda: [])()
    _mechanism_name = None
    
    if "ML-DSA-65" in enabled_mechs:
        _mechanism_name = "ML-DSA-65"
    elif "Dilithium3" in enabled_mechs:
        _mechanism_name = "Dilithium3"
    
    if _mechanism_name:
        # Probe sizes once; reuse instances per call to ensure fresh context
        with oqs.Signature(_mechanism_name) as _probe:
            _sizes = {
                "pk": _probe.length_public_key,  # type: ignore[attr-defined]
                "sk": _probe.length_secret_key,  # type: ignore[attr-defined]
                "sig": _probe.length_signature,  # type: ignore[attr-defined]
            }
        _OQS_OK = True
except Exception:
    _OQS_OK = False
    _mechanism_name = None


# --------------------------------------------------------------------------------------
# Unsafe dev fallback (only if explicitly enabled)
# --------------------------------------------------------------------------------------
_DEV_FAKE_OK = False
if not _OQS_OK and os.environ.get("ANIMICA_UNSAFE_PQ_FAKE", "") == "1":
    _DEV_FAKE_OK = True
    # Chosen arbitrarily; matches our fake encode/verify below
    _sizes = {"pk": 32, "sk": 32, "sig": 64}


# Hash helpers for the fake mode (kept local to avoid extra deps here)
def _sha3_256(data: bytes) -> bytes:
    try:
        import hashlib

        return hashlib.sha3_256(data).digest()
    except Exception as e:
        raise RuntimeError("hashlib.sha3_256 unavailable") from e


def _sha3_512(data: bytes) -> bytes:
    try:
        import hashlib

        return hashlib.sha3_512(data).digest()
    except Exception as e:
        raise RuntimeError("hashlib.sha3_512 unavailable") from e


# --------------------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------------------
sizes = _sizes.copy()


def is_available() -> bool:
    """
    Return True if a working Dilithium3 implementation is available.
    This is True when python-oqs exposes Dilithium3, or when the explicit
    ANIMICA_UNSAFE_PQ_FAKE=1 dev-only fallback is enabled.
    """
    return _OQS_OK or _DEV_FAKE_OK


def keypair(seed: Optional[bytes] = None) -> Tuple[bytes, bytes]:
    """
    Generate a Dilithium3/ML-DSA-65 keypair.

    Notes:
      • With python-oqs, the RNG is internal; the optional seed is ignored.
      • In DEV-ONLY fallback, we derive (sk, pk) deterministically from seed or OS RNG.
      • Uses ML-DSA-65 (NIST standard) if available, falls back to Dilithium3 (legacy).
    """
    if _OQS_OK and _mechanism_name:
        with oqs.Signature(_mechanism_name) as signer:  # type: ignore[name-defined]
            pk = signer.generate_keypair()
            sk = signer.export_secret_key()
            return (sk, pk)

    if _DEV_FAKE_OK:
        if seed is None:
            # Use OS RNG to synthesize a seed
            seed = os.urandom(32)
        sk = _sha3_256(b"animica-dev-fake-sk|" + seed)
        pk = _sha3_256(b"animica-dev-fake-pk|" + sk)
        return (sk, pk)

    raise NotImplementedError(
        "Dilithium3 unavailable. Install python-oqs/liboqs or set ANIMICA_UNSAFE_PQ_FAKE=1 (DEV-ONLY)"
    )


def generate_keypair(seed: Optional[bytes] = None) -> Tuple[bytes, bytes]:
    """Compatibility wrapper returning (pk, sk)."""
    sk, pk = keypair(seed)
    return (pk, sk)


def sign(sk: bytes, msg: bytes) -> bytes:
    """
    Sign a message.

    Returns:
      Signature bytes.

    Security:
      • Real signatures when python-oqs is present.
      • DEV-ONLY fallback returns sha3_512(sk || msg) and is NOT secure.
      • Uses ML-DSA-65 (NIST standard) if available, falls back to Dilithium3 (legacy).
    """
    if _OQS_OK and _mechanism_name:
        with oqs.Signature(_mechanism_name, secret_key=sk) as signer:  # type: ignore[name-defined]
            return signer.sign(msg)

    if _DEV_FAKE_OK:
        pk = _sha3_256(b"animica-dev-fake-pk|" + sk)
        return _sha3_512(b"animica-dev-fake-sig|" + pk + b"|" + msg)

    raise NotImplementedError(
        "Dilithium3 unavailable. Install python-oqs/liboqs or enable ANIMICA_UNSAFE_PQ_FAKE=1 for local dev."
    )


def verify(pk: bytes, msg: bytes, sig: bytes) -> bool:
    """
    Verify a signature.

    Returns:
      True if valid, False otherwise.
      Uses ML-DSA-65 (NIST standard) if available, falls back to Dilithium3 (legacy).
    """
    if _OQS_OK and _mechanism_name:
        try:
            # Newer oqs builds accept public_key in the constructor
            with oqs.Signature(_mechanism_name, public_key=pk) as verifier:  # type: ignore[name-defined]
                try:
                    return bool(verifier.verify(msg, sig))
                except Exception:
                    return False
        except TypeError:
            # Older oqs builds expect the public key to be passed to verify()
            with oqs.Signature(_mechanism_name) as verifier:  # type: ignore[name-defined]
                try:
                    return bool(verifier.verify(msg, sig, pk))
                except Exception:
                    return False

    if _DEV_FAKE_OK:
        expect = _sha3_512(
            b"animica-dev-fake-sig|"
            + _sha3_256(b"animica-dev-fake-sk|" + pk)
            + b"|"
            + msg
        )
        # In fake mode we don't have the real sk; accept either derivation:
        #   (a) signer used sk directly (sign() path)
        #   (b) verifier reconstructs a pseudo-sk from pk for convenience
        return (
            sig == _sha3_512(b"animica-dev-fake-sig|" + pk + b"|" + msg)
            or sig == expect
        )

    # If neither backend is available, report failure cleanly.
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
