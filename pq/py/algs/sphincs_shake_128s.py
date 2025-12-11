from __future__ import annotations

"""
Animica PQ: SPHINCS+ SHAKE-128s signature backend (native wrapper)

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
from typing import Dict, Optional, Tuple

# --------------------------------------------------------------------------------------
# Try python-oqs first
# --------------------------------------------------------------------------------------
_OQS_OK = False
_OQS_MECH = None  # type: Optional[str]
_sizes: Dict[str, int] = {"pk": 0, "sk": 0, "sig": 0}

# A few possible mechanism names used across oqs/liboqs versions
# Note: liboqs 0.15.0+ uses "-simple" suffix for the simple parameter set
_POSSIBLE_MECH_NAMES = {
    # Prefer "robust" for cross-version compatibility with existing nodes.
    "robust": [
        "SPHINCS+-SHAKE-128s-robust",
        "SPHINCS+-shake-128s-robust",
        "SPHINCS+-SHAKE-128s",  # older versions without suffix default to robust
        "SPHINCS+-shake-128s",
    ],
    # Fallback to "simple" (liboqs 0.15+ default) when robust isn't available.
    "simple": [
        "SPHINCS+-SHAKE-128s-simple",
        "SPHINCS+-shake-128s-simple",
    ],
}


def _select_spx_mechanism(enabled: set[str]) -> Optional[str]:
    """Choose a deterministic SPHINCS+ mechanism from the enabled set."""

    preferred_variant = os.environ.get("ANIMICA_SPHINCS_VARIANT", "robust").strip().lower()
    # Default preference keeps compatibility with nodes built against older liboqs
    # releases that only ship the "robust" parameter set.
    variant_order = ["robust", "simple"]
    if preferred_variant == "simple":
        variant_order = ["simple", "robust"]

    for variant in variant_order:
        for name in _POSSIBLE_MECH_NAMES.get(variant, []):
            if name in enabled:
                return name
    return None

try:
    import oqs  # type: ignore

    enabled = set(getattr(oqs, "get_enabled_sig_mechanisms", lambda: [])())
    _OQS_MECH = _select_spx_mechanism(enabled)
    if _OQS_MECH:
        with oqs.Signature(_OQS_MECH) as _probe:  # type: ignore[arg-type]
            _sizes = {
                "pk": _probe.length_public_key,  # type: ignore[attr-defined]
                "sk": _probe.length_secret_key,  # type: ignore[attr-defined]
                "sig": _probe.length_signature,  # type: ignore[attr-defined]
            }
        _OQS_OK = True
except Exception:
    _OQS_OK = False
    _OQS_MECH = None

# --------------------------------------------------------------------------------------
# Unsafe dev fallback (only if explicitly enabled)
# --------------------------------------------------------------------------------------
_DEV_FAKE_OK = False
if not _OQS_OK and os.environ.get("ANIMICA_UNSAFE_PQ_FAKE", "") == "1":
    _DEV_FAKE_OK = True
    # Chosen arbitrarily for local-only operation
    _sizes = {"pk": 32, "sk": 32, "sig": 64}


# Local SHA3 helpers for the fake mode (avoid importing our higher-level utils here)
def _sha3_256(data: bytes) -> bytes:
    import hashlib

    return hashlib.sha3_256(data).digest()


def _sha3_512(data: bytes) -> bytes:
    import hashlib

    return hashlib.sha3_512(data).digest()


# --------------------------------------------------------------------------------------
# Public API (uniform)
# --------------------------------------------------------------------------------------
sizes = _sizes.copy()


def is_available() -> bool:
    """
    Return True if a working SPHINCS+-SHAKE-128s implementation is available.
    True when python-oqs exposes a compatible mechanism, or when the explicit
    ANIMICA_UNSAFE_PQ_FAKE=1 dev-only fallback is enabled.
    """
    return _OQS_OK or _DEV_FAKE_OK


def keypair(seed: Optional[bytes] = None) -> Tuple[bytes, bytes]:
    """
    Generate a SPHINCS+-SHAKE-128s keypair.

    Notes:
      • With python-oqs, RNG is internal; the optional seed is ignored.
      • In DEV-ONLY fallback, we derive (sk, pk) deterministically from seed or OS RNG.
    """
    if _OQS_OK and _OQS_MECH:
        with oqs.Signature(_OQS_MECH) as signer:  # type: ignore[arg-type]
            pk = signer.generate_keypair()
            sk = signer.export_secret_key()
            return (sk, pk)

    if _DEV_FAKE_OK:
        if seed is None:
            seed = os.urandom(32)
        sk = _sha3_256(b"animica-dev-fake-sphincs-sk|" + seed)
        pk = _sha3_256(b"animica-dev-fake-sphincs-pk|" + sk)
        return (sk, pk)

    raise NotImplementedError(
        "SPHINCS+-SHAKE-128s unavailable. Install python-oqs/liboqs or set ANIMICA_UNSAFE_PQ_FAKE=1 (DEV-ONLY)"
    )


def generate_keypair(seed: Optional[bytes] = None) -> Tuple[bytes, bytes]:
    """Compatibility wrapper returning (pk, sk)."""
    sk, pk = keypair(seed)
    return (pk, sk)


def sign(sk: bytes, msg: bytes) -> bytes:
    """
    Sign a message.

    Security:
      • Real signatures when python-oqs is present.
      • DEV-ONLY fallback returns sha3_512(tag || pk || msg) and is NOT secure.
    """
    if _OQS_OK and _OQS_MECH:
        with oqs.Signature(_OQS_MECH, secret_key=sk) as signer:  # type: ignore[arg-type]
            return signer.sign(msg)

    if _DEV_FAKE_OK:
        # Derive pk from sk to make verify work (matches keypair derivation)
        pk = _sha3_256(b"animica-dev-fake-sphincs-pk|" + sk)
        return _sha3_512(b"animica-dev-fake-sphincs-sig|" + pk + b"|" + msg)

    raise NotImplementedError(
        "SPHINCS+-SHAKE-128s unavailable. Install python-oqs/liboqs or enable ANIMICA_UNSAFE_PQ_FAKE=1 for local dev."
    )


def verify(pk: bytes, msg: bytes, sig: bytes) -> bool:
    """
    Verify a signature. Returns True if valid, False otherwise.
    """
    if _OQS_OK and _OQS_MECH:
        try:
            with oqs.Signature(_OQS_MECH, public_key=pk) as verifier:  # type: ignore[arg-type]
                try:
                    return bool(verifier.verify(msg, sig))
                except Exception:
                    return False
        except TypeError:
            with oqs.Signature(_OQS_MECH) as verifier:  # type: ignore[arg-type]
                try:
                    return bool(verifier.verify(msg, sig, pk))
                except Exception:
                    return False

    if _DEV_FAKE_OK:
        # In fake mode we don't have the real sk; accept signature tied to pk directly
        # (signer used sk, but we reconstruct expected sig from pk for verification)
        expect = _sha3_512(b"animica-dev-fake-sphincs-sig|" + pk + b"|" + msg)
        return sig == expect

    return False


if __name__ == "__main__":
    print("[sphincs_shake_128s] available:", is_available(), "sizes:", sizes)
    try:
        sk, pk = keypair()
        m = b"hello animica (sphincs)"
        s = sign(sk, m)
        print("verify(ok):", verify(pk, m, s))
        print("verify(bad):", verify(pk, m + b"x", s))
    except NotImplementedError as e:
        print(str(e))
